"""Weekly performance summary: reconstructs trades from the 6 raw Discord
alert channels' message history and posts a pooled weekly recap.

This is a synced copy. The tested source of truth, with its unit tests, lives
at `tracker/weekly_summary.py` in the private `apex-bot` repo -- this file was
copied out to this public repo for the same reason as `delay_relay.py`:
GitHub Actions minutes are free/unlimited here, billed on the private repo.
See that repo's `docs/superpowers/specs/2026-09-25-weekly-summary-design.md`
for the full design. This module carries no proprietary trading logic --
only Discord channel IDs and generic parsing/aggregation logic.

Fully independent from `delay_relay.py` -- no shared code or state, even
though both read the same channel IDs (duplicated on purpose, see the
design spec's explicit non-goal on this).

Stdlib-only (no `requests`) so this file has zero pip-install step when it
runs via GitHub Actions.
"""
from __future__ import annotations

import re

_ENTRY_RE = re.compile(
    r"^[🟢🔴]\s*\*\*(LONG|SHORT)\s+#(\d+)\*\*.*?\|\s*Grade:\s*\*\*([^*]+)\*\*\s*\|\s*Entry:\s*([\d.]+)"
)
_TARGET_HIT_RE = re.compile(
    r"^🎯\s*\*\*TARGET HIT\s+#(\d+)\*\*.*?\|\s*(LONG|SHORT)\s+from\s+([\d.]+)"
)
_STOP_HIT_RE = re.compile(
    r"^🛑\s*\*\*STOP HIT\s+#(\d+)\*\*.*?\|\s*(LONG|SHORT)\s+from\s+([\d.]+)"
)
_BE_HIT_RE = re.compile(
    r"^⚡\s*\*\*BE HIT\s+#(\d+)\*\*.*?\|\s*(LONG|SHORT)\s+from\s+([\d.]+)"
)


def parse_entry(content: str) -> dict | None:
    match = _ENTRY_RE.match(content)
    if not match:
        return None
    direction, number, grade, entry_price = match.groups()
    return {
        "direction": direction,
        "number": number,
        "grade": grade,
        "entry_price": float(entry_price),
    }


def parse_resolution(content: str) -> dict | None:
    for outcome, pattern in (("win", _TARGET_HIT_RE), ("loss", _STOP_HIT_RE), ("be", _BE_HIT_RE)):
        match = pattern.match(content)
        if match:
            number, direction, entry_price = match.groups()
            return {
                "outcome": outcome,
                "direction": direction,
                "number": number,
                "entry_price": float(entry_price),
            }
    return None


def reconstruct_trades(messages: list[dict], timeframe: str) -> list[dict]:
    """messages must be chronologically ascending (oldest first)."""
    open_trades: dict[tuple[str, str, float], dict] = {}
    trades = []
    for msg in messages:
        content = msg.get("content", "")
        entry = parse_entry(content)
        if entry is not None:
            key = (entry["direction"], entry["number"], entry["entry_price"])
            open_trades[key] = {"grade": entry["grade"], "entry_time": msg["timestamp"]}
            continue
        resolution = parse_resolution(content)
        if resolution is None:
            continue
        key = (resolution["direction"], resolution["number"], resolution["entry_price"])
        opened = open_trades.pop(key, None)
        if opened is None:
            continue
        trades.append({
            "timeframe": timeframe,
            "outcome": resolution["outcome"],
            "entry_time": opened["entry_time"],
            "grade": opened["grade"],
        })
    return trades


from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

# Real trading-session boundaries, Europe/Berlin local time. Overnight wraps
# midnight (23:00-02:00); a trade landing in the 23:00-00:00 "market closed"
# gap folds into Overnight rather than raising.
SESSION_ORDER = ["Tokyo", "London", "New York", "Overnight"]
_SESSION_MINUTES = [
    ("Tokyo", 2 * 60, 9 * 60),
    ("London", 9 * 60, 15 * 60 + 30),
    ("New York", 15 * 60 + 30, 23 * 60),
]


def session_for_time(hour: int, minute: int) -> str:
    total_minutes = hour * 60 + minute
    if total_minutes >= 23 * 60 or total_minutes < 2 * 60:
        return "Overnight"
    for name, start, end in _SESSION_MINUTES:
        if start <= total_minutes < end:
            return name
    raise ValueError(f"time {hour:02d}:{minute:02d} not in any session")

GRADE_ORDER = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-"]

TF_ORDER = ["30s", "1m", "2m", "3m", "5m", "15m"]


def to_local(iso_ts: str) -> datetime:
    return datetime.fromisoformat(iso_ts).astimezone(BERLIN)


def grade_sort_key(grade: str) -> tuple:
    if grade in GRADE_ORDER:
        return (0, GRADE_ORDER.index(grade))
    return (1, grade)


def tally(trades: list[dict], key_fn) -> dict:
    result: dict = {}
    for t in trades:
        k = key_fn(t)
        bucket = result.setdefault(k, {"win": 0, "loss": 0, "be": 0})
        bucket[t["outcome"]] += 1
    return result


def format_pct(wins: int, losses: int) -> str:
    total = wins + losses
    pct = round(wins / total * 100, 1) if total else 0.0
    if pct == int(pct):
        return f"{int(pct)}%"
    return f"{pct}%"


def format_net(wins: int, losses: int) -> str:
    return f"{wins - losses:+d}R"


def win_rate(bucket: dict) -> float:
    total = bucket["win"] + bucket["loss"]
    return bucket["win"] / total if total else -1.0


def trade_count(bucket: dict) -> int:
    return bucket["win"] + bucket["loss"] + bucket["be"]


def net(bucket: dict) -> int:
    return bucket["win"] - bucket["loss"]


def pick_best(entries: list[tuple], tie_break_order: list | None = None):
    def sort_key(item):
        key, bucket = item
        if tie_break_order is not None:
            final = tie_break_order.index(key) if key in tie_break_order else len(tie_break_order)
        else:
            final = key
        return (-win_rate(bucket), -trade_count(bucket), -net(bucket), final)
    return min(entries, key=sort_key)[0]


from datetime import timedelta


def format_row(label: str, wins: int, losses: int, label_width: int = 12) -> str:
    return f"{label:<{label_width}}{wins:>2}W {losses:>2}L   WR:{format_pct(wins, losses):>7}   Net: {format_net(wins, losses)}"


def format_hour_row(hour: int, session: str, wins: int, losses: int) -> str:
    return f"{hour:02d}:00  {session:<12}{wins:>2}W {losses:>2}L   WR:{format_pct(wins, losses):>7}   Net: {format_net(wins, losses)}"


def select_best_hours(by_hour: dict) -> list[int]:
    qualifying = [
        (h, b) for h, b in by_hour.items()
        if trade_count(b) >= 3 and (b["win"] + b["loss"]) > 0
    ]
    ranked = sorted(
        qualifying,
        key=lambda item: (-win_rate(item[1]), -trade_count(item[1]), -net(item[1]), item[0]),
    )
    selected = [h for h, _ in ranked[:10]]
    return sorted(selected)


def _current_report_window(now_berlin: datetime) -> tuple[datetime, datetime]:
    monday = (now_berlin - timedelta(days=now_berlin.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday, now_berlin


def build_weekly_report(trades: list[dict], week_start: datetime, week_end: datetime) -> str:
    wins = sum(1 for t in trades if t["outcome"] == "win")
    losses = sum(1 for t in trades if t["outcome"] == "loss")
    bes = sum(1 for t in trades if t["outcome"] == "be")
    total = wins + losses + bes

    for t in trades:
        t["_local_entry"] = to_local(t["entry_time"])

    lines = [
        f"📈 Weekly Performance Stats — {week_start:%b %d} — {week_end:%b %d, %Y}",
        "🕐 All times shown in CET/CEST",
        "",
        f"⏱️ {total} trades",
        f"✅ Wins    {wins}   +{wins}.0R",
        f"❌ Losses  {losses}   -{losses}.0R",
        f"⚡️ BE       {bes}",
        "─────────────────────",
        f"📈 Net        {format_net(wins, losses)}",
        f"🎯 Win Rate   {format_pct(wins, losses)}",
        "",
        "",
        "🌐 By Session",
    ]
    by_session = tally(trades, lambda t: session_for_time(t["_local_entry"].hour, t["_local_entry"].minute))
    session_names = SESSION_ORDER
    session_entries = [
        (name, by_session[name]) for name in session_names
        if name in by_session and (by_session[name]["win"] + by_session[name]["loss"]) > 0
    ]
    for name, bucket in session_entries:
        lines.append(format_row(name, bucket["win"], bucket["loss"]))
    if session_entries:
        best = pick_best(session_entries, tie_break_order=session_names)
        lines.append("")
        lines.append(f"⭐ Best session: {best} ({format_pct(by_session[best]['win'], by_session[best]['loss'])} WR)")

    lines.append("")
    lines.append("🕐 Best Hours (min 3 trades, top 10 by WR)")
    by_hour = tally(trades, lambda t: t["_local_entry"].hour)
    best_hours = select_best_hours(by_hour)
    for hour in best_hours:
        bucket = by_hour[hour]
        session = session_for_time(hour, 0)  # cosmetic label: session at the start of this clock hour
        lines.append(format_hour_row(hour, session, bucket["win"], bucket["loss"]))
    if best_hours:
        hour_entries = [(h, by_hour[h]) for h in best_hours]
        peak = pick_best(hour_entries)
        lines.append("")
        lines.append(f"⭐ Peak hour: {peak:02d}:00 ({format_pct(by_hour[peak]['win'], by_hour[peak]['loss'])} WR)")

    lines.append("")
    lines.append("🏆 By Grade")
    by_grade = tally(trades, lambda t: t["grade"])
    grade_entries = [
        (g, by_grade[g]) for g in sorted(by_grade, key=grade_sort_key)
        if (by_grade[g]["win"] + by_grade[g]["loss"]) > 0
    ]
    for grade, bucket in grade_entries:
        lines.append(format_row(grade, bucket["win"], bucket["loss"], label_width=6))
    if grade_entries:
        best_grade = pick_best(grade_entries, tie_break_order=[g for g, _ in grade_entries])
        lines.append("")
        lines.append(f"⭐ Best grade: {best_grade} ({format_pct(by_grade[best_grade]['win'], by_grade[best_grade]['loss'])} WR)")

    lines.append("")
    lines.append("⏱️ By Timeframe")
    by_tf = tally(trades, lambda t: t["timeframe"])
    tf_entries = [
        (tf, by_tf[tf]) for tf in TF_ORDER
        if tf in by_tf and (by_tf[tf]["win"] + by_tf[tf]["loss"]) > 0
    ]
    for tf, bucket in tf_entries:
        lines.append(format_row(tf, bucket["win"], bucket["loss"], label_width=6))

    return "\n".join(lines)


import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://discord.com/api/v10"
FETCH_PAGE_LIMIT = 100
MAX_PAGES = 20
SUMMARY_CHANNEL_ID = "1507284062119657522"

# (timeframe, channel_id) -- duplicated from tracker/delay_relay.py's PAIRS on
# purpose; this module is fully independent (see module docstring).
PAIRS = [
    ("30s", "1507159472156704908"),
    ("1m", "1503837558017360034"),
    ("2m", "1503838210755657828"),
    ("3m", "1503838275964768418"),
    ("5m", "1503838319019167885"),
    ("15m", "1504811070110437386"),
]


def _discord_request(token: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bot {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "DiscordWeeklySummary (https://github.com/TuanAnh17N/apex-bot-delay-relay, 1.0)")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_messages_in_range(token: str, channel_id: str, start: datetime, end: datetime) -> list[dict]:
    collected: list[dict] = []
    before = None
    for _ in range(MAX_PAGES):
        path = f"/channels/{channel_id}/messages?limit={FETCH_PAGE_LIMIT}"
        if before:
            path += f"&before={before}"
        page = _discord_request(token, "GET", path)
        if not page:
            break
        collected.extend(page)
        oldest_ts = datetime.fromisoformat(page[-1]["timestamp"])
        before = page[-1]["id"]
        if oldest_ts < start:
            break
        if len(page) < FETCH_PAGE_LIMIT:
            break
    collected.reverse()
    return [m for m in collected if start <= datetime.fromisoformat(m["timestamp"]) < end]


def post_message(token: str, channel_id: str, content: str) -> None:
    _discord_request(token, "POST", f"/channels/{channel_id}/messages", {"content": content})


def main() -> None:
    token = os.environ["DISCORD_BOT_TOKEN"]
    now_berlin = datetime.now(timezone.utc).astimezone(BERLIN)
    week_start, week_end = _current_report_window(now_berlin)

    all_trades = []
    any_failed = False
    for tf, channel_id in PAIRS:
        try:
            messages = fetch_messages_in_range(token, channel_id, week_start, week_end)
            trades = reconstruct_trades(messages, tf)
            all_trades.extend(trades)
            print(f"[{tf}] {len(trades)} trades")
        except Exception as exc:
            print(f"[{tf}] FAILED: {exc}")
            any_failed = True

    report = build_weekly_report(all_trades, week_start, week_end)
    post_message(token, SUMMARY_CHANNEL_ID, f"```\n{report}\n```")

    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
