import os
import json
import datetime
import subprocess
import sys
import time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from supabase import create_client, Client
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TZ = ZoneInfo("America/Los_Angeles")  # the user's time zone; a server's own clock is often UTC
CANVAS_TODO_HOME = os.environ.get("CANVAS_TODO_HOME", os.path.expanduser("~/canvas-todo-sync"))
supabase: Client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

def get_calendar_service():
    """Authenticates and returns the Google Calendar API service."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
        except RefreshError:  # Google revoked the sign-in (every 7 days while the app is in Testing)
            creds = None
        if not creds or not creds.valid:
            if sys.stdin is None or not sys.stdin.isatty():
                # Nobody can finish a browser sign-in on a server service, so say what to do instead of waiting
                raise RuntimeError("Google sign-in expired. Sign in on your laptop (python assistant.py), "
                                   "then copy token.json to the server and restart the bot.")
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            # Give up after 2 minutes so an unanswered sign-in doesn't freeze the bot
            creds = flow.run_local_server(port=0, timeout_seconds=120)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def get_calibrated_estimate(category_id: str, user_est_mins: int) -> dict:
    """Looks up the user's historical multiplier for a task category and suggests a realistic duration."""
    res = supabase.table("category_profiles").select("*").eq("category_id", category_id).execute()
    if not res.data:
        return {"error": f"Unknown category {category_id}"}
    
    profile = res.data[0]
    mult = float(profile["duration_multiplier"])
    suggested = max(5, int(round((user_est_mins * mult) / 5.0) * 5))  # Nearest 5 mins, never 0
    return {
        "category_id": category_id,
        "user_est_mins": user_est_mins,
        "historical_multiplier": round(mult, 2),
        "suggested_mins": suggested,
        "max_session_mins": profile["max_session_mins"],
        "preferred_window": profile["preferred_window"]
    }


def check_calendar_availability(days_ahead: int = 7) -> list:
    """Fetches upcoming events from Google Calendar so the assistant can see fixed commitments and open gaps."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return list_events(now, now + datetime.timedelta(days=days_ahead))


def list_events(time_min: datetime.datetime, time_max: datetime.datetime) -> list:
    """Every Google Calendar event between two times, in the shape the assistant sees."""
    service = get_calendar_service()
    now, end = time_min.isoformat(), time_max.isoformat()
    
    items, page_token = [], None
    while True:  # Google returns at most one page (250 events by default) per call
        page = service.events().list(
            calendarId="primary", timeMin=now, timeMax=end,
            singleEvents=True, orderBy="startTime", pageToken=page_token
        ).execute()
        items += page.get("items", [])
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    
    formatted = []
    for e in items:
        props = e.get("extendedProperties", {}).get("private", {})
        formatted.append({
            "event_id": e["id"],
            "summary": e.get("summary", "Busy"),
            "start": e["start"].get("dateTime", e["start"].get("date")),
            "end": e["end"].get("dateTime", e["end"].get("date")),
            "flexibility": props.get("flexibility", "fixed"),
            "task_db_id": props.get("task_db_id", None)
        })
    return formatted


def schedule_calibrated_task(
    title: str, category_id: str, user_est_mins: int, ai_suggested_mins: int,
    start_iso: str, deadline_iso: str
) -> dict:
    """Logs a task in Supabase and creates a flexible block on Google Calendar with hidden metadata."""
    service = get_calendar_service()
    start_dt = datetime.datetime.fromisoformat(start_iso)
    end_dt = start_dt + datetime.timedelta(minutes=ai_suggested_mins)
    
    # 1. Insert into Supabase first
    db_res = supabase.table("tasks_log").insert({
        "title": title,
        "category_id": category_id,
        "user_est_mins": user_est_mins,
        "ai_suggested_mins": ai_suggested_mins,
        "deadline": deadline_iso,
        "status": "scheduled"
    }).execute()
    task_id = db_res.data[0]["id"]
    
    # 2. Create the Google Calendar event with extendedProperties
    event_body = {
        "summary": f"[Flex] {title}",
        "description": f"User Est: {user_est_mins}m | AI Calibrated: {ai_suggested_mins}m",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TZ.key},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": TZ.key},
        "extendedProperties": {
            "private": {
                "task_db_id": str(task_id),
                "category_id": category_id,
                "flexibility": "movable"
            }
        }
    }
    try:
        created_event = service.events().insert(calendarId="primary", body=event_body).execute()
    except Exception:
        # Don't leave a task row with no calendar block behind (Gemini usually retries)
        supabase.table("tasks_log").delete().eq("id", task_id).execute()
        raise
    
    # 3. Link the Google Calendar event ID back to Supabase
    supabase.table("tasks_log").update({"gcal_event_id": created_event["id"]}).eq("id", task_id).execute()
    return {"status": "scheduled", "task_id": task_id, "gcal_event_id": created_event["id"], "scheduled_start": start_iso}


def log_task_progress_and_learn(task_id: str, minutes_worked_today: int, total_percent_complete: int) -> dict:
    """
    Updates task progress. If 100% complete, updates the category's EMA multiplier.
    If partially complete, calculates remaining time needed so the assistant can reschedule the rest.
    """
    task_res = supabase.table("tasks_log").select("*").eq("id", task_id).execute()
    if not task_res.data:
        return {"error": "Task not found"}
    
    task = task_res.data[0]
    if task["status"] == "completed":
        return {"error": "Task already completed"}
    new_actual_mins = task["actual_mins_spent"] + minutes_worked_today
    
    if total_percent_complete >= 100:
        # Update task as completed
        supabase.table("tasks_log").update({
            "actual_mins_spent": new_actual_mins,
            "percent_complete": 100,
            "status": "completed"
        }).eq("id", task_id).execute()
        
        # Update the EMA multiplier: alpha_new = 0.3 * (actual / user_est) + 0.7 * alpha_old
        cat_id = task["category_id"]
        cat_res = supabase.table("category_profiles").select("*").eq("category_id", cat_id).execute()
        if cat_res.data:
            old_mult = float(cat_res.data[0]["duration_multiplier"])
            samples = int(cat_res.data[0]["sample_count"])
            # Floor both sides at 15 min so tiny tasks neither drag the multiplier down nor blow it up
            observed_ratio = max(new_actual_mins, 15) / max(task["user_est_mins"], 15)
            new_mult = round((0.3 * observed_ratio) + (0.7 * old_mult), 3)
            
            supabase.table("category_profiles").update({
                "duration_multiplier": new_mult,
                "sample_count": samples + 1
            }).eq("category_id", cat_id).execute()
            return {"status": "completed", "total_mins_spent": new_actual_mins, "new_category_multiplier": new_mult}
            
    # If unfinished, estimate remaining minutes linearly from actual pace
    effective_pct = max(total_percent_complete, 10)
    projected_total_mins = int((new_actual_mins / (effective_pct / 100.0)))
    remaining_mins = max(projected_total_mins - new_actual_mins, 15)
    
    supabase.table("tasks_log").update({
        "actual_mins_spent": new_actual_mins,
        "percent_complete": total_percent_complete,
        "reschedule_count": task["reschedule_count"] + 1,
        "status": "in_progress"
    }).eq("id", task_id).execute()
    
    return {
        "status": "partially_complete",
        "mins_spent_so_far": new_actual_mins,
        "percent_complete": total_percent_complete,
        "estimated_remaining_mins": remaining_mins
    }


def create_calendar_event(title: str, start_iso: str, end_iso: str, recurrence: str = "", movable: bool = False) -> dict:
    """Adds any event to the user's Google Calendar: meetings, classes, lab hours, appointments, one-time or repeating.
    `recurrence` is an iCalendar RRULE for repeating events, for example "RRULE:FREQ=WEEKLY;BYDAY=TU" or
    "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261211T235959Z"; leave it empty for a one-time event. `start_iso` and
    `end_iso` are the first occurrence. Set `movable` only for personal blocks Pacer may reschedule later; meetings,
    classes and anything involving other people stay fixed."""
    if recurrence and not (recurrence.startswith("RRULE:") and "FREQ=" in recurrence):
        return {"error": 'recurrence must be an RRULE like "RRULE:FREQ=WEEKLY;BYDAY=TU"'}
    start, end = datetime.datetime.fromisoformat(start_iso), datetime.datetime.fromisoformat(end_iso)
    if end <= start:
        return {"error": "end must be after start"}
    body = {
        "summary": f"[Flex] {title}" if movable else title,
        # timeZone is required for repeats and keeps them at the same local time across daylight saving changes
        "start": {"dateTime": start.isoformat(), "timeZone": TZ.key},
        "end": {"dateTime": end.isoformat(), "timeZone": TZ.key},
        "extendedProperties": {"private": {"flexibility": "movable" if movable else "fixed"}},
    }
    if recurrence:
        body["recurrence"] = [recurrence]
    event = get_calendar_service().events().insert(calendarId="primary", body=body).execute()
    return {"status": "created", "event_id": event["id"], "title": body["summary"], "repeats": recurrence or "no"}


def move_calendar_event(gcal_event_id: str, new_start_iso: str, duration_mins: int) -> dict:
    """Moves an existing flexible Google Calendar block to a new time slot."""
    service = get_calendar_service()
    event = service.events().get(calendarId="primary", eventId=gcal_event_id).execute()
    # Enforce in code, not just the prompt: only Pacer's own [Flex] blocks may move
    if event.get("extendedProperties", {}).get("private", {}).get("flexibility") != "movable":
        return {"error": "This is a fixed event (a class, meeting or anything Pacer didn't book), so it can't be moved."}
    
    new_start = datetime.datetime.fromisoformat(new_start_iso)
    new_end = new_start + datetime.timedelta(minutes=duration_mins)
    
    event["start"]["dateTime"] = new_start.isoformat()
    event["end"]["dateTime"] = new_end.isoformat()
    
    updated = service.events().update(calendarId="primary", eventId=gcal_event_id, body=event).execute()
    return {"status": "moved", "event_id": updated["id"], "new_start": new_start_iso, "duration_mins": duration_mins}


_assignments_cache = (float("-inf"), None)  # (time.monotonic() when read, result)


def get_upcoming_assignments() -> dict:
    """Lists the user's unfinished Canvas and Gradescope assignments (course, title, exact due time), read from their canvas-todo-sync tool. Read-only."""
    global _assignments_cache
    # The preview takes ~8 s; a schedule answer reads it for Gemini and again for the week picture
    if time.monotonic() - _assignments_cache[0] < 120:
        return _assignments_cache[1]
    py = os.path.join(CANVAS_TODO_HOME, ".venv", "Scripts", "python.exe") if os.name == "nt" else os.path.join(CANVAS_TODO_HOME, ".venv", "bin", "python")
    run = subprocess.run(
        [py, os.path.join(CANVAS_TODO_HOME, "canvas_todo_sync.py"), "--preview", "--json"],
        capture_output=True, text=True, timeout=120
    )
    if run.returncode != 0:
        # The tool logs its error to stderr; its last line is the useful part
        raise RuntimeError(f"canvas-todo-sync failed: {(run.stderr.strip().splitlines() or ['no output'])[-1]}")
    data = json.loads(run.stdout)
    result = {
        "assignments": [i for i in data["items"] if not i["complete"]],
        "warnings": data["warnings"]
    }
    _assignments_cache = (time.monotonic(), result)
    return result
