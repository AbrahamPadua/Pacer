import os
import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from unittest import mock

# Dummy values win over .env (load_dotenv never overrides), so tests can't touch real data or spend credits
os.environ.update(SUPABASE_URL="https://example.supabase.co", SUPABASE_KEY="sb_secret_test",
                  GEMINI_API_KEY="test", DISCORD_OWNER_ID="111")
import discord
import assistant
import bot

OWNER, STRANGER, BOT_USER = SimpleNamespace(id=111), SimpleNamespace(id=222), SimpleNamespace(id=999)


class FakeServerChannel:
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

    async def test_leftover_picture_request_is_dropped(self):
        assistant._week_image_requested = True  # a reply that failed after Gemini called the tool
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


if __name__ == "__main__":
    unittest.main()
