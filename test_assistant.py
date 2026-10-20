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


class DailyChatTest(unittest.TestCase):
    def test_new_chat_each_day(self):
        created = []
        client = SimpleNamespace(chats=SimpleNamespace(create=lambda **kw: created.append(kw) or object()))
        days = iter([datetime.date(2026, 10, 20), datetime.date(2026, 10, 20), datetime.date(2026, 10, 21)])
        fake_datetime = SimpleNamespace(date=SimpleNamespace(today=lambda: next(days)), datetime=datetime.datetime)
        with mock.patch.object(assistant, "client", client), mock.patch.object(assistant, "datetime", fake_datetime):
            first = assistant.get_chat()
            self.assertIs(assistant.get_chat(), first)  # same day: keep the conversation
            self.assertIsNot(assistant.get_chat(), first)  # next day: start fresh
        self.assertEqual(len(created), 2)


if __name__ == "__main__":
    unittest.main()
