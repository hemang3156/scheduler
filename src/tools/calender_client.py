import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
from google.auth.exceptions import RefreshError 
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from src.core.exceptions import CalendarAuthError, CalendarError, CalendarResourceNotFoundError
from src.core.models import CalendarEvent

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CREDENTIALS = PROJECT_ROOT / "credentials.json"
DEFAULT_TOKEN = PROJECT_ROOT / "token.json"


SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TIMEZONE = "Asia/Kolkata"

class CalendarClient:
    def __init__(self, credentials_path=DEFAULT_CREDENTIALS, token_path=DEFAULT_TOKEN,calendar_id: str = "primary"):
        
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.calendar_id = calendar_id
        self.tz = ZoneInfo(TIMEZONE)
        self.service = self._authenticate() 


    def _authenticate(self):
        creds = None
        if self.token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
            except Exception:
                creds = None
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError:
                    if self.token_path.exists():
                        self.token_path.unlink()
                    creds = None
            if not creds:
                if not self.credentials_path.exists():
                    raise CalendarAuthError(f"Credentials file not found at {self.credentials_path}")
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)

                with open(self.token_path, "w") as token_file:
                    token_file.write(creds.to_json())
            
        return build("calendar", "v3", credentials=creds)
    

    def list_events(self,start_time: dt.datetime, end_time: dt.datetime):
        try:
            events_result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=start_time.isoformat(),
                timeMax=end_time.isoformat(),
                singleEvents=True,
                orderBy="startTime"
            ).execute()
        except HttpError as error:
            raise CalendarError(f"An error occurred while listing events: {error}")
        events: list[CalendarEvent] = []
        for item in events_result.get("items", []):
            start_raw = item["start"]
            end_raw = item["end"]

            if "dateTime" in start_raw:
                start_dt = dt.datetime.fromisoformat(start_raw["dateTime"])
                end_dt = dt.datetime.fromisoformat(end_raw["dateTime"])
                is_all_day = False
            else:
                start_dt = dt.datetime.strptime(start_raw["date"], "%Y-%m-%d").replace(tzinfo=self.tz)
                end_dt = dt.datetime.strptime(end_raw["date"], "%Y-%m-%d").replace(tzinfo=self.tz)
                is_all_day = True

            events.append(
                CalendarEvent(
                    id=item["id"],
                    summary=item.get("summary", "(No title)"),
                    start=start_dt,
                    end=end_dt,
                    is_all_day=is_all_day,
                )
            )
        return events

    def create_event(self, event: CalendarEvent):
        event_body = {
            "summary": event.summary,
            "start": {"dateTime": event.start.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": event.end.isoformat(), "timeZone": TIMEZONE},
        }
        try:
            created_event = self.service.events().insert(calendarId=self.calendar_id, body=event_body).execute()
            return CalendarEvent(
                id=created_event["id"],
                summary=created_event.get("summary", "(No title)"),
                start=dt.datetime.fromisoformat(created_event["start"]["dateTime"]),
                end=dt.datetime.fromisoformat(created_event["end"]["dateTime"]),
                is_all_day=False,
            )
        except HttpError as error:
            raise CalendarError(f"An error occurred while creating the event: {error}")
    
    def patch_event(self, event_id: str, new_start: dt.datetime, new_end: dt.datetime):
        patch_body = {
            "start": {"dateTime": new_start.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": new_end.isoformat(), "timeZone": TIMEZONE},
        }
        try:
            self.service.events().patch(calendarId=self.calendar_id, eventId=event_id, body=patch_body).execute()
        except HttpError as error:
            if error.resp.status == 404:
                raise CalendarResourceNotFoundError(f"Event with ID {event_id} not found.") from error
            else:
                raise CalendarError(f"Failed to update event {event_id}: {error}") from error

    def delete_event(self, event_id: str):
        try:
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as error:
            if error.resp.status == 404:
                raise CalendarResourceNotFoundError(f"Event with ID {event_id} not found.") from error
            else:
                raise CalendarError(f"Failed to delete event {event_id}: {error}") from error

    def delete_events(self, event_ids: list[str]) -> list[str]:
        """Deletes multiple events by their IDs."""
        deleted: list[str] = []
        for event_id in event_ids:
            self.delete_event(event_id)
            deleted.append(event_id)
        return deleted



