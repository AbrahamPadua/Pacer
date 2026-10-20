import os
import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar"]
supabase: Client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

def get_calendar_service():
    """Authenticates and returns the Google Calendar API service."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def get_calibrated_estimate(category_id: str, user_est_mins: int) -> dict:
    """Looks up the user's historical multiplier for a task category and suggests a realistic duration."""
    res = supabase.table("category_profiles").select("*").eq("category_id", category_id).execute()
    if not res.data:
        return {"suggested_mins": user_est_mins, "multiplier": 1.0, "max_session_mins": 90}
    
    profile = res.data[0]
    mult = float(profile["duration_multiplier"])
    suggested = int(round((user_est_mins * mult) / 5.0) * 5)  # Round to nearest 5 mins
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
    service = get_calendar_service()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    end = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days_ahead)).isoformat()
    
    events_result = service.events().list(
        calendarId="primary", timeMin=now, timeMax=end,
        singleEvents=True, orderBy="startTime"
    ).execute()
    
    formatted = []
    for e in events_result.get("items", []):
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
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Los_Angeles"},
        "extendedProperties": {
            "private": {
                "task_db_id": str(task_id),
                "category_id": category_id,
                "flexibility": "movable"
            }
        }
    }
    created_event = service.events().insert(calendarId="primary", body=event_body).execute()
    
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
            observed_ratio = new_actual_mins / max(task["user_est_mins"], 15)
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


def move_calendar_event(gcal_event_id: str, new_start_iso: str, duration_mins: int) -> dict:
    """Moves an existing flexible Google Calendar block to a new time slot."""
    service = get_calendar_service()
    event = service.events().get(calendarId="primary", eventId=gcal_event_id).execute()
    
    new_start = datetime.datetime.fromisoformat(new_start_iso)
    new_end = new_start + datetime.timedelta(minutes=duration_mins)
    
    event["start"]["dateTime"] = new_start.isoformat()
    event["end"]["dateTime"] = new_end.isoformat()
    
    updated = service.events().update(calendarId="primary", eventId=gcal_event_id, body=event).execute()
    return {"status": "moved", "event_id": updated["id"], "new_start": new_start_iso, "duration_mins": duration_mins}