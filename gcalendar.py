from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# Google Calendar's eleven event colours. The API only ever returns/accepts the
# numeric colorId; these are the names shown in the Calendar UI, included so a
# caller can say "Sage" instead of "2". An event with no colorId inherits the
# calendar's own colour, reported here as "" / "Default".
EVENT_COLOR_NAMES: Dict[str, str] = {
    "1": "Lavender",
    "2": "Sage",
    "3": "Grape",
    "4": "Flamingo",
    "5": "Banana",
    "6": "Tangerine",
    "7": "Peacock",
    "8": "Graphite",
    "9": "Blueberry",
    "10": "Basil",
    "11": "Tomato",
}


class CalendarService:
    def __init__(self, credentials: Credentials, account_name: str = ""):
        self.service = build("calendar", "v3", credentials=credentials)
        self.account_name = account_name

    # ------------------------------------------------------------------ calendars

    def list_calendars(self) -> List[Dict[str, Any]]:
        result = self.service.calendarList().list().execute()
        return [
            {
                "id": cal["id"],
                "summary": cal.get("summary", ""),
                "description": cal.get("description", ""),
                "primary": cal.get("primary", False),
                "accessRole": cal.get("accessRole", ""),
                "backgroundColor": cal.get("backgroundColor", ""),
            }
            for cal in result.get("items", [])
        ]

    # ------------------------------------------------------------------ events

    def list_events(
        self,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 20,
        calendar_id: str = "primary",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        params: Dict[str, Any] = {
            "calendarId": calendar_id,
            "maxResults": min(max_results, 50),
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": time_min or now,
        }
        if time_max:
            params["timeMax"] = time_max

        result = self.service.events().list(**params).execute()
        events = [self._parse_event(e) for e in result.get("items", [])]
        return {
            "calendar_id": calendar_id,
            "count": len(events),
            "events": events,
            "nextPageToken": result.get("nextPageToken"),
        }

    def search_events(
        self,
        query: str,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 20,
        calendar_id: str = "primary",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        params: Dict[str, Any] = {
            "calendarId": calendar_id,
            "q": query,
            "maxResults": min(max_results, 50),
            "singleEvents": True,
            "orderBy": "startTime",
            "timeMin": time_min or now,
        }
        if time_max:
            params["timeMax"] = time_max

        result = self.service.events().list(**params).execute()
        events = [self._parse_event(e) for e in result.get("items", [])]
        return {
            "query": query,
            "count": len(events),
            "events": events,
        }

    def get_event(self, event_id: str, calendar_id: str = "primary") -> Dict[str, Any]:
        event = self.service.events().get(
            calendarId=calendar_id, eventId=event_id
        ).execute()
        return self._parse_event(event)

    # ------------------------------------------------------------------ internals

    def _parse_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        start = event.get("start", {})
        end = event.get("end", {})

        attendees = [
            {
                "email": a.get("email", ""),
                "name": a.get("displayName", ""),
                "response": a.get("responseStatus", ""),
                "self": a.get("self", False),
            }
            for a in event.get("attendees", [])
        ]

        return {
            "id": event.get("id", ""),
            "summary": event.get("summary", "(sin título)"),
            "description": event.get("description", ""),
            "location": event.get("location", ""),
            "start": start.get("dateTime", start.get("date", "")),
            "end": end.get("dateTime", end.get("date", "")),
            "allDay": "date" in start and "dateTime" not in start,
            "status": event.get("status", ""),
            "organizer": event.get("organizer", {}).get("email", ""),
            "attendees": attendees,
            "attendeeCount": len(attendees),
            "meetLink": event.get("hangoutLink", ""),
            "htmlLink": event.get("htmlLink", ""),
            "recurrence": bool(event.get("recurrence") or event.get("recurringEventId")),
            "colorId": event.get("colorId", ""),
            "colorName": EVENT_COLOR_NAMES.get(event.get("colorId", ""), "Default"),
        }

    # ------------------------------------------------------------------ colours

    def list_event_colors(self) -> Dict[str, Any]:
        """The palette this calendar account can apply to an event."""
        palette = self.service.colors().get().execute()
        return {
            "colors": [
                {
                    "colorId": color_id,
                    "name": EVENT_COLOR_NAMES.get(color_id, ""),
                    "background": value.get("background", ""),
                    "foreground": value.get("foreground", ""),
                }
                for color_id, value in sorted(
                    palette.get("event", {}).items(), key=lambda kv: int(kv[0])
                )
            ],
            "note": (
                "An event with no colorId inherits its calendar's colour. "
                "Pass color_id='' to calendar_set_event_color to revert to that."
            ),
        }

    def set_event_color(
        self,
        event_id: str,
        color_id: str,
        calendar_id: str = "primary",
    ) -> Dict[str, Any]:
        """Set one event's colour. Patch update: nothing else on the event changes.

        `color_id` must be "1".."11", or "" to clear the override and fall back
        to the calendar's own colour.
        """
        color_id = (color_id or "").strip()
        if color_id and color_id not in EVENT_COLOR_NAMES:
            raise ValueError(
                f"Unknown color_id {color_id!r}. Valid ids are 1-11 "
                f"({', '.join(f'{k}={v}' for k, v in EVENT_COLOR_NAMES.items())}), "
                "or '' to revert to the calendar's colour."
            )

        updated = self.service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body={"colorId": color_id or None},
            sendUpdates="none",
        ).execute()

        return {
            "status": "updated",
            "event": self._parse_event(updated),
        }
