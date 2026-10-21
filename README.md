# Pacer

Pacer is a scheduling assistant you message on Discord. It reads your Google Calendar and your Canvas and Gradescope deadlines, learns how long your work really takes, and books time blocks that fit.

Ask it things like "what's due this week?", "plan my ECE homework" or "can I go out Friday night?". When you say "I only got 40% done", it works out how much time is left and moves the rest.

## How it works

- Gemini reads your message and calls the tools in `scheduler_tools.py`.
- Google Calendar holds the schedule. Pacer's own blocks are tagged `[Flex]`, and they're the only events it will move.
- Supabase stores every task Pacer books, plus a multiplier for each category (`math_pset`, `ece_assignment` and so on). When you finish a task, the multiplier moves toward real time ÷ your guess. If your 2-hour psets usually take 2.5 hours, the next 2-hour guess gets a 2.5-hour block.
- Deadlines come from canvas-todo-sync, a separate tool, if it's installed. Pacer only reads them.

## Setup

You need Python 3.11 or newer.

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
   If canvas-todo-sync isn't in `~/canvas-todo-sync`, also set `CANVAS_TODO_HOME` to its folder. To talk to Pacer in one server channel without @mentioning it, set `PACER_CHANNEL_ID` to that channel's ID (with Developer Mode on, right-click the channel and pick Copy Channel ID).

`.env`, `credentials.json` and `token.json` are in `.gitignore`. Keep them out of git.

## Run

```
venv/Scripts/python bot.py
```

Then DM the bot, or @mention it in your server. In the `PACER_CHANNEL_ID` channel, if you set one, every message from you counts without a mention. It only answers the user in `DISCORD_OWNER_ID`. The first calendar request opens a Google sign-in page in your browser. You have 2 minutes to finish it, and it saves `token.json`.

The bot starts a new conversation each day, so it won't remember what you said yesterday. Your tasks and multipliers stay in Supabase.

Send `week` to get a picture of today and the next 6 days, with your classes, Pacer's blocks and assignment deadlines. It's drawn straight from your calendar and Canvas, so it doesn't use Gemini credits.

Questions about your schedule get a picture too. Ask about one day ("what does my day look like?", "when am I free Friday?") and Pacer lists your free time, counting 8 AM to 10 PM, and attaches a picture of that day. Ask about the week and it attaches the week picture.

Pacer can also add things to your calendar, once or repeating ("add my SCCN meeting, Tuesdays 3-4 PM"). Meetings and classes it adds are fixed, so it never moves them later. If you leave out something it needs, like the day, it asks.

To chat in the terminal instead, run `venv/Scripts/python assistant.py`.

## Run it on a server

The bot can run on any always-on Linux machine, such as an Azure for Students VM. These steps assume Ubuntu 24.04 and a user that can run `sudo`.

1. On the server, install the packages:
   ```
   sudo apt update && sudo apt install -y python3-venv
   git clone https://github.com/AbrahamPadua/Pacer.git ~/Pacer
   cd ~/Pacer
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   ```
2. Sign in to Google on your own computer first (see Run above). The server has no browser to show the sign-in page. Then copy the secret files from your computer to the server:
   ```
   scp .env credentials.json token.json <user>@<server>:~/Pacer/
   ```
3. For "what's due" answers, copy `canvas_todo_sync.py`, `requirements.txt` and `config.json` from canvas-todo-sync into `~/canvas-todo-sync` on the server, then set up its environment there:
   ```
   cd ~/canvas-todo-sync
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
   Pacer only uses the tool's read-only preview, so the server doesn't need its Microsoft sign-in or its schedule. Keep the To Do sync running on one machine only.
4. Make the secret files readable only by you:
   ```
   chmod 600 ~/Pacer/.env ~/Pacer/token.json ~/Pacer/credentials.json ~/canvas-todo-sync/config.json
   ```
5. Install the bot as a service, so it starts at boot and restarts after a crash:
   ```
   sudo tee /etc/systemd/system/pacer.service > /dev/null <<EOF
   [Unit]
   Description=Pacer Discord bot
   After=network-online.target
   Wants=network-online.target

   [Service]
   User=$USER
   WorkingDirectory=$HOME/Pacer
   ExecStart=$HOME/Pacer/venv/bin/python -u bot.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   EOF
   sudo systemctl daemon-reload
   sudo systemctl enable --now pacer
   ```
   `journalctl -u pacer -f` shows the log. You should see `Logged in as` within a few seconds.
6. Stop any other copy of the bot, such as one on your laptop. With two copies running, every message gets two answers and your calendar gets booked twice.

After you push changes, update the server with:

```
cd ~/Pacer && git pull && venv/bin/pip install -r requirements.txt && sudo systemctl restart pacer
```

If Google signs you out, the bot replies that the sign-in expired. Sign in again on your computer, copy the new `token.json` to the server, and run `sudo systemctl restart pacer`.

## Tests

```
venv/Scripts/python -m unittest
```

The tests use fake Supabase, Calendar and Gemini objects, so they never touch your real data or spend credits.
