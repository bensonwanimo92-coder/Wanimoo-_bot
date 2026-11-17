"""Optional Facebook Page publisher for Wanimoo Football Live.

Configure FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN in Railway.
The publisher uses Meta's official Page Feed endpoint and stores event keys
in the same SQLite database as the Telegram bot to avoid duplicate posts.
"""
import logging
import os
import sqlite3
from datetime import datetime, timezone

import httpx

log = logging.getLogger("wanimoo.facebook")
PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "").strip()
PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
GRAPH_VERSION = os.getenv("FACEBOOK_GRAPH_VERSION", "v27.0").strip()
DB_PATH = os.getenv("DATABASE_PATH", "wanimoo.db")


def _connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS facebook_events(
            event_key TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            post_id TEXT,
            error TEXT,
            attempted_at TEXT NOT NULL
        )"""
    )
    return conn


async def publish_once(client: httpx.AsyncClient, event_key: str, message: str) -> bool:
    """Publish an event once. Returns False when not configured or already attempted."""
    if not PAGE_ID or not PAGE_TOKEN:
        return False

    now = datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO facebook_events
               (event_key, state, attempted_at) VALUES (?, 'sending', ?)""",
            (event_key, now),
        )
        if cursor.rowcount == 0:
            return False

    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PAGE_ID}/feed"
    try:
        response = await client.post(
            url,
            data={"message": message, "access_token": PAGE_TOKEN},
        )
        payload = response.json() if response.content else {}
        response.raise_for_status()
        post_id = payload.get("id") if isinstance(payload, dict) else None
        if not post_id:
            raise RuntimeError("Meta API response did not include a published post ID")
        with _connection() as conn:
            conn.execute(
                "UPDATE facebook_events SET state='published', post_id=?, error=NULL WHERE event_key=?",
                (str(post_id), event_key),
            )
        log.info("Facebook post published for event %s (post id %s)", event_key, post_id)
        return True
    except Exception as exc:
        # Do not auto-retry an ambiguous network failure: Meta may have accepted
        # the post before the connection failed, and a retry could duplicate it.
        safe_error = str(exc)
        if PAGE_TOKEN:
            safe_error = safe_error.replace(PAGE_TOKEN, "[REDACTED]")
        with _connection() as conn:
            conn.execute(
                "UPDATE facebook_events SET state='failed', error=? WHERE event_key=?",
                (safe_error[:1000], event_key),
            )
        log.error("Facebook publishing failed for event %s: %s", event_key, safe_error)
        return False
