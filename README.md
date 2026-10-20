# Pacer

Pacer is a scheduling assistant you message on Discord. It reads your Google Calendar and your Canvas and Gradescope deadlines, learns how long your work really takes, and books time blocks that fit.

Ask it things like "what's due this week?", "plan my ECE homework" or "can I go out Friday night?". When you say "I only got 40% done", it works out how much time is left and moves the rest.

## How it works

- Gemini reads your message and calls the tools in `scheduler_tools.py`.
- Google Calendar holds the schedule. Pacer's own blocks are tagged `[Flex]`, and they're the only events it will move.
- Supabase stores every task Pacer books, plus a multiplier for each category (`math_pset`, `ece_assignment` and so on). When you finish a task, the multiplier moves toward real time ÷ your guess. If your 2-hour psets usually take 2.5 hours, the next 2-hour guess gets a 2.5-hour block.
- Deadlines come from canvas-todo-sync, a separate tool, if it's installed. Pacer only reads them.

## Setup

You need Python 3.10 or newer.

1. Install the packages:
   ```
   python -m venv venv
   venv/Scripts/python -m pip install -r requirements.txt
   ```
   On macOS or Linux, use `venv/bin/python` instead of `venv/Scripts/python`, here and below.
2. Supabase: create a project, open the SQL Editor and run `schema.sql`. Copy the project URL and the secret key (`sb_secret_...`) from Project Settings > API Keys.
3. Google Calendar: in Google Cloud Console, enable the Google Calendar API and create an OAuth client of type Desktop app. Download it as `credentials.json` into this folder. Then go to Google Auth Platform > Audience and publish the app. While it's in Testing, Google signs you out every 7 days.
4. Gemini: create an API key in [Google AI Studio](https://aistudio.google.com/apikey). The project needs billing or prepaid credits.
5. Discord: create an application in the [Developer Portal](https://discord.com/developers/applications). On the Bot tab, copy the token and turn on Message Content Intent. Under OAuth2 > URL Generator, tick `bot`, Send Messages and Read Message History, and use the link to add the bot to a server. For your user ID, turn on Developer Mode in Discord's settings, then right-click your name and pick Copy User ID.
6. Create a `.env` file in this folder:
   ```
   SUPABASE_URL=...
   SUPABASE_KEY=sb_secret_...
   GEMINI_API_KEY=...
   DISCORD_BOT_TOKEN=...
   DISCORD_OWNER_ID=...
   ```
   If canvas-todo-sync isn't in `~/canvas-todo-sync`, also set `CANVAS_TODO_HOME` to its folder.

`.env`, `credentials.json` and `token.json` are in `.gitignore`. Keep them out of git.

## Run

```
venv/Scripts/python bot.py
```

Then DM the bot, or @mention it in your server. It only answers the user in `DISCORD_OWNER_ID`. The first calendar request opens a Google sign-in page in your browser. You have 2 minutes to finish it, and it saves `token.json`.

The bot starts a new conversation each day, so it won't remember what you said yesterday. Your tasks and multipliers stay in Supabase.

To chat in the terminal instead, run `venv/Scripts/python assistant.py`.

## Tests

```
venv/Scripts/python -m unittest
```

The tests use fake Supabase, Calendar and Gemini objects, so they never touch your real data or spend credits.
