import os
import json
import datetime as dt
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# -----------------------------------------------------------
# CONFIG
# -----------------------------------------------------------
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Render: credenciais vêm do ambiente
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://alma-server.onrender.com/oauth2/callback")

TOKEN_FILE = "token.json"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------
# OAUTH FLOW (RENDER)
# -----------------------------------------------------------
def build_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uris": [REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
    )


def save_credentials(creds):
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())


def load_credentials():
    if not os.path.exists(TOKEN_FILE):
        return None
    return Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)


# -----------------------------------------------------------
# ROUTES
# -----------------------------------------------------------
@app.get("/")
def home():
    return {"status": "ok", "message": "Alma Server online"}


@app.get("/auth")
def auth_step1():
    flow = build_flow()
    flow.redirect_uri = REDIRECT_URI
    auth_url, _ = flow.authorization_url(prompt="consent")
    return {"auth_url": auth_url}


@app.get("/oauth2/callback")
def auth_callback(code: str):
    flow = build_flow()
    flow.redirect_uri = REDIRECT_URI
    flow.fetch_token(code=code)

    creds = flow.credentials
    save_credentials(creds)

    return {"status": "authenticated", "message": "Alma calendar access granted."}


# -----------------------------------------------------------
# SERVICE HELPERS
# -----------------------------------------------------------
def get_service():
    creds = load_credentials()
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated with Google Calendar")

    return build("calendar", "v3", credentials=creds)


ALMA_CALENDAR_NAME = "Alma — Ritmo Comportamental"


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


# -----------------------------------------------------------
# MODELS
# -----------------------------------------------------------
class EventRequest(BaseModel):
    title: str
    start: str
    end: str
    description: str = ""


class DeleteRequest(BaseModel):
    event_id: str
    calendar_id: str


# -----------------------------------------------------------
# ENDPOINTS
# -----------------------------------------------------------
@app.get("/calendar/upcoming")
def get_upcoming():
    service = get_service()
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
def free_slots():
    service = get_service()
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

    free = []
    cursor = now

    for event in events:
        start = dt.datetime.fromisoformat(event["start"]["dateTime"])
        if start > cursor:
            free.append({"from": cursor.isoformat(), "to": start.isoformat()})
        cursor = dt.datetime.fromisoformat(event["end"]["dateTime"])

    if cursor < end_of_day:
        free.append({"from": cursor.isoformat(), "to": end_of_day.isoformat()})

    return {"free_slots": free}


@app.post("/calendar/add-event")
def add_event(req: EventRequest):
    service = get_service()
    alma_cal_id = get_or_create_alma_calendar(service)

    body = {
        "summary": req.title,
        "description": req.description,
        "start": {"dateTime": req.start, "timeZone": "America/Sao_Paulo"},
        "end": {"dateTime": req.end, "timeZone": "America/Sao_Paulo"},
    }

    created = service.events().insert(calendarId=alma_cal_id, body=body).execute()
    return {"status": "created", "event": created}


@app.post("/calendar/delete-event")
def delete_event(req: DeleteRequest):
    service = get_service()
    try:
        service.events().delete(calendarId=req.calendar_id, eventId=req.event_id).execute()
        return {"status": "deleted"}
    except Exception:
        raise HTTPException(status_code=404, detail="Event not found")
