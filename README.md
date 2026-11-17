# Wanimoo Football Bot ⚽

Telegram live-goal alerts and optional Facebook Page publishing powered by GOAL API and Meta Graph API.

## Features
- Automatic live fixture polling and Telegram goal alerts.\n- Optional Facebook Page posts for live-score snapshots, goals, and full-time results.
- Commands: /start, /help, /subscribe, /unsubscribe, /stop, /live.
- SQLite subscriber and score persistence.
- Configurable polling interval; default 120 seconds.
- API quota visibility from response headers.
- Secrets supplied through environment variables, never committed.

## Run locally
1. Install Python 3.11+.
2. Run `pip install -r requirements.txt`.
3. Set TELEGRAM_BOT_TOKEN and GOAL_API_KEY in your shell environment. Facebook publishing is optional; add FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN to enable it.
4. Run `python bot.py`.

## Railway 24/7 deployment
Deploy this repository as a worker. Set TELEGRAM_BOT_TOKEN and GOAL_API_KEY in Railway Variables. To enable Facebook, also set FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN. The Page token must have CREATE_CONTENT access and the Meta permissions pages_manage_posts, pages_read_engagement, and pages_show_list. Start command: `python bot.py`. Optional: set POLL_SECONDS=120. The included Procfile and railway.toml define the worker process.

## Telegram
A user must first open the bot and send /start, then /subscribe. Telegram bots cannot initiate private chats with users who have not started them.

## Free Start plan
This bot uses REST polling, not WebSockets. Default 120-second polling is about 720 requests per day if every poll is successful. Confirm your exact quota and competition coverage in your GOAL API dashboard. If HTTP 429 appears, increase POLL_SECONDS.

## Secrets and validation
Never paste tokens into chat or commit them. Add them only as deployment secrets. The first poll seeds existing scores to avoid announcing old goals as new. GOAL API fixture fields and coverage must be validated against the response from your account before calling the bot production-ready.
