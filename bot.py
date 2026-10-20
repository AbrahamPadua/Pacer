import os
import asyncio
import datetime
import discord
from assistant import get_chat

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

    # The system prompt only knows when today's chat started, so send the real time with every message
    prompt = f"[Current time: {datetime.datetime.now().isoformat()}] {message.content}"
    async with lock, message.channel.typing():
        try:
            # send_message blocks while Gemini calls Calendar/Supabase; keep Discord's event loop free
            response = await asyncio.to_thread(get_chat().send_message, prompt)
            text = response.text or "(no reply)"
        except Exception as e:
            text = f"Something broke: {e}"

    for i in range(0, len(text), 2000):  # Discord's message length limit
        await message.reply(text[i:i + 2000])


if __name__ == "__main__":
    bot.run(os.environ["DISCORD_BOT_TOKEN"])
