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


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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


def record_query_event(
    *,
    source: str,
    session_id: str,
    query: str,
    result: dict[str, Any] | None,
    dataset: str | None = None,
) -> None:
    """Append one RAG request summary to analytics/session_events.jsonl."""
    try:
        result = result or {}
        event = {
            "event": "query_completed",
            "recorded_at": _utcnow(),
            "source": source,
            "session_id": session_id,
            "query": query[:180],
            "dataset": dataset,
            "prompt_tokens": _safe_int(result.get("prompt_tokens")),
            "response_tokens": _safe_int(result.get("response_tokens")),
            "total_tokens": _safe_int(result.get("total_tokens")),
            "latency": result.get("latency"),
            "retrieval_mode": result.get("retrieval_mode"),
            "no_info": bool(result.get("no_info")),
        }

        ANALYTICS_DIR.mkdir(exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[usage_analytics] Could not record query event: {exc}")


def usage_summary() -> dict[str, int]:
    """Return aggregate request/session/token counts from persisted analytics."""
    summary = {
        "request_count": 0,
        "session_count": 0,
        "prompt_tokens": 0,
        "response_tokens": 0,
        "total_tokens": 0,
    }
    if not EVENTS_FILE.exists():
        return summary

    try:
        with EVENTS_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "query_completed":
                    summary["request_count"] += 1
                elif event.get("event") == "session_ended":
                    summary["session_count"] += 1
                summary["prompt_tokens"] += _safe_int(event.get("prompt_tokens"))
                summary["response_tokens"] += _safe_int(event.get("response_tokens"))
                summary["total_tokens"] += _safe_int(event.get("total_tokens"))
    except OSError:
        pass
    return summary
