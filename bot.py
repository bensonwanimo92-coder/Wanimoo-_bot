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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(matches)")}
    if "status" not in columns:
        conn.execute("ALTER TABLE matches ADD COLUMN status TEXT NOT NULL DEFAULT ''")
    return conn


def pick(obj, *keys, default=None):
    if isinstance(obj, dict):
        for key in keys:
            if obj.get(key) is not None:
                return obj[key]
    return default


def normalize(raw: dict):
    # GOAL API v1 returns fixture rows in the response's "data" array.
    fixture = raw.get("fixture") or raw
    match_id = (
        pick(raw, "id", "fixtureId", "matchId", "uuid")
        or pick(fixture, "id", "fixtureId", "matchId")
    )
    score = raw.get("score") or fixture.get("score") or {}
    home_score = pick(
        raw, "homeScore", "homeTeamScore",
        default=pick(fixture, "homeScore", "homeTeamScore",
                     default=pick(score, "home", "homeScore"))
    )
    away_score = pick(
        raw, "awayScore", "awayTeamScore",
        default=pick(fixture, "awayScore", "awayTeamScore",
                     default=pick(score, "away", "awayScore"))
    )
    if match_id is None or home_score is None or away_score is None:
        return None
    try:
        home_score, away_score = int(home_score), int(away_score)
    except (TypeError, ValueError):
        return None

    def name(side):
        team = (
            raw.get(side + "Team") or fixture.get(side + "Team")
            or raw.get(side) or fixture.get(side) or {}
        )
        return team if isinstance(team, str) else str(
            pick(team, "name", "shortName", default=side.title())
        )

    status = raw.get("status") or fixture.get("status") or {}
    if isinstance(status, dict):
        status_text = str(pick(status, "short", "code", "name", "long", "description",
                               "status", default="")).strip()
    else:
        status_text = str(status or "").strip()
    minute = pick(
        raw, "matchMinute", "minute", "elapsed", "matchStatus",
        default=pick(fixture, "matchMinute", "minute", "elapsed", "matchStatus",
                     default=pick(status, "minute", "elapsed", default=""))
    )
    return {"id": str(match_id), "home": name("home"), "away": name("away"),
            "hs": home_score, "as": away_score, "minute": str(minute or ""),
            "status": status_text}


def parse_matches(payload: Any):
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data")
        if rows is None:
            rows = payload.get("fixtures") or payload.get("matches") or []
        # Some API gateways wrap the fixture list one level deeper.
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("fixtures") or rows.get("matches") or []
    else:
        rows = []
    if not isinstance(rows, list):
        return []
    return [match for match in (normalize(row) for row in rows
                                if isinstance(row, dict)) if match]


async def fetch(client: httpx.AsyncClient):
    response = await client.get(
        API_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"},
    )
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "not supplied")
        log.warning("GOAL API rate limit/quota reached; Retry-After=%s", retry_after)
        return []
    if response.status_code == 204 or not response.content:
        log.info("GOAL API returned no live fixtures (HTTP %s).", response.status_code)
        return []
    response.raise_for_status()
    remaining = response.headers.get("X-RateLimit-Remaining", "not supplied")
    limit = response.headers.get("X-RateLimit-Limit", "not supplied")
    log.info("GOAL API quota: %s/%s requests remaining", remaining, limit)
    payload = response.json()
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError("GOAL API reported success=false: " +
                           str(payload.get("error") or payload.get("message") or "unknown API error"))
    matches = parse_matches(payload)
    if not matches:
        if isinstance(payload, dict):
            rows = payload.get("data")
            row_count = len(rows) if isinstance(rows, list) else "not a list"
            row_keys = sorted(rows[0].keys()) if isinstance(rows, list) and rows and isinstance(rows[0], dict) else []
            log.info("GOAL API parse diagnostic: top-level keys=%s, data_rows=%s, first_row_keys=%s",
                     sorted(payload.keys()), row_count, row_keys)
        else:
            log.info("GOAL API returned payload type=%s with no parseable fixtures",
                     type(payload).__name__)
    else:
        log.info("GOAL API live fixtures parsed: %s", len(matches))
    return matches


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


def is_finished(status: str) -> bool:
    value = (status or "").strip().upper().replace("-", "_")
    return value in {"FT", "AET", "PEN", "FINISHED", "FULL_TIME",
                     "FULL TIME", "ENDED", "MATCH FINISHED"} or "FINISHED" in value


async def publish_facebook(app: Application, match: dict, event_type: str):
    from facebook_publisher import publish_once

    if event_type == "live":
        key = f"live:{match['id']}"
        message = (
            f"🔴 LIVE MATCH\n\n{match['home']} {match['hs']} - "
            f"{match['as']} {match['away']}\n"
            f"⏱ {match['minute'] or 'Live'}\n\n"
            f"⚽ WANIMOO FOOTBALL LIVE"
        )
    elif event_type == "goal":
        key = f"goal:{match['id']}:{match['hs']}-{match['as']}"
        message = (
            f"⚽ GOAL! GOAL! GOAL!\n\n{match['home']} {match['hs']} - "
            f"{match['as']} {match['away']}\n"
            f"⏱ {match['minute'] or 'Live'}\n\n"
            f"⚽ WANIMOO FOOTBALL LIVE"
        )
    else:
        key = f"fulltime:{match['id']}:{match['hs']}-{match['as']}"
        message = (
            f"🏁 FULL-TIME RESULT\n\n{match['home']} {match['hs']} - "
            f"{match['as']} {match['away']}\n\n"
            f"⚽ WANIMOO FOOTBALL LIVE"
        )
    await publish_once(app.bot_data["http"], key, message)


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
                        "SELECT home_score,away_score,status FROM matches WHERE match_id=?",
                        (match["id"],),
                    ).fetchone()
                    conn.execute(
                        """INSERT INTO matches(match_id,home_score,away_score,last_minute,status)
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(match_id) DO UPDATE SET
                        home_score=excluded.home_score,
                        away_score=excluded.away_score,
                        last_minute=excluded.last_minute,
                        status=excluded.status""",
                        (match["id"], match["hs"], match["as"], match["minute"],
                         match["status"]),
                    )
                    chats = [row[0] for row in
                             conn.execute("SELECT chat_id FROM subscribers")]

                # Publish a live-score snapshot once per fixture. Event keys make
                # repeated polling/restarts idempotent for each Facebook post.
                if not is_finished(match["status"]):
                    try:
                        await publish_facebook(app, match, "live")
                    except Exception:
                        log.exception("Facebook live-score publishing failed")

                if old is not None and (match["hs"] > old[0] or match["as"] > old[1]):
                    try:
                        await publish_facebook(app, match, "goal")
                    except Exception:
                        log.exception("Facebook goal publishing failed")

                if is_finished(match["status"]):
                    try:
                        await publish_facebook(app, match, "fulltime")
                    except Exception:
                        log.exception("Facebook full-time publishing failed")

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
