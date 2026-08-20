# feedback-bot

A Telegram feedback bot. Users write to the bot, their messages land in the owner's private
chat, and the owner answers by **replying** to them — the reply reaches the user as a message
from the bot. The owner's account is never exposed.

Built to collect feedback for several projects at once: each project gets its own deep link,
so every incoming message arrives already tagged.

## How it works

- A user sends anything — text, photo, video, voice, document.
- The owner receives a header (name, `@username`, id, project) followed by the message itself.
- Replying to either of those two messages delivers the answer to the user, quoting their
  original message.
- Everything is stored in SQLite: profiles, the full message log, and the message-to-author
  mapping used for replies.

Answers are sent as *copies*, not forwards, so the owner's profile stays hidden.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. `cp .env.example .env` and fill in `BOT_TOKEN`.
3. To find your own id: start the bot, send it `/id`, put the number into `ADMIN_IDS`, restart.

Run locally:

```bash
./run.ps1
```

Or manually:

```bash
py -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt; .\.venv\Scripts\python.exe bot.py
```

Run on a server:

```bash
docker compose up -d --build
```

## Projects

The project list lives in `config.py` as the `PROJECTS` dict. Each key becomes a deep link:

```
https://t.me/YOUR_BOT?start=wordle
```

Following that link tags the user with the project automatically — put the link in the
channel description or inside the project itself. Without a deep link the bot offers a
keyboard to pick one. The project shows up in every message header and in `/stats`.

## Owner commands

| Command | What it does |
|---|---|
| `/stats` | users and message counts, broken down by project |
| `/users [N]` | last N users |
| `/say <user_id> text` | message a user without replying |
| `/ban` | reply to a message (or `/ban <user_id>`) — that user stops getting through |
| `/unban <user_id>` | lift the ban |
| `/export` | dump the whole feedback log as CSV |
| `/id` | id of the current chat |

## Configuration

All of it lives in `.env`:

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | token from BotFather |
| `ADMIN_IDS` | your Telegram id; comma-separated for several owners |
| `DB_PATH` | database file, defaults to `feedback.db` |
| `RATE_LIMIT_PER_MINUTE` | per-user message cap, defaults to 20 |

Greeting and confirmation texts sit at the bottom of `config.py`.

## Tests

`tests/test_flow.py` feeds real `Update` objects through the dispatcher with `Bot.__call__`
replaced, so the whole flow is exercised without a token and without network access:

```bash
python tests/test_flow.py
```

It covers deep-link tagging, delivery to the owner, replies, `/stats`, ban and unban, `/say`,
rate limiting, attachments, and the CSV export.

## Deployment

`.github/workflows/deploy.yml` runs the tests on every push and pull request, then deploys
`main` to a VPS over SSH: it copies the sources, writes `.env` from the repository secrets and
runs `docker compose up -d --build`. Until the secrets are filled in, the deploy job reports
itself as skipped instead of failing.

Required repository secrets (Settings → Secrets and variables → Actions):

| Secret | Meaning |
|---|---|
| `VPS_HOST` | server address |
| `SSH_USERNAME` | SSH user |
| `SSH_PASSWORD` | SSH password |
| `BOT_TOKEN` | token from BotFather |
| `ADMIN_IDS` | your Telegram id |
| `SSH_PORT` | optional, defaults to 22 |
| `DEPLOY_PATH` | optional, defaults to `~/feedback-bot` |

The server needs Docker with the Compose plugin. The database is kept in `./data` next to the
compose file on the server, so it survives redeploys.

If the SSH account is not in the `docker` group, the deploy falls back to `sudo`, using
`SUDO_PASSWORD` or the SSH password. After the container starts, the job prints the bot log and
fails if the container is not running.

## Notes

- The owner has to press `/start` once: Telegram does not let bots message people first.
- The bot talks to users in Russian; the strings are in `config.py` and `bot.py`.
