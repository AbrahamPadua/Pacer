import os
import asyncio
import contextlib
import datetime
import unittest
from types import SimpleNamespace
from unittest import mock
from aiohttp.test_utils import TestClient, TestServer

# Dummy values win over .env (load_dotenv never overrides), so tests can't touch real data or spend credits
os.environ.update(SUPABASE_URL="https://example.supabase.co", SUPABASE_KEY="sb_secret_test",
                  GEMINI_API_KEY="test", DISCORD_OWNER_ID="111")
import discord
import assistant
import bot
import week_image as wi

OWNER, STRANGER, BOT_USER = SimpleNamespace(id=111), SimpleNamespace(id=222), SimpleNamespace(id=999)


class FakeServerChannel:
    def __init__(self, channel_id=444):
        self.id = channel_id

    def typing(self):
        return contextlib.AsyncExitStack()


class FakeDM(discord.DMChannel):
    def __init__(self):
        pass  # skip discord's setup; only the isinstance check and typing() are used

    def typing(self):
        return contextlib.AsyncExitStack()


class OnMessageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.replies, self.files, self.prompts, self.answer = [], [], [], "ok"
        self.enterContext(mock.patch.object(bot, "lock", asyncio.Lock()))
        self.enterContext(mock.patch.object(bot.bot._connection, "user", BOT_USER))
        self.enterContext(mock.patch.object(bot, "get_chat", lambda: SimpleNamespace(send_message=self.gemini)))
        self.enterContext(mock.patch.object(bot, "week_png", lambda: b"\x89PNG fake"))
        self.enterContext(mock.patch.object(bot, "day_png", lambda d: b"\x89PNG fake day"))

    def gemini(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return SimpleNamespace(text=self.answer)

    async def send(self, content, author=OWNER, mentioned=True, channel=None):
        async def reply(content=None, file=None):
            self.replies.append(content)
            if file:
                self.files.append(file.filename)
        await bot.on_message(SimpleNamespace(author=author, content=content, reply=reply,
                                             mentions=[BOT_USER] if mentioned else [],
                                             channel=channel or FakeServerChannel()))

    async def test_strangers_are_ignored_even_when_they_mention_the_bot(self):
        await self.send("move my lab meeting to tomorrow", author=STRANGER)
        await self.send("week", author=STRANGER)
        self.assertEqual((self.replies, self.prompts), ([], []))

    async def test_owner_is_ignored_in_a_server_without_a_mention(self):
        await self.send("going out friday lol", mentioned=False)
        self.assertEqual((self.replies, self.prompts), ([], []))

    async def test_pacer_channel_needs_no_mention_but_is_still_owner_only(self):
        self.enterContext(mock.patch.object(bot, "PACER_CHANNEL_ID", 555))
        await self.send("what does my day look like?", mentioned=False, channel=FakeServerChannel(555))
        await self.send("lol same", mentioned=False, channel=FakeServerChannel(444))  # any other channel
        await self.send("delete my classes", author=STRANGER, mentioned=False, channel=FakeServerChannel(555))
        self.assertEqual((self.replies, self.prompts[0].endswith("what does my day look like?")), (["ok"], True))
        self.assertEqual(len(self.prompts), 1)

    async def test_owner_dm_goes_to_gemini_with_the_time(self):
        await self.send("what's due this week?", mentioned=False, channel=FakeDM())
        self.assertEqual(self.replies, ["ok"])
        self.assertTrue(self.prompts[0].startswith("[Current time: "))
        self.assertTrue(self.prompts[0].endswith("] what's due this week?"))

    async def test_week_sends_the_image_without_gemini(self):
        for text in ["week", "<@999> week", "<@!999>  WEEK "]:
            await self.send(text)
        self.assertEqual((self.replies, self.files, self.prompts), ([None] * 3, ["week.png"] * 3, []))

    async def test_gemini_decides_when_to_attach_the_week_picture(self):
        await self.send("how long should HW4 take?")  # Gemini doesn't call the tool
        self.assertEqual((self.replies, self.files), (["ok"], []))

        def gemini_shows_week(prompt):
            assistant.show_week_image()  # what Gemini's tool call does
            return SimpleNamespace(text="Busy Wednesday, free Saturday.")
        with mock.patch.object(bot, "get_chat", lambda: SimpleNamespace(send_message=gemini_shows_week)):
            await self.send("what does my week look like?")
        self.assertEqual((self.replies[1:], self.files), (["Busy Wednesday, free Saturday."], ["week.png"]))

    async def test_gemini_attaches_the_day_picture(self):
        drawn = []

        def gemini_shows_day(prompt):
            assistant.show_day_image("2026-10-23")
            return SimpleNamespace(text="You are free 8 AM-10 PM.")
        with mock.patch.object(wi, "list_events", lambda start, end: []), \
                mock.patch.object(bot, "day_png", lambda d: drawn.append(d) or b"\x89PNG fake day"), \
                mock.patch.object(bot, "get_chat", lambda: SimpleNamespace(send_message=gemini_shows_day)):
            await self.send("what does Friday look like?")
        self.assertEqual((self.replies, self.files, drawn),
                         (["You are free 8 AM-10 PM."], ["day.png"], [datetime.date(2026, 10, 23)]))

    async def test_failed_writes_show_under_a_confident_reply(self):
        def schedule_calibrated_task(title):
            raise RuntimeError("Supabase is down")
        tracked = assistant._track(schedule_calibrated_task)

        def gemini_claims_success(prompt):
            try:
                tracked(title="MATH 170A HW4 (Part 1)")
            except RuntimeError:
                pass  # the SDK hands tool errors to Gemini instead of raising
            return SimpleNamespace(text="I have scheduled your HW4 block.")
        assistant._failed_writes.append("old: left over from a reply that crashed")
        with mock.patch.object(bot, "get_chat", lambda: SimpleNamespace(send_message=gemini_claims_success)):
            await self.send("book HW4 thursday at 4")
        self.assertEqual(self.replies,
                         ["I have scheduled your HW4 block.\n\nNot saved: MATH 170A HW4 (Part 1): Supabase is down"])

    async def test_leftover_picture_request_is_dropped(self):
        assistant._image_request = ("week", None)  # a reply that failed after Gemini called the tool
        await self.send("thanks!")
        self.assertEqual(self.files, [])

    async def test_picture_failure_keeps_the_answer(self):
        def gemini_shows_week(prompt):
            assistant.show_week_image()
            return SimpleNamespace(text="Here's your week.")

        def broken_week():
            raise RuntimeError("calendar down")
        with mock.patch.object(bot, "get_chat", lambda: SimpleNamespace(send_message=gemini_shows_week)), \
                mock.patch.object(bot, "week_png", broken_week):
            await self.send("show me my schedule")
        self.assertEqual((self.replies, self.files), (["Here's your week.\n\n(Couldn't draw your week: calendar down)"], []))

    async def test_long_answers_are_split_for_discord(self):
        self.answer = "x" * 4500
        await self.send("plan my week")
        self.assertEqual([len(r) for r in self.replies], [2000, 2000, 500])

    async def test_errors_are_replied_not_swallowed(self):
        self.answer = RuntimeError("Gemini credits depleted")
        await self.send("plan my week")

        def broken_week():
            raise RuntimeError("calendar down")
        with mock.patch.object(bot, "week_png", broken_week):
            await self.send("week")
        self.assertEqual(self.replies, ["Something broke: Gemini credits depleted", "Something broke: calendar down"])


class CoverTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.drawn = []
        self.enterContext(mock.patch.object(bot, "COVER_TOKEN", "s3cret-token"))
        self.enterContext(mock.patch.object(bot, "lock", asyncio.Lock()))
        self.enterContext(mock.patch.object(bot, "day_png",
                                            lambda d, size: self.drawn.append((d, size)) or b"\x89PNG fake cover"))
        self.enterContext(mock.patch.object(bot, "week_png", lambda: self.drawn.append("week") or b"\x89PNG fake week"))
        self.client = TestClient(TestServer(bot.web_app()))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_secret_url_gets_todays_cover_picture(self):
        res = await self.client.get("/cover/s3cret-token.png")
        self.assertEqual((res.status, res.content_type, await res.read()), (200, "image/png", b"\x89PNG fake cover"))
        self.assertEqual(res.headers["Cache-Control"], "no-store")
        self.assertEqual(self.drawn, [(datetime.datetime.now(bot.TZ).date(), wi.COVER)])

    async def test_secret_url_gets_the_week_picture(self):
        res = await self.client.get("/week/s3cret-token.png")
        self.assertEqual((res.status, await res.read(), self.drawn), (200, b"\x89PNG fake week", ["week"]))

    async def test_wrong_or_missing_token_gets_nothing(self):
        for path in ["/cover/wrong.png", "/cover/s3cret-toke.png", "/cover/s3cret-token", "/cover/%C3%A9.png", "/",
                     "/week/wrong.png", "/month/s3cret-token.png"]:
            res = await self.client.get(path)
            self.assertEqual(res.status, 404, path)
        self.assertEqual(self.drawn, [])

    async def test_server_listens_on_localhost_only_and_only_with_a_token(self):
        site = mock.MagicMock(return_value=SimpleNamespace(start=mock.AsyncMock()))
        with mock.patch.object(bot.web, "AppRunner", lambda app: SimpleNamespace(setup=mock.AsyncMock())), \
                mock.patch.object(bot.web, "TCPSite", site):
            await bot.setup_hook()  # pictures only
            with mock.patch.object(bot, "COVER_TOKEN", ""):
                await bot.setup_hook()  # neither token: no server
                with mock.patch.object(bot, "ASK_TOKEN", "ask-s3cret"):
                    await bot.setup_hook()  # /ask messages only
        self.assertEqual(site.call_args_list, [mock.call(mock.ANY, "127.0.0.1", 8080)] * 2)


class AskTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.prompts = []
        self.enterContext(mock.patch.object(bot, "ASK_TOKEN", "ask-s3cret"))
        self.enterContext(mock.patch.object(bot, "lock", asyncio.Lock()))
        self.enterContext(mock.patch.object(bot, "get_chat", lambda: SimpleNamespace(send_message=self.gemini)))
        self.client = TestClient(TestServer(bot.web_app()))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def gemini(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(text="Booked HW4 Thursday 4-6 PM.")

    async def post(self, body, token="ask-s3cret"):
        return await self.client.post("/ask", json=body, headers={"Authorization": f"Bearer {token}"})

    async def test_phone_message_goes_to_gemini_with_the_time_and_asks_for_a_notification_reply(self):
        res = await self.post({"text": "schedule HW4 thursday at 4"})
        self.assertEqual((res.status, await res.json()), (200, {"reply": "Booked HW4 Thursday 4-6 PM."}))
        self.assertTrue(self.prompts[0].startswith("[Current time: "))
        self.assertIn(bot.PHONE_STYLE, self.prompts[0])
        self.assertTrue(self.prompts[0].endswith("] schedule HW4 thursday at 4"))

    async def test_markdown_comes_back_plain_for_the_notification(self):
        def markdown(prompt):
            return SimpleNamespace(text="**Booked** `HW4`:\n* Thu 4-6 PM\n  - Fri 2-3 PM\n## Free after 6")
        with mock.patch.object(bot, "get_chat", lambda: SimpleNamespace(send_message=markdown)):
            res = await self.post({"text": "book HW4"})
        self.assertEqual((await res.json())["reply"], "Booked HW4:\n• Thu 4-6 PM\n• Fri 2-3 PM\nFree after 6")

    async def test_discord_replies_keep_their_markdown(self):
        replies = []

        async def reply(content=None, file=None):
            replies.append(content)
        self.enterContext(mock.patch.object(bot.bot._connection, "user", BOT_USER))
        gemini = SimpleNamespace(send_message=lambda prompt: SimpleNamespace(text="**Booked** HW4"))
        with mock.patch.object(bot, "get_chat", lambda: gemini):
            await bot.on_message(SimpleNamespace(author=OWNER, content="book HW4", reply=reply, mentions=[BOT_USER],
                                                 channel=FakeServerChannel()))
        self.assertEqual(replies, ["**Booked** HW4"])

    async def test_wrong_missing_or_unset_key_gets_nothing(self):
        for headers in [{"Authorization": "Bearer wrong"}, {"Authorization": "Bearer "}, {}]:
            res = await self.client.post("/ask", json={"text": "delete my classes"}, headers=headers)
            self.assertEqual(res.status, 404, headers)
        with mock.patch.object(bot, "ASK_TOKEN", ""):  # an unset key must not match an empty one
            self.assertEqual((await self.post({"text": "delete my classes"}, token="")).status, 404)
        self.assertEqual((await self.client.get("/cover/ask-s3cret.png")).status, 404)  # the ask key opens no pictures
        self.assertEqual(self.prompts, [])

    async def test_bad_bodies_are_refused(self):
        for body in [{}, {"text": ""}, {"text": "  "}, {"text": 5}, ["text"], {"text": "x" * 2001}]:
            self.assertEqual((await self.post(body)).status, 400, body)
        res = await self.client.post("/ask", data="not json", headers={"Authorization": "Bearer ask-s3cret"})
        self.assertEqual((res.status, self.prompts), (400, []))

    async def test_failed_writes_show_in_the_reply(self):
        def gemini_claims_success(prompt):
            assistant._failed_writes.append("MATH 170A HW4 (Part 1): Supabase is down")
            return SimpleNamespace(text="I have scheduled your HW4 block.")
        with mock.patch.object(bot, "get_chat", lambda: SimpleNamespace(send_message=gemini_claims_success)):
            res = await self.post({"text": "book HW4 thursday at 4"})
        self.assertEqual((await res.json())["reply"],
                         "I have scheduled your HW4 block.\n\nNot saved: MATH 170A HW4 (Part 1): Supabase is down")

    async def test_gemini_failure_is_an_error_not_a_reply(self):
        def broken(prompt):
            raise RuntimeError("Gemini credits depleted")
        with mock.patch.object(bot, "get_chat", lambda: SimpleNamespace(send_message=broken)):
            res = await self.post({"text": "plan my week"})
        self.assertEqual((res.status, await res.json()), (502, {"error": "Gemini credits depleted"}))


if __name__ == "__main__":
    unittest.main()
