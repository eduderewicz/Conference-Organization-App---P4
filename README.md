App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool

## App project info:
APP-ID: 
link: https://scalable-app-learning-p4.appspot.com/#/
API explorer: https://scalable-app-learning-p4.appspot.com/_ah/api/explorer

## Design choices for speakers/sessions:
I implemented session as a child of Conference. All fields are strings except for date and time. Speaker is a string field, however this design choice would force more logic in the rest of the app or on the end user to deal with and maintain consistency. E.g. speaker name: Chad and chad could be treated as different speakers even though they are one person. One alternative could have been to make the speaker field an instance of the profile class. Ultimately I went with my design selection due to the complexity of this project. 

## Queries
1st query: returns sessions less than 60 minutes. Ideal for people on tight schedules or who need to jump around the conference. This helps them find shorter sessions. 

2nd query: returns sessions with a specified type. This for attendees who want to select sessions based on indicated type. Some attendees may desire only sessions with a type they are interested in 


## Evan's 1st additional query
    """The purpose of this query is to find sessions less than 60 minutes for people who
    have limited time to attend sessions"""
    @endpoints.method(message_types.VoidMessage, SessionForms,
        path='sessionsDuration/lessthan60',
        http_method='GET', name="sessionsDurationLessThan60")
    def sessionsDurationLessThan60(self,request):
        """return sessions less than 60 mins"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization is requied')

        sessions = Session.query(Session.duration <= "60")

        return SessionForms(
            items=[self._copySessionToForm(session) for session in sessions]
        )
## Evan's 2nd additional query
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
        
        
## Query problem - Sessions before 7 and not workshops
The problem is this query requires 2 inequality filters on separate properties. Datastore only allows this on the same property. My implementation of the query first queries based on sessions before 7 pm and then builds a list of a sessions that are not workshops and returns this list. 

## websafeConfernceKey
for API testing purposes: ahpzfnNjYWxhYmxlLWFwcC1sZWFybmluZy1wNHIyCxIHUHJvZmlsZSIVZWR1ZGVyZXdpY3pAZ21haWwuY29tDAsSCkNvbmZlcmVuY2UYAQw
