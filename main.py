import os
import json
import datetime as dt
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# ============================================================
# WRITE GOOGLE CREDS FROM ENV (REQUIRED ON RENDER)
# ============================================================
if not os.path.exists("client_secret.json"):
    cs = os.getenv("ALMA_CLIENT_SECRET_JSON")
    if cs:
        with open("client_secret.json", "w") as f:
            f.write(cs)

if not os.path.exists("token.json"):
    token_env = os.getenv("ALMA_TOKEN_JSON")
    if token_env:
        with open("token.json", "w") as f:
            f.write(token_env)


# ============================================================
# CONFIG
# ============================================================
SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = "token.json"
CLIENT_SECRET = "client_secret.json"

USER_EMAIL = "bryanchagas@gmail.com"
ALMA_CALENDAR_NAME = "Alma — Ritmo Comportamental"


# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # permitir chamadas do ChatGPT
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# AUTH HELPERS
# ============================================================
def get_credentials():
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        return creds

    # Local OAuth login (apenas quando rodando no seu Mac)
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as token:
        token.write(creds.to_json())

    return creds


def get_calendar_service():
    creds = get_credentials()
    return build("calendar", "v3", credentials=creds)


# ============================================================
# CALENDAR HELPERS
# ============================================================
def get_or_create_alma_calendar(service):
    calendars = service.calendarList().list().execute().get("items", [])
    for cal in calendars:
        if cal["summary"] == ALMA_CALENDAR_NAME:
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
