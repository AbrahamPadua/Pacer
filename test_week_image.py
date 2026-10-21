import os
import contextlib
import datetime as dt
import io
import struct
import unittest
from unittest import mock

# Dummy values win over .env (load_dotenv never overrides), so tests can't touch real data
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_KEY"] = "sb_secret_test"
import week_image as wi
from scheduler_tools import TZ

DAYS = [dt.date(2026, 10, 20) + dt.timedelta(d) for d in range(7)]


def event(summary, start, end, flex=False):
    return {"summary": summary, "start": start, "end": end, "flexibility": "movable" if flex else "fixed"}


class LayoutTest(unittest.TestCase):
    def test_overlapping_events_share_the_column(self):
        blocks = wi.layout([event("COGS 107A LE", "2026-10-22T09:30:00-07:00", "2026-10-22T10:50:00-07:00"),
                            event("ECE 161A LE", "2026-10-22T09:30:00-07:00", "2026-10-22T10:50:00-07:00"),
                            event("MATH 109 LE", "2026-10-22T13:00:00-07:00", "2026-10-22T13:50:00-07:00")], DAYS)
        self.assertEqual([(b["lane"], b["lanes"]) for b in blocks], [(0, 2), (1, 2), (0, 1)])

    def test_event_past_midnight_is_split_across_days(self):
        blocks = wi.layout([event("[Flex] MATH 170A HW4", "2026-10-21T23:00:00-07:00", "2026-10-22T01:00:00-07:00",
                                  flex=True)], DAYS)
        self.assertEqual([(b["day"], b["s"], b["e"]) for b in blocks], [(1, 23.0, 24.0), (2, 0.0, 1.0)])
        self.assertEqual((blocks[0]["course"], blocks[0]["rest"], blocks[0]["flex"]), ("MATH 170A", "HW4", True))

    def test_utc_times_land_on_the_pacific_day(self):
        # 03:00 UTC on Oct 21 is 8 PM Pacific on Oct 20
        blocks = wi.layout([event("BIPN 194 SE", "2026-10-21T03:00:00Z", "2026-10-21T04:00:00Z")], DAYS)
        self.assertEqual((blocks[0]["day"], blocks[0]["s"]), (0, 20.0))

    def test_all_day_and_out_of_range_events_are_left_out(self):
        self.assertEqual(wi.layout([event("Holiday", "2026-10-21", "2026-10-22"),
                                    event("MATH 109 LE", "2026-10-30T13:00:00-07:00", "2026-10-30T13:50:00-07:00")],
                                   DAYS), [])


class FreeSlotsTest(unittest.TestCase):
    def test_empty_day_is_free_8_am_to_10_pm(self):
        self.assertEqual(wi.free_slots([]), [(8.0, 22.0)])

    def test_gaps_under_30_minutes_are_dropped(self):
        blocks = [{"s": 17.0, "e": 18.5}, {"s": 18.75, "e": 19.75}]
        self.assertEqual(wi.free_slots(blocks), [(8.0, 17.0), (19.75, 22.0)])

    def test_overlapping_events_count_as_one_busy_stretch(self):
        blocks = [{"s": 9.5, "e": 11.0}, {"s": 10.0, "e": 11.5}, {"s": 9.75, "e": 10.5}]
        self.assertEqual(wi.free_slots(blocks), [(8.0, 9.5), (11.5, 22.0)])

    def test_events_outside_8_am_to_10_pm(self):
        blocks = [{"s": 6.0, "e": 7.5}, {"s": 7.5, "e": 9.0}, {"s": 21.0, "e": 23.0}, {"s": 23.0, "e": 24.0}]
        self.assertEqual(wi.free_slots(blocks), [(9.0, 21.0)])

    def test_after_rounds_up_to_15_minutes(self):
        blocks = [{"s": 15.0, "e": 17.0}]
        self.assertEqual(wi.free_slots(blocks, after=14 + 10 / 60), [(14.25, 15.0), (17.0, 22.0)])
        self.assertEqual(wi.free_slots([], after=6.1), [(8.0, 22.0)])


class FreeTimeTest(unittest.TestCase):
    def test_labels_and_total_for_today_and_another_day(self):
        events = [event("MATH 170A", "2026-10-20T15:00:00-07:00", "2026-10-20T18:30:00-07:00")]
        with mock.patch.object(wi, "list_events", lambda start, end: events):
            other = wi.free_time(dt.date(2026, 10, 20), now=dt.datetime(2026, 10, 19, 12, 0, tzinfo=TZ))
            today = wi.free_time(dt.date(2026, 10, 20), now=dt.datetime(2026, 10, 20, 14, 18, tzinfo=TZ))
        self.assertEqual(other, {"date": "2026-10-20", "free": ["8 AM-3 PM (7h)", "6:30-10 PM (3h 30m)"],
                                 "total": "10h 30m"})
        self.assertEqual(today, {"date": "2026-10-20", "free": ["2:30-3 PM (30m)", "6:30-10 PM (3h 30m)"],
                                 "total": "4h"})


class VisibleHoursTest(unittest.TestCase):
    def test_default_day_is_8_am_to_10_pm(self):
        self.assertEqual(wi.visible_hours([], []), (8, 22))

    def test_grid_stretches_to_early_events_and_deadlines(self):
        self.assertEqual(wi.visible_hours([{"s": 7.5, "e": 8.5}], []), (7, 22))
        self.assertEqual(wi.visible_hours([], [6.0]), (6, 22))  # a 6 AM deadline must not be drawn at 8 AM

    def test_late_deadline_runs_the_grid_to_midnight(self):
        self.assertEqual(wi.visible_hours([], [23 + 59 / 60]), (8, 24))


class RenderTest(unittest.TestCase):
    events = [event("COGS 107A LE", "2026-10-20T09:30:00-07:00", "2026-10-20T10:50:00-07:00"),
              event("ECE 161A LE", "2026-10-20T09:30:00-07:00", "2026-10-20T10:50:00-07:00"),
              event("[Flex] MATH 170A HW3 (Part 1)", "2026-10-20T17:00:00-07:00", "2026-10-20T18:30:00-07:00", True),
              event("[Flex] MATH 170A HW3 (Part 2)", "2026-10-20T18:45:00-07:00", "2026-10-20T19:45:00-07:00", True),
              event("Lab meeting", "2026-10-21T23:00:00-07:00", "2026-10-22T01:00:00-07:00")]
    dues = [{"course": "MATH 170A", "title": "HW3", "due": "2026-10-20T23:59:00-07:00"},
            {"course": "BIPN 194", "title": "Questions about Week 5", "due": "2026-10-24T23:59:00-07:00"},
            {"course": "ECE 161A", "title": "HW 3", "due": "2026-10-24T23:59:00-07:00"},
            {"course": "COGS 107A", "title": "Extra credit survey", "due": None}]
    now = dt.datetime(2026, 10, 20, 17, 30, tzinfo=TZ)

    def assert_full_png(self, png, size=(2520, 1764)):
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct.unpack(">II", png[16:24]), size)  # width and height from the PNG header

    def test_draws_the_week_with_deadlines(self):
        self.assert_full_png(wi.render_week(self.events, self.dues, self.now))

    def test_draws_without_deadlines_or_events(self):
        self.assert_full_png(wi.render_week(self.events, None, self.now))
        self.assert_full_png(wi.render_week([], [], self.now))

    def test_draws_a_day_for_today_and_another_day(self):
        self.assert_full_png(wi.render_day(self.events, self.dues, self.now, dt.date(2026, 10, 20)), (810, 1764))
        self.assert_full_png(wi.render_day(self.events, None, self.now, dt.date(2026, 10, 21)), (810, 1764))
        self.assert_full_png(wi.render_day([], [], self.now, dt.date(2026, 10, 23)), (810, 1764))


class ColorTest(unittest.TestCase):
    def test_colors_come_from_everything_passed_in(self):
        events = [event("MATH 170A LE", "2026-10-21T08:00:00-07:00", "2026-10-21T08:50:00-07:00"),
                  event("[Flex] COGS 107A Reading", "2026-10-22T15:00:00-07:00", "2026-10-22T16:00:00-07:00", True)]
        colors = wi.course_colors(events, [{"course": "BIPN 194", "title": "Paper", "due": None}])
        self.assertEqual(sorted(colors), ["BIPN 194", "COGS 107A", "MATH 170A"])
        self.assertEqual(colors["MATH 170A"], wi.PALETTE[2])  # third course alphabetically, on any day

    def test_day_picture_reads_the_week_window_so_colors_match(self):
        windows = []
        now = dt.datetime.now(TZ)
        with mock.patch.object(wi, "list_events", lambda start, end: windows.append((start.date(), end - start)) or []), \
                mock.patch.object(wi, "get_upcoming_assignments", lambda: {"assignments": []}):
            wi.day_png(now.date() + dt.timedelta(2))
            wi.day_png(now.date() + dt.timedelta(10))
        self.assertEqual(windows, [(now.date(), dt.timedelta(7)), (now.date() + dt.timedelta(10), dt.timedelta(7))])


class WeekPngTest(unittest.TestCase):
    def test_missing_deadlines_are_logged_and_the_image_still_goes_out(self):
        def broken():
            raise RuntimeError("canvas-todo-sync failed: ERROR Canvas token was rejected")
        log = io.StringIO()
        with mock.patch.object(wi, "get_upcoming_assignments", broken), \
                mock.patch.object(wi, "list_events", lambda start, end: []), contextlib.redirect_stdout(log):
            png = wi.week_png()
            day = wi.day_png(dt.date(2026, 10, 20))
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertTrue(day.startswith(b"\x89PNG"))
        self.assertIn("Canvas token was rejected", log.getvalue())


if __name__ == "__main__":
    unittest.main()
