import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from scheduler_tools import (
    get_calibrated_estimate,
    check_calendar_availability,
    schedule_calibrated_task,
    log_task_progress_and_learn,
    move_calendar_event,
    get_upcoming_assignments
)

load_dotenv()

client = genai.Client()


def system_prompt():
    return f"""
You are a personal capacity-aware scheduling assistant. Today's current time is {datetime.datetime.now().isoformat()}.
Your job is to manage the user's Google Calendar and Supabase task history:
1. NEVER take the user's time estimate at face value. Always call `get_calibrated_estimate` first to check their historical multiplier for that category (`math_pset`, `cogs_assignment`, `ece_assignment`, `bipn_assignment`, `lab_research`, `chores_errands`, `daily_routine`).
2. If a calibrated task exceeds `max_session_mins` (usually 90 mins), recommend splitting it across multiple blocks.
3. Fixed events (`flexibility == 'fixed'`, like classes and lab meetings) CANNOT be moved. Movable events (`flexibility == 'movable'`) can be shifted if a higher-priority deadline needs the slot.
4. Protect evening rest and social blocks whenever possible. If the user asks "Can I join this club?" or "Can I go out Friday?", check `check_calendar_availability`, calculate total remaining flexible work, and give an honest capacity assessment before changing the calendar.
5. When the user asks what's due or to plan their homework, call `get_upcoming_assignments`. Map each course to a category by department: MATH -> `math_pset`, COGS -> `cogs_assignment`, ECE -> `ece_assignment`, BIPN -> `bipn_assignment`. If the user hasn't given a time estimate for an assignment, ask for one.
6. Before scheduling an assignment, check `check_calendar_availability` for an existing `[Flex]` block with the same title so you never book it twice.
"""


_chat, _chat_day = None, None


def get_chat():
    """Returns today's chat. Starting fresh each day stops the history (and its cost) from growing forever."""
    global _chat, _chat_day
    today = datetime.date.today()
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
                    get_upcoming_assignments
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