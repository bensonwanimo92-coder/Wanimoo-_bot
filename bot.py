import asyncio
import logging
import os
import sqlite3
from typing import Any

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Fail fast with a useful message when Railway variables are missing.
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API_KEY = os.getenv("GOAL_API_KEY", "").strip()
if not BOT_TOKEN or not API_KEY:
    missing = [name for name, value in (
        ("TELEGRAM_BOT_TOKEN", BOT_TOKEN), ("GOAL_API_KEY", API_KEY)
    ) if not value]
    raise RuntimeError("Missing required Railway variable(s): " + ", ".join(missing))

API_URL = os.getenv("GOAL_LIVE_URL", "https://api.goal-api.com/v1/fixtures/live")
POLL_SECONDS = max(60, int(os.getenv("POLL_SECONDS", "120")))
DB_PATH = os.getenv("DATABASE_PATH", "wanimoo.db")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Telegram request URLs contain the bot token; never print them to production logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("wanimoo")


def database():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("CREATE TABLE IF NOT EXISTS subscribers(chat_id INTEGER PRIMARY KEY)")
    conn.execute("""CREATE TABLE IF NOT EXISTS matches(
        match_id TEXT PRIMARY KEY, home_score INTEGER NOT NULL,
        away_score INTEGER NOT NULL, last_minute TEXT)""")
    return conn


def pick(obj, *keys, default=None):
    if isinstance(obj, dict):
        for key in keys:
            if obj.get(key) is not None:
                return obj[key]
    return default


def normalize(raw: dict):
    fixture = raw.get("fixture") or {}
    match_id = pick(raw, "id", "fixtureId", "matchId", "uuid") or pick(fixture, "id", "fixtureId")
    score = raw.get("score") or {}
    home_score = pick(raw, "homeScore", default=pick(score, "home", "homeScore"))
    away_score = pick(raw, "awayScore", default=pick(score, "away", "awayScore"))
    if match_id is None or home_score is None or away_score is None:
        return None
    try:
        home_score, away_score = int(home_score), int(away_score)
    except (TypeError, ValueError):
        return None

    def name(side):
        team = raw.get(side + "Team") or raw.get(side) or {}
        return team if isinstance(team, str) else str(pick(team, "name", "shortName", default=side.title()))

    status = raw.get("status") or {}
    minute = pick(raw, "matchMinute", "minute", "elapsed",
                  default=pick(status, "minute", "elapsed", default=""))
    return {"id": str(match_id), "home": name("home"), "away": name("away"),
            "hs": home_score, "as": away_score, "minute": str(minute or "")}


def parse_matches(payload: Any):
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("fixtures") or payload.get("matches") or []
        if isinstance(rows, dict):
            rows = rows.get("data") or []
    else:
        rows = []
    return [m for m in (normalize(x) for x in rows if isinstance(x, dict)) if m] if isinstance(rows, list) else []


async def fetch(client: httpx.AsyncClient):
    response = await client.get(
        API_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"},
    )
    if response.status_code == 429:
        log.warning("GOAL API rate limit reached; increase POLL_SECONDS.")
        return []
    response.raise_for_status()
    log.info("GOAL API remaining quota: %s",
             response.headers.get("X-RateLimit-Remaining", "not supplied"))
    return parse_matches(response.json())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "⚽ Karibu Wanimoo Football Updates!\n"
        "/subscribe - jiunge na goal alerts\n"
        "/unsubscribe - simamisha alerts\n"
        "/live - angalia live matches\n/help - msaada"
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with database() as conn:
        conn.execute("INSERT OR IGNORE INTO subscribers VALUES (?)",
                     (update.effective_chat.id,))
    await update.effective_message.reply_text("✅ Umejiunga! Goal alerts zitakufikia hapa.")


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with database() as conn:
        conn.execute("DELETE FROM subscribers WHERE chat_id=?",
                     (update.effective_chat.id,))
    await update.effective_message.reply_text("Umeondolewa kwenye goal alerts.")


async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        matches = await fetch(context.application.bot_data["http"])
    except Exception:
        log.exception("Live fixtures request failed")
        await update.effective_message.reply_text(
            "Live scores hazipatikani kwa sasa. Jaribu tena baadaye."
        )
        return
    if not matches:
        await update.effective_message.reply_text(
            "Hakuna live matches zilizorudishwa na API kwa sasa."
        )
        return
    lines = ["⚽ LIVE MATCHES"]
    for match in matches[:20]:
        minute = f" ({match['minute']})" if match["minute"] else ""
        lines.append(
            f"🔴 {match['home']} {match['hs']} - {match['as']} {match['away']}{minute}"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def goal_monitor(app: Application):
    client = app.bot_data["http"]
    # Seed scores on first run so old goals are not announced as new.
    with database() as conn:
        first_cycle = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0

    while True:
        try:
            matches = await fetch(client)
            for match in matches:
                with database() as conn:
                    old = conn.execute(
                        "SELECT home_score,away_score FROM matches WHERE match_id=?",
                        (match["id"],),
                    ).fetchone()
                    conn.execute(
                        """INSERT INTO matches VALUES(?,?,?,?)
                        ON CONFLICT(match_id) DO UPDATE SET
                        home_score=excluded.home_score,
                        away_score=excluded.away_score,
                        last_minute=excluded.last_minute""",
                        (match["id"], match["hs"], match["as"], match["minute"]),
                    )
                    chats = [row[0] for row in
                             conn.execute("SELECT chat_id FROM subscribers")]

                if old is None or first_cycle:
                    continue
                if match["hs"] > old[0] or match["as"] > old[1]:
                    message = (
                        f"⚽⚽ GOAL! GOAL! GOAL!\n"
                        f"{match['home']} {match['hs']} - {match['as']} {match['away']}\n"
                        f"⏱ {match['minute'] or 'Live'}"
                    )
                    for chat_id in chats:
                        try:
                            await app.bot.send_message(chat_id=chat_id, text=message)
                        except Exception:
                            log.exception("Could not alert chat %s", chat_id)
            first_cycle = False
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            log.error("GOAL API HTTP error: %s", exc.response.status_code)
        except Exception:
            log.exception("Monitor cycle failed")

        await asyncio.sleep(POLL_SECONDS)


async def post_init(app: Application):
    timeout = httpx.Timeout(20.0, connect=10.0)
    limits = httpx.Limits(max_connections=5, max_keepalive_connections=2)
    app.bot_data["http"] = httpx.AsyncClient(timeout=timeout, limits=limits)
    # Use a managed asyncio task: PTB post_init runs before Application.start(),
    # so Application.create_task() here produces a lifecycle warning.
    app.bot_data["monitor_task"] = asyncio.create_task(
        goal_monitor(app), name="goal-monitor"
    )
    log.info("Wanimoo initialized; polling every %s seconds", POLL_SECONDS)


async def post_shutdown(app: Application):
    task = app.bot_data.get("monitor_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    client = app.bot_data.get("http")
    if client:
        await client.aclose()


def main():
    database().close()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("stop", unsubscribe))
    app.add_handler(CommandHandler("live", live))
    log.info("Wanimoo starting; polling interval=%s seconds", POLL_SECONDS)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
