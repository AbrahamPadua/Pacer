import io
import os
import re
import asyncio
import datetime
import secrets
import discord
from aiohttp import web
from assistant import get_chat, pop_failed_writes, pop_image_request
from scheduler_tools import TZ
from week_image import COVER, day_png, week_png

OWNER_ID = int(os.environ["DISCORD_OWNER_ID"])
PACER_CHANNEL_ID = int(os.environ.get("PACER_CHANNEL_ID") or 0)  # optional; 0 matches no channel
COVER_TOKEN = os.environ.get("COVER_TOKEN", "")  # optional; empty serves no cover picture
COVER_PORT = 8080

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
lock = asyncio.Lock()  # one shared chat per day, so handle one message at a time


async def picture(request):
    """Today sized for the phone's cover screen, or the week picture, for whoever has the secret URL."""
    # The token is the URL's only lock: compare in constant time, as bytes so odd characters can't raise
    if not secrets.compare_digest(request.match_info["token"].encode(), COVER_TOKEN.encode()):
        raise web.HTTPNotFound()
    async with lock:  # don't read the calendar while a chat reply is using it
        png = await asyncio.to_thread(week_png) if request.match_info["kind"] == "week" else \
            await asyncio.to_thread(day_png, datetime.datetime.now(TZ).date(), COVER)
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})


def cover_app():
    app = web.Application()
    app.router.add_get("/{kind:cover|week}/{token}.png", picture)
    return app


@bot.event
async def setup_hook():  # runs once before Discord connects, unlike on_ready
    if COVER_TOKEN:
        runner = web.AppRunner(cover_app())
        await runner.setup()
        # Localhost only: Caddy in front adds HTTPS, so the token never crosses the network in plain text
        await web.TCPSite(runner, "127.0.0.1", COVER_PORT).start()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}. DM me or @mention me to manage your calendar.")


@bot.event
async def on_message(message):
    # Only you can touch your calendar, and only when you're talking to the bot: a DM, a mention, or the Pacer channel
    if message.author.id != OWNER_ID:
        return
    if not (isinstance(message.channel, discord.DMChannel) or bot.user in message.mentions
            or message.channel.id == PACER_CHANNEL_ID):
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
            pop_image_request()  # drop a request and failures left over from a reply that failed midway
            pop_failed_writes()
            # send_message blocks while Gemini calls Calendar/Supabase; keep Discord's event loop free
            response = await asyncio.to_thread(get_chat().send_message, prompt)
            text = response.text or "(no reply)"
            # Shown from what the tools reported, whatever Gemini's own wording says
            if failed := pop_failed_writes():
                text += "\n\n" + "\n".join(f"Not saved: {f}" for f in failed)
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
