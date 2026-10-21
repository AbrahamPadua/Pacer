import datetime
from google import genai
from google.genai import types
from scheduler_tools import (
    get_calibrated_estimate,
    check_calendar_availability,
    schedule_calibrated_task,
    log_task_progress_and_learn,
    move_calendar_event,
    get_upcoming_assignments,
    TZ
)
from week_image import free_time

client = genai.Client()


def system_prompt():
    return f"""
You are a personal capacity-aware scheduling assistant. Today's current time is {datetime.datetime.now(TZ).isoformat()}.
Your job is to manage the user's Google Calendar and Supabase task history:
1. NEVER take the user's time estimate at face value. Always call `get_calibrated_estimate` first to check their historical multiplier for that category (`math_pset`, `cogs_assignment`, `ece_assignment`, `bipn_assignment`, `lab_research`, `chores_errands`, `daily_routine`).
2. If a calibrated task exceeds `max_session_mins` (usually 90 mins), recommend splitting it across multiple blocks.
3. Fixed events (`flexibility == 'fixed'`, like classes and lab meetings) CANNOT be moved. Movable events (`flexibility == 'movable'`) can be shifted if a higher-priority deadline needs the slot.
4. Protect evening rest and social blocks whenever possible. If the user asks "Can I join this club?" or "Can I go out Friday?", check `check_calendar_availability`, calculate total remaining flexible work, and give an honest capacity assessment before changing the calendar.
5. When the user asks what's due or to plan their homework, call `get_upcoming_assignments`. Map each course to a category by department: MATH -> `math_pset`, COGS -> `cogs_assignment`, ECE -> `ece_assignment`, BIPN -> `bipn_assignment`. If the user hasn't given a time estimate for an assignment, ask for one.
6. Before scheduling an assignment, check `check_calendar_availability` for an existing `[Flex]` block with the same title so you never book it twice.
7. When your answer is about a single day (today, tomorrow, Friday, etc.), call `show_day_image` and tell the user exactly when they are free using its `free` list. When your answer is about the week or several days (or right after you book or move blocks across the week), call `show_week_image`. Call at most one of them per reply, and skip both for questions that have nothing to do with their schedule.
"""


_image_request = None  # ("week", None) or ("day", date); safe because the bot handles one message at a time


def show_week_image() -> dict:
    """Attaches a picture of the user's calendar for today and the next 6 days (classes, Pacer's blocks and assignment deadlines) under your reply. Call it when your answer is about the week or several days."""
    global _image_request
    _image_request = ("week", None)
    return {"status": "The picture will appear under your reply, so summarize instead of listing every event."}


def show_day_image(date: str = "") -> dict:
    """Attaches a picture of a single day's schedule (with free-time bands and deadlines) under your reply, and returns that day's free slots. `date` is YYYY-MM-DD (defaults to today). Call it when the user asks about a single day, then tell them their free times from `free`."""
    global _image_request
    day = datetime.date.fromisoformat(date) if date else datetime.datetime.now(TZ).date()
    _image_request = ("day", day)
    return {
        **free_time(day),
        "status": "The day picture will appear under your reply. Tell the user their free times from `free`."
    }


def pop_image_request():
    """Returns the pending ('week', None) or ('day', date) request from the last reply, and clears it."""
    global _image_request
    req, _image_request = _image_request, None
    return req


_chat, _chat_day = None, None


def get_chat():
    """Returns today's chat. Starting fresh each day stops the history (and its cost) from growing forever."""
    global _chat, _chat_day
    today = datetime.datetime.now(TZ).date()
    if _chat_day != today:
        _chat_day = today
        _chat = client.chats.create(
            model="gemini-3.8-flash",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt(),
                tools=[
                    get_calibrated_estimate,
                    check_calendar_availability,
                    schedule_calibrated_task,
                    log_task_progress_and_learn,
                    move_calendar_event,
                    get_upcoming_assignments,
                    show_week_image,
                    show_day_image
                ],
                temperature=0.2
            )
        )
    return _chat


if __name__ == "__main__":
    print("Scheduler Assistant Active. Type 'quit' to exit.\n")
    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in ["quit", "exit"]:
            break
        response = get_chat().send_message(user_input)
        print(f"\nAssistant: {response.text}\n")
        if req := pop_image_request():
            print(f"(Pacer attached a picture of your {req[0]}. It only shows up in Discord.)\n")
