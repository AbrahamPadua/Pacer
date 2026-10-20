import os
import unittest
from types import SimpleNamespace

# Dummy values win over .env (load_dotenv never overrides), so tests can't touch real data
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_KEY"] = "sb_secret_test"
import scheduler_tools as st


class FakeTable:
    """Just enough of the Supabase query chain for scheduler_tools: select/update + eq + execute."""

    def __init__(self, rows):
        self.rows = rows

    def select(self, *_):
        self.changes = None
        return self

    def update(self, changes):
        self.changes = changes
        return self

    def eq(self, col, val):
        self.match = (col, val)
        return self

    def execute(self):
        hits = [r for r in self.rows if r[self.match[0]] == self.match[1]]
        for r in hits:
            r.update(self.changes or {})
        return SimpleNamespace(data=[dict(r) for r in hits])


class LearningTest(unittest.TestCase):
    def setUp(self):
        self.categories = [
            {"category_id": "daily_routine", "duration_multiplier": 1.0, "sample_count": 0, "max_session_mins": 30, "preferred_window": "evening"},
            {"category_id": "math_pset", "duration_multiplier": 1.25, "sample_count": 0, "max_session_mins": 90, "preferred_window": "afternoon"},
        ]
        self.tasks = []
        tables = {"category_profiles": self.categories, "tasks_log": self.tasks}
        st.supabase = SimpleNamespace(table=lambda name: FakeTable(tables[name]))

    def add_task(self, category_id, user_est_mins):
        self.tasks.append({"id": str(len(self.tasks)), "category_id": category_id, "user_est_mins": user_est_mins,
                           "actual_mins_spent": 0, "reschedule_count": 0, "status": "scheduled"})
        return self.tasks[-1]["id"]

    def multiplier(self, category_id):
        return next(c for c in self.categories if c["category_id"] == category_id)["duration_multiplier"]

    def test_accurate_guess_keeps_multiplier(self):
        task = self.add_task("math_pset", 120)
        self.assertEqual(st.log_task_progress_and_learn(task, 150, 100)["new_category_multiplier"], 1.25)

    def test_perfect_short_task_keeps_multiplier(self):
        task = self.add_task("daily_routine", 5)
        st.log_task_progress_and_learn(task, 5, 100)
        self.assertEqual(self.multiplier("daily_routine"), 1.0)

    def test_tiny_guess_cannot_blow_up_multiplier(self):
        task = self.add_task("daily_routine", 2)
        st.log_task_progress_and_learn(task, 10, 100)
        self.assertEqual(self.multiplier("daily_routine"), 1.0)

    def test_second_completion_is_refused(self):
        task = self.add_task("math_pset", 120)
        st.log_task_progress_and_learn(task, 150, 100)
        self.assertEqual(st.log_task_progress_and_learn(task, 150, 100), {"error": "Task already completed"})
        self.assertEqual(self.multiplier("math_pset"), 1.25)
        self.assertEqual(self.tasks[0]["actual_mins_spent"], 150)

    def test_tiny_estimate_gets_at_least_5_minutes(self):
        self.assertEqual(st.get_calibrated_estimate("daily_routine", 2)["suggested_mins"], 5)

    def test_unknown_category_is_an_error(self):
        self.assertEqual(st.get_calibrated_estimate("math_181b", 60), {"error": "Unknown category math_181b"})


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
