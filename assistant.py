import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from scheduler_tools import (
    get_calibrated_estimate,
    check_calendar_availability,
    schedule_calibrated_task,
    log_task_progress_and_learn,
    move_calendar_event
)

load_dotenv()

client = genai.Client()

SYSTEM_PROMPT = f"""
You are a personal capacity-aware scheduling assistant. Today's current time is {datetime.datetime.now().isoformat()}.
Your job is to manage the user's Google Calendar and Supabase task history:
1. NEVER take the user's time estimate at face value. Always call `get_calibrated_estimate` first to check their historical multiplier for that category (`math_pset`, `cogs_assignment`, `lab_research`, `chores_errands`, `daily_routine`).
2. If a calibrated task exceeds `max_session_mins` (usually 90 mins), recommend splitting it across multiple blocks.
3. Fixed events (`flexibility == 'fixed'`, like classes and lab meetings) CANNOT be moved. Movable events (`flexibility == 'movable'`) can be shifted if a higher-priority deadline needs the slot.
4. Protect evening rest and social blocks whenever possible. If the user asks "Can I join this club?" or "Can I go out Friday?", check `check_calendar_availability`, calculate total remaining flexible work, and give an honest capacity assessment before changing the calendar.
"""

chat = client.chats.create(
    model="gemini-2.5-flash",
    config=types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[
            get_calibrated_estimate,
            check_calendar_availability,
            schedule_calibrated_task,
            log_task_progress_and_learn,
            move_calendar_event
        ],
        temperature=0.2
    )
)

if __name__ == "__main__":
    print("Scheduler Assistant Active. Type 'quit' to exit.\n")
    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in ["quit", "exit"]:
            break
        response = chat.send_message(user_input)
        print(f"\nAssistant: {response.text}\n")