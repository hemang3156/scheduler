import datetime as dt
import os
import os.path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CALENDAR_ID_EXPORT ="3989d0a8d8e557a63765a2bc13cc023ad8561fbc974aafe3372650f879a13cd0@group.calendar.google.com"
CALENDAR_ID_IMPORT ="hemug67@gmail.com"
TIMEZONE = "Asia/Kolkata"
script_dir = os.path.dirname(os.path.abspath(__file__))

def get_calendar_service():
    
    creds = None
    
    if os.path.exists(os.path.join(script_dir, "token.json")):
        creds = Credentials.from_authorized_user_file(os.path.join(script_dir, "token.json"), SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                os.path.join(script_dir, "credentials.json"),
                SCOPES,
            )
        
        creds = flow.run_local_server(port=0)
    
    with open(os.path.join(script_dir, "token.json"), "w") as token:
            token.write(creds.to_json())
    
    return build("calendar", "v3", credentials=creds)

def list_events_today(service):
    tz = ZoneInfo(TIMEZONE)
    now = dt.datetime.now(tz)
    
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = now.replace(hour=23, minute=59, second=59)
    
    events_result = (
        
        service.events().list(
            calendarId=CALENDAR_ID_IMPORT,
            timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    )
    events = events_result.get("items", [])
    
    print(f"\n {len(events)} events found for {now.date()}:\n")
    
    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        summary = event.get("summary", "No Title")
        print(f"{start} - {summary}")
    return events

def create_event(service):
    tz = ZoneInfo(TIMEZONE)
    now = dt.datetime.now(tz)
    
    start = now + dt.timedelta(minutes=10)
    end = start + dt.timedelta(hours=1)
    
    event_body = {
        "summary": "Sample Event",
        "description": "This is a sample event created using the Google Calendar API.",
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": TIMEZONE,
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": TIMEZONE,
        },
    }
    
    event= (
        service.events().insert(calendarId=CALENDAR_ID_EXPORT, body=event_body).execute()
    )
    
    print("\nCreated dummy event:")
    print(event.get("htmlLink"))
    

