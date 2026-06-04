"""
Small analytics helper for persisted widget chat sessions.

The API should never fail just because analytics cannot be written, so this
module keeps writes best-effort and independent from chat history storage.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


ANALYTICS_DIR = Path("analytics")
EVENTS_FILE = ANALYTICS_DIR / "session_events.jsonl"


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _sum_turn_field(turns: list[dict[str, Any]], field: str) -> int:
    total = 0
    for turn in turns:
        try:
            total += int(turn.get(field) or 0)
        except (TypeError, ValueError):
            continue
    return total


def record_session_event(session: dict[str, Any], source: str = "widget") -> None:
    """Append a compact session summary to analytics/session_events.jsonl."""
    try:
        turns = session.get("turns") or []
        messages = session.get("messages") or []
        event = {
            "event": "session_ended",
            "recorded_at": _utcnow(),
            "source": source,
            "session_id": session.get("session_id"),
            "title": session.get("title", "Untitled Chat"),
            "created_at": session.get("created_at"),
            "ended_at": session.get("ended_at"),
            "message_count": len(messages),
            "turn_count": len(turns),
            "prompt_tokens": _sum_turn_field(turns, "prompt_tokens"),
            "response_tokens": _sum_turn_field(turns, "response_tokens"),
            "total_tokens": _sum_turn_field(turns, "total_tokens"),
        }

        ANALYTICS_DIR.mkdir(exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[usage_analytics] Could not record session event: {exc}")
