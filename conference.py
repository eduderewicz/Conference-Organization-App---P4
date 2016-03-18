#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import SessionForm
from models import SessionForms
from models import TypeOfSession
from models import Session

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
MEMCACHE_FEATURED_SPEAKER_KEY = "FEATURED_SPEAKER"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
speaker_sessions = []
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey = messages.StringField(1),
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey = messages.StringField(1),
)

SESS_GET_REQUEST_BY_TYPE = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey = messages.StringField(1),
    typeOfSession = messages.StringField(2),
)

SESS_GET_REQUEST_BY_SPEAKER = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker = messages.StringField(1),
)

WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey = messages.StringField(1),
)
WISHLIST_DELETE_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey = messages.StringField(1),
)

SPECIFIED_DURATION = endpoints.ResourceContainer(
    message_types.VoidMessage,
    duration = messages.IntegerField(1, variant=messages.Variant.INT32),
)
"""WISHLIST_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey = messages.StringField(1),
)"""
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )
# - - - Session objects - - - - - 

    def _createSessionObject(self,request):
        """Create or update Session object, returning SessionForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field is required")

        conf = ndb.Key(urlsafe = request.websafeConferenceKey).get()

        if not conf:
            raise endpoints.NotFoundException("no conference found")

        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException("Only the owner can update conference.")

        data = {field.name: getattr(request,field.name) for field in request.all_fields()}

        wsck = data['websafeConferenceKey']

        #generate session key based on Conf
        c_key = ndb.Key(Conference, request.websafeConferenceKey)
        s_id = Session.allocate_ids(size=1,parent = c_key)[0]
        s_key = ndb.Key(Session,s_id, parent = c_key)

        
        print data['duration']

        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'], '%H:%M').time()
        if data['date']:
            data['date'] = datetime.strptime(data['date'], '%Y-%m-%d').date()

        if data['typeOfSession'] == None:
            data['typeOfSession'] = str(TypeOfSession.NOT_SPECIFIED)
        
        speaker = data['speaker']
        
        sessions = Session.query(Session.speaker == speaker).fetch()

        if len(sessions) > 1:
            taskqueue.add(
                params={'websafeConferenceKey': request.websafeConferenceKey,
                        'speaker': speaker},
                url='/tasks/set_featured_speaker')

        data['websafeConferenceKey'] = request.websafeConferenceKey

        #speaker_name = data['speaker']
        
        Session(**data).put()
        return request

    @staticmethod
    def _cacheFeaturedSpeaker(websafeConferenceKey, speaker):
        """Assign featured speaker to memcache"""

        sessions = Session.query(Session.websafeConferenceKey == websafeConferenceKey)
        sessions = Session.query(Session.speaker == speaker).fetch()
        speaker_sessions.append(speaker)
        for session in sessions:
            speaker_sessions.append(session.name)
        
        #http://www.decalage.info/en/python/print_list 
        memcache.set(MEMCACHE_FEATURED_SPEAKER_KEY, ', '.join(map(str, speaker_sessions)))





    @endpoints.method(message_types.VoidMessage, StringMessage,
        path='session/featured/get',http_method='GET',
        name = 'getFeaturedSpeaker')
    def getFeaturedSpeaker(self,request):
        """Return Featured Speaker from memcache"""
        featured = memcache.get(MEMCACHE_FEATURED_SPEAKER_KEY)

        if not featured:
            featured = ""
        return StringMessage(data=featured)


    @endpoints.method(SessionForm, SessionForm, path="session", 
            http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._createSessionObject(request)

    @endpoints.method(SESS_GET_REQUEST, SessionForms,
            path='getConferenceSessions/{websafeConferenceKey}',
            http_method ='GET', name ='getConferenceSessions')
    def getConferenceSessions(self,request):
        """return requested sessions for websafeConferenceKey"""
        #check user auth
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization Required')
        user_id = getUserId(user)

        sessions = Session.query(Session.websafeConferenceKey == request.websafeConferenceKey)
        

        return SessionForms(
            items = [self._copySessionToForm(session) for session in sessions]
            )

    def _copySessionToForm(self,sess):
        """Copy fields from Session to SessionForm"""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(sess, field.name):
                #convert all to string, except duration
                if field.name != "duration":
                    setattr(sf,field.name,str(getattr(sess, field.name)))
            elif field.name == "websafeSessionKey":
                setattr(sf, field.name, sess.key.urlsafe())
        sf.check_initialized()
        return sf 

    @endpoints.method(SESS_GET_REQUEST_BY_TYPE, SessionForms,
            path='getConferenceSessionsByType/{websafeConferenceKey}/{typeOfSession}',
            http_method = 'GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """return sessions by session type"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization is requied')

        user_id = getUserId(user)

        sessions = Session.query(Session.websafeConferenceKey == request.websafeConferenceKey,
            Session.typeOfSession == request.typeOfSession)

        return SessionForms(
            items = [self._copySessionToForm(session) for session in sessions]
        )

    @endpoints.method(SESS_GET_REQUEST_BY_SPEAKER, SessionForms, 
            path='getConferenceSessionsBySpeaker/{speaker}',
            http_method='GET', name='getConferenceSessionsBySpeaker')
    def getConferenceSessionsBySpeaker(self,request):
        """return sessions by speaker."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization is requied')

        user_id = getUserId(user)

        sessions = Session.query(Session.speaker == request.speaker)

        return SessionForms(
            items = [self._copySessionToForm(session) for session in sessions])


    @endpoints.method(WISHLIST_POST_REQUEST, StringMessage, 
        path='profile/addToWishlist', 
        http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self,request):
        """create/update wishlist, returning wishlist/request"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization is requied')


        userProfile = self._getProfileFromUser()
        
        currentWishlist = userProfile.sessionsWishlist
        
        sessionToAdd = request.websafeSessionKey
        print sessionToAdd

        #evaluate if session is already in wishlist, if not add it
        if sessionToAdd in currentWishlist:
            msg = StringMessage(data="Session is already in your wishlist")
        else:
            userProfile.sessionsWishlist.append(sessionToAdd)
            msg = StringMessage(data="The wishlist has been updated with the desired session")

        userProfile.put()

        return msg

    @endpoints.method(message_types.VoidMessage, SessionForms, 
        path="profile/getWishlist", http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):

        """return wishlist"""
        #load currrent user
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization is requied')
        userProfile = self._getProfileFromUser()
       
        #get users wishlist
        sessions = userProfile.sessionsWishlist


        return SessionForms(
            items=[self._copySessionToForm(ndb.Key(urlsafe=session).get()) for session in sessions]
        
        )

    @endpoints.method(WISHLIST_DELETE_REQUEST,StringMessage,
        path='profile/deleteFromWishlist',
        http_method = 'DELETE', name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self,request):
        """remove a session from users' wishlist."""
        #load currrent user
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization is requied')
        userProfile = self._getProfileFromUser()

        currentWishlist = userProfile.sessionsWishlist

        sessionToRemove = request.websafeSessionKey

        message = StringMessage(data="session does not exist")
        #if session is in wish list, remove it
        if sessionToRemove in userProfile.sessionsWishlist:
            userProfile.sessionsWishlist.remove(sessionToRemove)
            userProfile.put()
            message = StringMessage(data="Session removed")
                     
        return message

    #Evan's 1st additional query
    """The purpose of this query is to find sessions less than 60 minutes for people who
    have limited time to attend sessions"""
    @endpoints.method(SPECIFIED_DURATION, SessionForms,
        path='sessionsDuration/lessthanspecified',
        http_method='GET', name="sessionsDurationLessThanSpecified")
    def sessionsDurationLessThanSpecified(self,request):
        """return sessions less than mins specified by user"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization is requied')

        
        print request.duration
        sessions = Session.query()

        #filter unspecified durations
        sessions = sessions.filter(Session.duration != None)
        sessions = sessions.filter(Session.duration < request.duration)
        
        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )

    #Evan's 2nd additional query
    """The purpose of this query is to return sessions with specified types"""
    @endpoints.method(message_types.VoidMessage, SessionForms,
        path='sessionstypes/specified',
        http_method='GET', name="sessionsWithType")
    def sessionsWithType(self,request):
        """Return Sessions with specified Types"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization is requied')

        sessions = Session.query(Session.typeOfSession != 'NOT_SPECIFIED')

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        ) 

    @endpoints.method(message_types.VoidMessage, SessionForms, path='sessions/after7/noworkshop',
            http_method='GET', name='getSessionsAfter7NoWorkshop')
    def getSessionsAfter7NoWorkshop(self,request):
        """Return sessions after 7 pm that are not workshops"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization is requied')

        sevenPM = '19:00:00'
        timeToFilter = datetime.strptime(sevenPM, '%H:%M:%S').time()

        sessions = Session.query(Session.startTime < timeToFilter)

        #create a list to store properties to complete 2nd part of inequality filter
        filterSessions = []

        #loop over items that are not workshops
        for session in sessions:
            if session.typeOfSession != "WORKSHOP":
                filterSessions.append(session)

        return SessionForms(
            items=[self._copySessionToForm(session) for session in filterSessions]
        )

    #credit for this goes to addSessionToWishlist parameter clarificaiton forum question, 
    #user: krishna_303760150814 
    @endpoints.method(message_types.VoidMessage, message_types.VoidMessage,
            path='conference/session/create/updatewebsafeSessionKey',
            http_method = 'POST', name='updateWebsafeSessionKey')
    def updateWebsafeSessionKey(self,request):
        """used to update websafeSessionKey for all sessions"""
        sessions = Session.query()
        for session in sessions:
            wssk = session.key.urlsafe()
            session.websafeSessionKey = wssk
            session.put()

        
        return message_types.VoidMessage()

    #api helper function to update duration types 
    @endpoints.method(message_types.VoidMessage, message_types.VoidMessage,
            path='conference/session/create/updatesessionduration',
            http_method = 'POST', name='updateSessionDuration')
    def updateSessionDuration(self,request):
        """used to update session duration for all sessions"""
        sessions = Session.query()
        for session in sessions:
            if session.duration == (None or '0'):
                session.duration = 0
            else: 
                session.duration = int(session.duration)
            session.put()

        
        return message_types.VoidMessage()


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='filterPlayground',
            http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )


api = endpoints.api_server([ConferenceApi]) # register API
