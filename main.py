import os
import json
import datetime as dt
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials


# ============================================================
# CONFIG
# ============================================================
SCOPES = ["https://www.googleapis.com/auth/calendar"]
USER_EMAIL = "bryanchagas@gmail.com"
ALMA_CALENDAR_NAME = "Alma — Ritmo Comportamental"


# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # permitir chamadas externas
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# SERVE OPENAI ACTIONS MANIFEST
# ============================================================
@app.get("/.well-known/openai.yaml", response_class=PlainTextResponse)
def serve_openai_manifest():
    """Serves the OpenAI Actions manifest file located at the project root."""
    try:
        with open("openai.yaml", "r") as f:
            content = f.read()
        return PlainTextResponse(content, media_type="text/yaml")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# SERVE OPENAPI SPECIFICATION
# ============================================================
@app.get("/openapi.yaml", response_class=PlainTextResponse)
def serve_openapi_spec():
    """Serves the OpenAPI specification used by ChatGPT Actions."""
    try:
        with open("openapi.yaml", "r") as f:
            return PlainTextResponse(f.read(), media_type="application/yaml")
    except Exception:
        raise HTTPException(status_code=500, detail="Cannot load OpenAPI spec file.")


# ============================================================
# AUTH WITH SERVICE ACCOUNT
# ============================================================
def get_calendar_service():
    """
    Loads Google Calendar credentials from the service account JSON
    stored in the environment variable GOOGLE_SERVICE_ACCOUNT_JSON.
    """
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not service_account_json:
        raise Exception("Missing GOOGLE_SERVICE_ACCOUNT_JSON environment variable")

    info = json.loads(service_account_json)

    creds = Credentials.from_service_account_info(
        info,
        scopes=SCOPES
    )

    service = build("calendar", "v3", credentials=creds)
    return service


# ============================================================
# CALENDAR HELPERS
# ============================================================
def get_or_create_alma_calendar(service):
    calendars = service.calendarList().list().execute().get("items", [])
    for cal in calendars:
        if cal.get("summary") == ALMA_CALENDAR_NAME:
            return cal["id"]

    new_calendar = {
        "summary": ALMA_CALENDAR_NAME,
        "timeZone": "America/Sao_Paulo",
    }
    created = service.calendars().insert(body=new_calendar).execute()
    return created["id"]


# ============================================================
# REQUEST MODELS
# ============================================================
class EventRequest(BaseModel):
    title: str
    start: str
    end: str
    description: str = ""


class DeleteRequest(BaseModel):
    event_id: str
    calendar_id: str


# ============================================================
# ROUTES
# ============================================================

@app.get("/calendar/upcoming")
def get_upcoming_events():
    service = get_calendar_service()
    now = dt.datetime.utcnow().isoformat() + "Z"

    events = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now,
            maxResults=10,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
        .get("items", [])
    )

    return {"events": events}


@app.get("/calendar/free-slots")
def get_free_slots():
    service = get_calendar_service()
    now = dt.datetime.utcnow()
    end_of_day = now.replace(hour=23, minute=59)

    events = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat() + "Z",
            timeMax=end_of_day.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
        .get("items", [])
    )

    free_blocks = []
    cursor = now

    for event in events:
        start = dt.datetime.fromisoformat(event["start"]["dateTime"])
        if start > cursor:
            free_blocks.append({"from": cursor.isoformat(), "to": start.isoformat()})
        cursor = dt.datetime.fromisoformat(event["end"]["dateTime"])

    if cursor < end_of_day:
        free_blocks.append({"from": cursor.isoformat(), "to": end_of_day.isoformat()})

    return {"free_slots": free_blocks}


@app.post("/calendar/add-event")
def add_event(event: EventRequest):
    service = get_calendar_service()
    alma_calendar_id = get_or_create_alma_calendar(service)

    event_body = {
        "summary": event.title,
        "description": event.description,
        "start": {"dateTime": event.start, "timeZone": "America/Sao_Paulo"},
        "end": {"dateTime": event.end, "timeZone": "America/Sao_Paulo"},
    }

    created = service.events().insert(calendarId=alma_calendar_id, body=event_body).execute()
    return {"status": "created", "event": created}


@app.post("/calendar/delete-event")
def delete_event(req: DeleteRequest):
    service = get_calendar_service()

    try:
        service.events().delete(calendarId=req.calendar_id, eventId=req.event_id).execute()
        return {"status": "deleted"}
    except Exception:
        raise HTTPException(status_code=404, detail="Event not found")
