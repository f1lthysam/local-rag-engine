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

# ── Import RAG function (READ-ONLY — only calls ChromaDB .query()) ───────────
try:
    from query_data import query_rag_web
except ImportError as _exc:
    raise SystemExit(
        "\n[api.py] ERROR: Cannot import query_rag_web from query_data.py\n"
    ) from _exc


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Alian Software RAG Chatbot API",
    description="Read-only RAG chatbot — no database write operations exposed.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],   # DELETE is only used for chat history cleanup
    allow_headers=["Content-Type", "Accept", "ngrok-skip-browser-warning"],
    max_age=3600,
)


# ── Session persistence (local JSON — nothing touches ChromaDB) ───────────────
HISTORY_DIR = Path("chat_histories")
HISTORY_DIR.mkdir(exist_ok=True)


def _path(sid: str) -> Path:
    return HISTORY_DIR / f"chat_history_{sid}.json"


def _load(sid: str) -> dict | None:
    p = _path(sid)
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def _save(data: dict) -> None:
    _path(data["session_id"]).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_sid(sid: str) -> bool:
    bad = {"/", "\\", "..", "~", "\x00", "%"}
    return bool(sid) and not any(c in sid for c in bad) and len(sid) <= 64


# ── Request model ─────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_answer(raw) -> str:
    """
    query_rag_web() returns a dict like:
        {"response": "...", "confidence": 80.0, "sources": [...], ...}
    or occasionally a plain str (legacy). Handle both.
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return str(raw.get("response") or raw.get("answer") or "")
    return str(raw)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "Alian Software RAG API"}


@app.post("/chat")
async def chat(req: ChatRequest):
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty.")

    sid = req.session_id if req.session_id and _safe_sid(req.session_id) else str(uuid.uuid4())

    session = _load(sid) or {
        "session_id": sid,
        "created_at": _utcnow(),
        "title": (question[:47] + "…") if len(question) > 47 else question,
        "messages": [],
    }

    session["messages"].append(
        {"role": "user", "content": question, "timestamp": _utcnow()}
    )

    # READ-ONLY RAG query — query_rag_web() only calls ChromaDB .query()
    try:
        raw = query_rag_web(question)          # returns dict
        answer: str = _extract_answer(raw)
    except Exception as exc:
        print(f"[api.py] RAG error: {exc}")
        raise HTTPException(
            status_code=500,
            detail="The knowledge base query failed. Please try again.",
        ) from exc

    session["messages"].append(
        {"role": "assistant", "content": answer, "timestamp": _utcnow()}
    )
    session["updated_at"] = _utcnow()
    _save(session)

    return {"answer": answer, "session_id": sid, "title": session["title"]}


@app.get("/history")
def list_sessions():
    sessions: list[dict] = []
    for f in HISTORY_DIR.glob("chat_history_*.json"):
        try:
            d = json.loads(f.read_text("utf-8"))
            sessions.append({
                "session_id":    d["session_id"],
                "title":         d.get("title", "Untitled Chat"),
                "created_at":    d.get("created_at"),
                "updated_at":    d.get("updated_at", d.get("created_at")),
                "message_count": len(d.get("messages", [])),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    sessions.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {"sessions": sessions, "count": len(sessions)}


@app.get("/history/{session_id}")
def get_session(session_id: str):
    if not _safe_sid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")
    session = _load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


@app.delete("/history/{session_id}")
def delete_session(session_id: str):
    if not _safe_sid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")
    p = _path(session_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Session not found.")
    p.unlink()
    return {"deleted": True, "session_id": session_id}


@app.post("/history/{session_id}/delete")
def delete_session_post(session_id: str):
    if not _safe_sid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")
    p = _path(session_id)
    if p.exists():
        p.unlink()
    return {"deleted": True, "session_id": session_id}


@app.delete("/history")
def delete_all_sessions():
    deleted = 0
    for f in HISTORY_DIR.glob("chat_history_*.json"):
        try:
            f.unlink()
            deleted += 1
        except OSError:
            continue
    return {"deleted": True, "deleted_count": deleted}


@app.post("/new-session")
def new_session():
    return {"session_id": str(uuid.uuid4())}


# ── Serve widget static files ─────────────────────────────────────────────────
# Embed on any site:
#   <script src="https://your-server/widget/chatbot.js"></script>
app.mount("/widget", StaticFiles(directory="widget", html=True), name="widget")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
