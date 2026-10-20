import os
import unittest
from types import SimpleNamespace

# Dummy values win over .env (load_dotenv never overrides), so tests can't touch real data
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_KEY"] = "sb_secret_test"
import scheduler_tools as st


class MoveGuardTest(unittest.TestCase):
    def move(self, event):
        updates = []
        events = SimpleNamespace(
            get=lambda **_: SimpleNamespace(execute=lambda: event),
            update=lambda body, **_: updates.append(body) or SimpleNamespace(execute=lambda: {"id": "e1"}),
        )
        st.get_calendar_service = lambda: SimpleNamespace(events=lambda: events)
        return st.move_calendar_event("e1", "2026-10-21T15:00:00", 60), updates

    def test_fixed_event_is_not_moved(self):
        result, updates = self.move({"id": "e1", "summary": "Lab meeting", "start": {}, "end": {}})
        self.assertIn("error", result)
        self.assertEqual(updates, [])

    def test_flex_block_moves(self):
        flex = {"id": "e1", "start": {}, "end": {}, "extendedProperties": {"private": {"flexibility": "movable"}}}
        result, updates = self.move(flex)
        self.assertEqual(result["status"], "moved")
        self.assertEqual(updates[0]["start"]["dateTime"], "2026-10-21T15:00:00")


if __name__ == "__main__":
    unittest.main()
