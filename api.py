"""
api.py — Alian Software RAG Chatbot Backend
============================================
FastAPI wrapper around query_data.py RAG pipeline.

Run BOTH servers:
    python app.py          # Flask on :5000  (existing admin UI)
    uvicorn api:app --reload --host 0.0.0.0 --port 8000   # this file

Or use the helper:
    python run_servers.py
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from query_data import query_rag_web
except ImportError as _exc:
    raise SystemExit(
        "\n[api.py] ERROR: Cannot import query_rag_web from query_data.py\n"
    ) from _exc

from usage_analytics import record_session_event

import json
from pathlib import Path
import os
from dotenv import load_dotenv
load_dotenv()

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "admin_oaHHpo_leC9Kr5p_I5DCjdLJ-HTLf-pAQBR3U0RQD1Y")

TENANTS_FILE = Path("tenants.json")

def _load_tenants():
    if not TENANTS_FILE.exists():
        return []
    try:
        return json.loads(TENANTS_FILE.read_text("utf-8"))
    except Exception:
        return []

def _save_tenants(tenants):
    TENANTS_FILE.write_text(json.dumps(tenants, indent=2, ensure_ascii=False), encoding="utf-8")

def _check_and_increment(api_key: str) -> dict:
    """
    Returns {"allowed": True} or {"allowed": False, "reason": "..."}
    Also increments requests_used if allowed.
    """
    if api_key == ADMIN_API_KEY:
        return {"allowed": True}
    tenants = _load_tenants()
    for t in tenants:
        if t.get("api_key") == api_key:
            used  = t.get("requests_used", 0)
            limit = t.get("request_limit", 1000)
            if t.get("blocked", False):
                return {"allowed": False, "reason": "Your chatbot has been paused by the administrator."}
            if used >= limit:
                t["blocked"] = True   # auto-block when limit hit
                _save_tenants(tenants)
                return {"allowed": False, "reason": "Request limit reached. Please contact support to continue."}
            t["requests_used"] = used + 1
            _save_tenants(tenants)
            return {"allowed": True}
    # no matching tenant — still allow (widget may not pass api_key in all setups)
    return {"allowed": False, "reason": "Your chatbot access has been revoked. Please contact support."}

def _get_collection_name(api_key: str) -> str | None:
    if not api_key:
        return None
    tenants = _load_tenants()
    for t in tenants:
        if t.get("api_key") == api_key:
            return t.get("tenant_id") or t.get("name", "").lower().replace(" ", "-")
    return None

app = FastAPI(
    title="Alian Software RAG Chatbot API",
    description="Read-only RAG chatbot — no database write operations exposed.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Accept", "ngrok-skip-browser-warning"],
    max_age=3600,
)


HISTORY_DIR = Path("chat_histories")
HISTORY_DIR.mkdir(exist_ok=True)

# Active widget sessions live in memory until /session/end persists them.
ACTIVE_SESSIONS: dict[str, dict] = {}


def _path(sid: str) -> Path:
    return HISTORY_DIR / f"chat_history_{sid}.json"


def _load_persisted(sid: str) -> dict | None:
    p = _path(sid)
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def _save_persisted(data: dict) -> None:
    _path(data["session_id"]).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_sid(sid: str) -> bool:
    bad = {"/", "\\", "..", "~", "\x00", "%"}
    return bool(sid) and not any(c in sid for c in bad) and len(sid) <= 64


def _new_session(sid: str, first_question: str = "") -> dict:
    title = first_question.strip()
    if len(title) > 47:
        title = title[:47] + "…"
    return {
        "session_id": sid,
        "source": "widget",
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
        "title": title or "Untitled Chat",
        "messages": [],
        "turns": [],
    }


def _get_or_create_active(sid: str, first_question: str = "") -> dict:
    if sid not in ACTIVE_SESSIONS:
        persisted = _load_persisted(sid)
        if persisted and persisted.get("messages"):
            ACTIVE_SESSIONS[sid] = persisted
        else:
            ACTIVE_SESSIONS[sid] = _new_session(sid, first_question)
    return ACTIVE_SESSIONS[sid]


def _session_to_chat_history(session: dict) -> list:
    history = []
    for turn in session.get("turns", []):
        history.append({
            "query": turn.get("query", ""),
            "result": {
                "response": turn.get("response", ""),
            },
        })
    return history


def _persist_session(sid: str) -> dict | None:
    session = ACTIVE_SESSIONS.pop(sid, None)
    if not session:
        session = _load_persisted(sid)
    if not session or not session.get("messages"):
        return None

    session["ended_at"] = _utcnow()
    session["updated_at"] = session["ended_at"]
    _save_persisted(session)
    record_session_event(session, source="widget")
    return session


class ChatRequest(BaseModel):
    question:   str
    session_id: Optional[str] = None
    api_key:    Optional[str] = None


class SessionEndRequest(BaseModel):
    session_id: str


def _extract_answer(raw) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return str(raw.get("response") or raw.get("answer") or "")
    return str(raw)


@app.get("/health")
def health():
    return {"status": "ok", "service": "Alian Software RAG API"}


from fastapi import Request as FastAPIRequest

@app.post("/chat")
async def chat(req: ChatRequest, request: FastAPIRequest):
    # read api_key from query param if not in body
    if not req.api_key:
        req.api_key = request.query_params.get("api_key")

    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty.")

    sid = req.session_id if req.session_id and _safe_sid(req.session_id) else str(uuid.uuid4())
    session = _get_or_create_active(sid, question)


    # Store api_key in session on first message so limit check works
    if req.api_key and not session.get("api_key"):
        session["api_key"] = req.api_key
        
    if not session.get("messages"):
        session["title"] = (question[:47] + "…") if len(question) > 47 else question

    session["messages"].append(
        {"role": "user", "content": question, "timestamp": _utcnow()}
    )

    chat_history = _session_to_chat_history(session)

    # ── Request limit check ───────────────────────────────────────────────────
    api_key = req.api_key if hasattr(req, 'api_key') else None
    # Try to get it from session metadata or active session
    stored_key = session.get("api_key")
    if stored_key:
        limit_check = _check_and_increment(stored_key)
        if not limit_check["allowed"]:
            return {"answer": limit_check["reason"], "session_id": sid, "title": session["title"]}

    try:
        collection_name = _get_collection_name(session.get("api_key") or stored_key)
        raw = query_rag_web(question, chat_history=chat_history, collection_name=collection_name)
        answer = _extract_answer(raw)
        from usage_analytics import record_query_event
        record_query_event(
            source="widget",
            session_id=sid,
            query=question,
            result=raw if isinstance(raw, dict) else {},
            tenant_id=_get_collection_name(session.get("api_key")),
        )
    except Exception as exc:
        print(f"[api.py] RAG error: {exc}")
        raise HTTPException(
            status_code=500,
            detail="The knowledge base query failed. Please try again.",
        ) from exc

    session["messages"].append(
        {"role": "assistant", "content": answer, "timestamp": _utcnow()}
    )
    session["turns"].append({
        "query": question,
        "response": answer,
        "timestamp": _utcnow(),
        "prompt_tokens": (raw or {}).get("prompt_tokens") if isinstance(raw, dict) else None,
        "response_tokens": (raw or {}).get("response_tokens") if isinstance(raw, dict) else None,
        "total_tokens": (raw or {}).get("total_tokens") if isinstance(raw, dict) else None,
        "latency": (raw or {}).get("latency") if isinstance(raw, dict) else None,
    })
    session["updated_at"] = _utcnow()
    ACTIVE_SESSIONS[sid] = session
    _save_persisted(session)  # persist after every turn

    return {"answer": answer, "session_id": sid, "title": session["title"]}


@app.post("/session/end")
def end_session(req: SessionEndRequest):
    sid = req.session_id
    if not _safe_sid(sid):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")

    persisted = _persist_session(sid)
    if persisted is None:
        ACTIVE_SESSIONS.pop(sid, None)
        return {"saved": False, "session_id": sid, "reason": "empty_or_missing"}

    return {
        "saved": True,
        "session_id": sid,
        "message_count": len(persisted.get("messages", [])),
        "total_tokens": sum(int(t.get("total_tokens") or 0) for t in persisted.get("turns", [])),
    }


@app.get("/history")
def list_sessions(api_key: Optional[str] = None):
    sessions: list[dict] = []
    seen: set[str] = set()

    for f in HISTORY_DIR.glob("chat_history_*.json"):
        try:
            d = json.loads(f.read_text("utf-8"))
            # filter by api_key if provided
            if api_key and d.get("api_key") != api_key:
                continue
            sid = d["session_id"]
            seen.add(sid)
            sessions.append({
                "session_id": sid,
                "title": d.get("title", "Untitled Chat"),
                "created_at": d.get("created_at"),
                "updated_at": d.get("updated_at", d.get("created_at")),
                "message_count": len(d.get("messages", [])),
            })
        except (json.JSONDecodeError, KeyError):
            continue

    for sid, d in ACTIVE_SESSIONS.items():
        if sid in seen or not d.get("messages"):
            continue
        if api_key and d.get("api_key") != api_key:
            continue
        sessions.append({
            "session_id": sid,
            "title": d.get("title", "Untitled Chat"),
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at", d.get("created_at")),
            "message_count": len(d.get("messages", [])),
            "active": True,
        })

    sessions.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {"sessions": sessions, "count": len(sessions)}

@app.get("/history/{session_id}")
def get_session(session_id: str):
    if not _safe_sid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")

    if session_id in ACTIVE_SESSIONS:
        return ACTIVE_SESSIONS[session_id]

    session = _load_persisted(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


@app.delete("/history")
def delete_all_sessions(api_key: Optional[str] = None):
    deleted = 0
    for f in HISTORY_DIR.glob("chat_history_*.json"):
        try:
            d = json.loads(f.read_text("utf-8"))
            if api_key and d.get("api_key") != api_key:
                continue
            f.unlink()
            deleted += 1
        except OSError:
            continue
    if api_key:
        for sid in list(ACTIVE_SESSIONS.keys()):
            if ACTIVE_SESSIONS[sid].get("api_key") == api_key:
                ACTIVE_SESSIONS.pop(sid, None)
    else:
        ACTIVE_SESSIONS.clear()
    return {"deleted": True, "deleted_count": deleted}


@app.post("/history/{session_id}/delete")
def delete_session_post(session_id: str):
    if not _safe_sid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")
    ACTIVE_SESSIONS.pop(session_id, None)
    p = _path(session_id)
    if p.exists():
        p.unlink()
    return {"deleted": True, "session_id": session_id}



@app.delete("/history/{session_id}")
def delete_session(session_id: str):
    if not _safe_sid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")
    ACTIVE_SESSIONS.pop(session_id, None)
    p = _path(session_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Session not found.")
    p.unlink()
    return {"deleted": True, "session_id": session_id}


# @app.delete("/history")
# def delete_all_sessions():
#     ACTIVE_SESSIONS.clear()
#     deleted = 0
#     for f in HISTORY_DIR.glob("chat_history_*.json"):
#         try:
#             f.unlink()
#             deleted += 1
#         except OSError:
#             continue
#     return {"deleted": True, "deleted_count": deleted}


@app.post("/new-session")
def new_session(api_key: Optional[str] = None):
    sid = str(uuid.uuid4())
    if api_key:
        session = _new_session(sid)
        session["api_key"] = api_key
        ACTIVE_SESSIONS[sid] = session
        _save_persisted(session)  # persist after every turn
    return {"session_id": sid}


app.mount("/widget", StaticFiles(directory="widget", html=True), name="widget")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
