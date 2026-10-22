import os
import datetime
import unittest
from types import SimpleNamespace
from unittest import mock

# Dummy values win over .env (load_dotenv never overrides), so tests can't touch real data or spend credits
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_KEY"] = "sb_secret_test"
os.environ["GEMINI_API_KEY"] = "test"
import assistant
import scheduler_tools
import week_image as wi
from google.genai import types


class DailyChatTest(unittest.TestCase):
    def test_new_chat_each_pacific_day(self):
        created, clock = [], {}

        class FakeDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"].astimezone(tz)

        client = SimpleNamespace(chats=SimpleNamespace(create=lambda **kw: created.append(kw) or object()))
        utc = datetime.timezone.utc
        with mock.patch.object(assistant, "client", client), \
                mock.patch.object(assistant, "datetime", SimpleNamespace(datetime=FakeDatetime)):
            clock["now"] = datetime.datetime(2026, 10, 21, 3, 0, tzinfo=utc)  # Tue Oct 20, 8:00 PM Pacific
            first = assistant.get_chat()
            clock["now"] = datetime.datetime(2026, 10, 21, 6, 59, tzinfo=utc)  # 11:59 PM Pacific, same day
            self.assertIs(assistant.get_chat(), first)
            clock["now"] = datetime.datetime(2026, 10, 21, 7, 1, tzinfo=utc)  # 12:01 AM Pacific, next day
            self.assertIsNot(assistant.get_chat(), first)
        self.assertEqual(len(created), 2)
        self.assertIn("2026-10-20T20:00:00-07:00", created[0]["config"].system_instruction)


class ImageRequestTest(unittest.TestCase):
    def test_week_request_is_read_once(self):
        assistant.pop_image_request()
        self.assertIsNone(assistant.pop_image_request())
        assistant.show_week_image()
        self.assertEqual(assistant.pop_image_request(), ("week", None))
        self.assertIsNone(assistant.pop_image_request())

    def test_day_request_returns_free_time_and_clears(self):
        assistant.pop_image_request()
        with mock.patch.object(wi, "list_events", lambda start, end: []):
            res = assistant.show_day_image("2026-10-23")
        self.assertEqual(res["date"], "2026-10-23")
        self.assertEqual(res["free"], ["8 AM-10 PM (14h)"])
        self.assertEqual(res["total"], "14h")
        self.assertIn("status", res)
        self.assertEqual(assistant.pop_image_request(), ("day", datetime.date(2026, 10, 23)))
        self.assertIsNone(assistant.pop_image_request())


class TrackedWriteTest(unittest.TestCase):
    def setUp(self):
        assistant.pop_failed_writes()

    def test_failures_are_recorded_and_successes_pass_through(self):
        def create_calendar_event(title: str) -> dict:
            if title == "Broken":
                raise RuntimeError("Google is down")
            return {"error": "end must be after start"} if title == "Refused" else {"status": "created"}
        tracked = assistant._track(create_calendar_event)
        self.assertEqual(tracked(title="Lab"), {"status": "created"})
        self.assertEqual(tracked(title="Refused"), {"error": "end must be after start"})
        with self.assertRaises(RuntimeError):  # still raised, so Gemini sees the error too
            tracked(title="Broken")
        self.assertEqual(assistant.pop_failed_writes(), ["Refused: end must be after start", "Broken: Google is down"])
        self.assertEqual(assistant.pop_failed_writes(), [])

    def test_tool_without_a_title_is_named_by_what_it_does(self):
        def log_task_progress_and_learn(task_id: str) -> dict:
            return {"error": "Task already completed"}
        assistant._track(log_task_progress_and_learn)(task_id="7")
        self.assertEqual(assistant.pop_failed_writes(), ["log task progress and learn: Task already completed"])

    def test_gemini_sees_the_same_tool_after_wrapping(self):
        for tool in (scheduler_tools.schedule_calibrated_task, scheduler_tools.log_task_progress_and_learn,
                     scheduler_tools.move_calendar_event, scheduler_tools.create_calendar_event):
            as_declared = lambda f: types.FunctionDeclaration.from_callable(client=assistant.client, callable=f)
            self.assertEqual(as_declared(assistant._track(tool)), as_declared(tool))


if __name__ == "__main__":
    unittest.main()
