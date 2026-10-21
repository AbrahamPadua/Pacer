import io
import os
import re
import asyncio
import datetime
import discord
from assistant import get_chat, pop_image_request
from scheduler_tools import TZ
from week_image import day_png, week_png

OWNER_ID = int(os.environ["DISCORD_OWNER_ID"])

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
lock = asyncio.Lock()  # one shared chat per day, so handle one message at a time


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}. DM me or @mention me to manage your calendar.")


@bot.event
async def on_message(message):
    # Only you can touch your calendar, and only when you're talking to the bot
    if message.author.id != OWNER_ID:
        return
    if not (isinstance(message.channel, discord.DMChannel) or bot.user in message.mentions):
        return

    # "week" (or "@Pacer week") sends the week image straight from the calendar, without asking Gemini
    if re.sub(r"<@!?\d+>", "", message.content).strip().lower() == "week":
        async with lock, message.channel.typing():
            try:
                reply = {"file": discord.File(io.BytesIO(await asyncio.to_thread(week_png)), "week.png")}
            except Exception as e:
                reply = {"content": f"Something broke: {e}"}
        await message.reply(**reply)
        return

    # The system prompt only knows when today's chat started, so send the real time with every message
    prompt = f"[Current time: {datetime.datetime.now(TZ).isoformat()}] {message.content}"
    image, filename = None, None
    async with lock, message.channel.typing():
        try:
            pop_image_request()  # drop a request left over from a reply that failed midway
            # send_message blocks while Gemini calls Calendar/Supabase; keep Discord's event loop free
            response = await asyncio.to_thread(get_chat().send_message, prompt)
            text = response.text or "(no reply)"
            if req := pop_image_request():  # Gemini called show_week_image or show_day_image
                kind, day = req
                try:
                    image = await asyncio.to_thread(week_png) if kind == "week" else await asyncio.to_thread(day_png, day)
                    filename = f"{kind}.png"
                except Exception as e:
                    print(f"{kind} image failed: {e}")
                    text += f"\n\n(Couldn't draw your {kind}: {e})"
        except Exception as e:
            text = f"Something broke: {e}"

    chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)]  # Discord's message length limit
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1  # the picture goes under the end of the answer
        await message.reply(chunk, file=discord.File(io.BytesIO(image), filename) if last and image else None)


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_BOT_TOKEN"])
