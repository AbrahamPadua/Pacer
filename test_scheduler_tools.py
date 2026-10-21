import os
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from google.auth.exceptions import RefreshError

# Dummy values win over .env (load_dotenv never overrides), so tests can't touch real data
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_KEY"] = "sb_secret_test"
import scheduler_tools as st


class FakeTable:
    """Just enough of the Supabase query chain for scheduler_tools: select/update/insert/delete + eq + execute."""

    def __init__(self, rows):
        self.rows, self.changes, self.deleting = rows, None, False

    def select(self, *_):
        return self

    def update(self, changes):
        self.changes = changes
        return self

    def insert(self, row):
        self.rows.append({"id": str(len(self.rows)), **row})
        return self.eq("id", self.rows[-1]["id"])

    def delete(self):
        self.deleting = True
        return self

    def eq(self, col, val):
        self.match = (col, val)
        return self

    def execute(self):
        hits = [r for r in self.rows if r[self.match[0]] == self.match[1]]
        for r in hits:
            r.update(self.changes or {})
            if self.deleting:
                self.rows.remove(r)
        return SimpleNamespace(data=[dict(r) for r in hits])


class FakeDbTest(unittest.TestCase):
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


class LearningTest(FakeDbTest):
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


class ScheduleTest(FakeDbTest):
    def test_failed_calendar_write_removes_task_row(self):
        def insert(**_):
            raise RuntimeError("calendar down")
        self.enterContext(mock.patch.object(
            st, "get_calendar_service", lambda: SimpleNamespace(events=lambda: SimpleNamespace(insert=insert))))
        with self.assertRaises(RuntimeError):
            st.schedule_calibrated_task("HW3", "math_pset", 120, 150, "2026-10-21T15:00:00", "2026-10-23T23:59:00")
        self.assertEqual(self.tasks, [])

    def test_reads_every_calendar_page(self):
        def event(event_id):
            return {"id": event_id, "start": {"dateTime": "s"}, "end": {"dateTime": "e"}}
        pages = {None: {"items": [event("a")], "nextPageToken": "p2"}, "p2": {"items": [event("b")]}}
        events = SimpleNamespace(list=lambda pageToken, **_: SimpleNamespace(execute=lambda: pages[pageToken]))
        self.enterContext(mock.patch.object(st, "get_calendar_service", lambda: SimpleNamespace(events=lambda: events)))
        self.assertEqual([e["event_id"] for e in st.check_calendar_availability(70)], ["a", "b"])


class MoveGuardTest(unittest.TestCase):
    def move(self, event):
        updates = []
        events = SimpleNamespace(
            get=lambda **_: SimpleNamespace(execute=lambda: event),
            update=lambda body, **_: updates.append(body) or SimpleNamespace(execute=lambda: {"id": "e1"}),
        )
        self.enterContext(mock.patch.object(st, "get_calendar_service", lambda: SimpleNamespace(events=lambda: events)))
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


class SignInTest(unittest.TestCase):
    """Google rejects the saved sign-in; what happens next depends on whether someone is at a terminal."""

    def setUp(self):
        self.old_cwd, self.tmp = os.getcwd(), tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)  # token.json reads and writes land here, never in the project
        with open("token.json", "w") as f:
            f.write("{}")
        self.flow_calls = []

        def refresh(_):
            raise RefreshError("invalid_grant: Token has been expired or revoked.")
        expired = SimpleNamespace(valid=False, expired=True, refresh_token="r", refresh=refresh)
        new = SimpleNamespace(valid=True, to_json=lambda: '{"new": true}')
        flow = SimpleNamespace(run_local_server=lambda **kw: self.flow_calls.append(kw) or new)
        for name, fake in [("Credentials", SimpleNamespace(from_authorized_user_file=lambda *a: expired)),
                           ("InstalledAppFlow", SimpleNamespace(from_client_secrets_file=lambda *a: flow)),
                           ("build", lambda *a, **kw: "service")]:
            patcher = mock.patch.object(st, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_server_without_terminal_fails_fast(self):
        with mock.patch.object(sys, "stdin", SimpleNamespace(isatty=lambda: False)):
            with self.assertRaisesRegex(RuntimeError, "copy token.json"):
                st.get_calendar_service()
        self.assertEqual(self.flow_calls, [])

    def test_terminal_falls_back_to_new_sign_in(self):
        with mock.patch.object(sys, "stdin", SimpleNamespace(isatty=lambda: True)):
            self.assertEqual(st.get_calendar_service(), "service")
        self.assertEqual(self.flow_calls, [{"port": 0, "timeout_seconds": 120}])
        with open("token.json") as f:
            self.assertEqual(f.read(), '{"new": true}')


class AssignmentsTest(unittest.TestCase):
    def run_tool(self, returncode, stdout="", stderr=""):
        fake_run = lambda *a, **kw: subprocess.CompletedProcess(a, returncode, stdout, stderr)
        with mock.patch.object(st.subprocess, "run", fake_run):
            return st.get_upcoming_assignments()

    def test_finished_assignments_are_dropped(self):
        out = json.dumps({"items": [{"title": "HW2", "complete": True}, {"title": "HW3", "complete": False}],
                          "warnings": ["Gradescope skipped"]})
        self.assertEqual(self.run_tool(0, out),
                         {"assignments": [{"title": "HW3", "complete": False}], "warnings": ["Gradescope skipped"]})

    def test_failed_run_reports_its_last_error_line(self):
        with self.assertRaisesRegex(RuntimeError, "^canvas-todo-sync failed: ERROR Canvas token was rejected$"):
            self.run_tool(1, stderr="INFO starting\nERROR Canvas token was rejected\n")


if __name__ == "__main__":
    unittest.main()
