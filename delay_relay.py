"""Delay relay: mirrors premium Discord alert channels into free channels,
15 minutes later, verbatim.

Stdlib-only (no `requests`) so this file has zero pip-install step when it
runs via GitHub Actions.

This is a synced copy. The tested source of truth, with its unit tests, lives
at `tracker/delay_relay.py` in the private `apex-bot` repo -- this file was
copied out to a public repo solely so its scheduled workflow can run on
GitHub Actions' free/unlimited public-repo minutes (the private repo's paid
quota can't sustain a 5-minute cadence). See that repo's
`docs/superpowers/specs/2026-09-22-discord-delay-relay-design.md` for the
full design. This module carries no proprietary trading logic -- only
Discord channel IDs and generic relay/dedup logic.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://discord.com/api/v10"
DELAY_SECONDS = 15 * 60
MAX_AGE_SECONDS = 2 * 60 * 60  # cap how old a message can be and still get relayed; keeps first-run backfill bounded and the delay footer honest
FETCH_LIMIT = 50

# (timeframe, premium_channel_id, free_channel_id)
PAIRS = [
    ("30s", "1507159472156704908", "1551967531114168391"),
    ("1m", "1503837558017360034", "1551967578279121009"),
    ("2m", "1503838210755657828", "1551967608503279696"),
    ("3m", "1503838275964768418", "1551967630737412158"),
    ("5m", "1503838319019167885", "1551967653009035304"),
    ("15m", "1504811070110437386", "1551967672491712542"),
]

_SRC_MARKER_RE = re.compile(r"src:(\d+)\s*$")


def message_age_seconds(iso_timestamp: str, now: datetime) -> float:
    """Seconds between `now` and a Discord message's ISO-8601 `timestamp` field."""
    posted_at = datetime.fromisoformat(iso_timestamp)
    return (now - posted_at).total_seconds()


def extract_relayed_source_ids(free_channel_messages: list[dict]) -> set[str]:
    """Pull the `src:<id>` markers this relay already appended, from a free
    channel's recent message history."""
    ids = set()
    for msg in free_channel_messages:
        match = _SRC_MARKER_RE.search(msg.get("content", ""))
        if match:
            ids.add(match.group(1))
    return ids


def build_relay_text(content: str, source_id: str) -> str:
    return f"{content}\n-# ⏱ ~15 min delayed · src:{source_id}"


def select_relay_candidates(
    premium_messages: list[dict],
    relayed_ids: set[str],
    now: datetime,
    delay_seconds: float = DELAY_SECONDS,
    max_age_seconds: float = MAX_AGE_SECONDS,
) -> list[dict]:
    """Messages eligible to relay: not already relayed, old enough, not too
    old, has text content. Returns oldest-eligible-first (input is
    newest-first, as the Discord API returns it)."""
    candidates = []
    for msg in premium_messages:
        if msg["id"] in relayed_ids:
            continue
        if not msg.get("content"):
            continue
        age = message_age_seconds(msg["timestamp"], now)
        if age < delay_seconds:
            continue
        if age > max_age_seconds:
            continue
        candidates.append(msg)
    candidates.reverse()
    return candidates


def _discord_request(token: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bot {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_recent_messages(token: str, channel_id: str, limit: int = FETCH_LIMIT) -> list[dict]:
    return _discord_request(token, "GET", f"/channels/{channel_id}/messages?limit={limit}")


def post_message(token: str, channel_id: str, content: str) -> None:
    _discord_request(token, "POST", f"/channels/{channel_id}/messages", {"content": content})


def relay_channel_pair(token: str, tf: str, premium_id: str, free_id: str) -> int:
    """Relay all eligible messages for one timeframe pair. Returns count relayed."""
    free_messages = fetch_recent_messages(token, free_id)
    relayed_ids = extract_relayed_source_ids(free_messages)
    premium_messages = fetch_recent_messages(token, premium_id)
    candidates = select_relay_candidates(premium_messages, relayed_ids, datetime.now(timezone.utc))
    for i, msg in enumerate(candidates):
        if i > 0:
            time.sleep(1)
        post_message(token, free_id, build_relay_text(msg["content"], msg["id"]))
    return len(candidates)


def main() -> None:
    token = os.environ["DISCORD_BOT_TOKEN"]
    any_failed = False
    for tf, premium_id, free_id in PAIRS:
        try:
            count = relay_channel_pair(token, tf, premium_id, free_id)
            print(f"[{tf}] relayed {count}")
        except Exception as exc:
            print(f"[{tf}] FAILED: {exc}")
            any_failed = True
    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
