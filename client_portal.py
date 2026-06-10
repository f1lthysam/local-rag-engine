"""
client_portal.py — Flask Blueprint for all /client/* routes.

Routes:
    GET  /client/register          → registration page
    POST /client/register          → handle registration form
    GET  /client/verify/<token>    → verify email link
    GET  /client/resend-verify     → resend verification email page
    POST /client/resend-verify     → handle resend
    GET  /client/login             → login page
    POST /client/login             → handle login form
    GET  /client/logout            → logout + redirect
    GET  /client/portal            → main portal dashboard (protected)
    GET  /client/api/analytics     → analytics data JSON (protected)
    GET  /client/api/documents     → list documents JSON (protected)
    POST /client/api/documents     → upload document (protected)
    DELETE /client/api/documents/<name> → delete document (protected)
    POST /client/api/scrape        → trigger website re-scrape (protected)
    GET  /client/api/scrape/stream → SSE stream for scrape progress (protected)
    POST /client/api/change-password → change password (protected)
    GET  /client/api/widget-code   → get embed code (protected)

Register in app.py:
    from client_portal import client_portal_bp
    app.register_blueprint(client_portal_bp)
"""

import json
import os
import threading
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import (
    Blueprint, Response, flash, jsonify,
    redirect, render_template, request,
    session, stream_with_context, url_for,
)
from werkzeug.utils import secure_filename

from client_auth import (
    change_password, client_login_required,
    login_client, logout_client,
    register_client, resend_verification,
    verify_client_email,
)
import query_data

# ── Blueprint ─────────────────────────────────────────────────────────────────

client_portal_bp = Blueprint(
    "client_portal",
    __name__,
    url_prefix="/client",
    template_folder="templates",
)

# ── Constants ─────────────────────────────────────────────────────────────────

ANALYTICS_FILE     = Path("analytics/session_events.jsonl")
DATA_PATH          = Path("data")
ALLOWED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}

# Scrape progress store — {tenant_id: [log lines]}
_SCRAPE_PROGRESS: dict[str, list[str]] = {}
_SCRAPE_LOCK = threading.Lock()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _tenant() -> str:
    return session.get("client_tenant", "")


def _allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _tenant_data_dir() -> Path:
    d = DATA_PATH / _tenant()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_analytics(tenant_id: str) -> list[dict]:
    """Load all analytics events for this tenant from the JSONL file."""
    events = []
    if not ANALYTICS_FILE.exists():
        return events
    with open(ANALYTICS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("tenant_id") == tenant_id:
                    events.append(ev)
            except json.JSONDecodeError:
                continue
    return events


# ── Auth routes ───────────────────────────────────────────────────────────────

@client_portal_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("client_register.html")

    email        = request.form.get("email", "").strip()
    password     = request.form.get("password", "")
    confirm_pw   = request.form.get("confirm_password", "")
    company_name = request.form.get("company_name", "").strip()
    tenant_id    = request.form.get("tenant_id", "").strip().lower().replace(" ", "-")

    if password != confirm_pw:
        flash("Passwords do not match.", "error")
        return render_template("client_register.html", form=request.form)

    result = register_client(email, password, company_name, tenant_id)

    if not result["ok"]:
        flash(result["error"], "error")
        return render_template("client_register.html", form=request.form)

    if result.get("email_sent"):
        flash("Account created! Check your email for a verification link.", "success")
    else:
        flash("Account created but we couldn't send the verification email. "
              "Use 'Resend Verification' below.", "warning")

    return redirect(url_for("client_portal.login"))


@client_portal_bp.route("/verify/<token>")
def verify_email(token: str):
    result = verify_client_email(token)
    if not result["ok"]:
        flash(result["error"], "error")
        return redirect(url_for("client_portal.login"))

    if result.get("already_verified"):
        flash("Your email is already verified. Please log in.", "info")
    else:
        flash("Email verified! You can now log in.", "success")

    return redirect(url_for("client_portal.login"))


@client_portal_bp.route("/resend-verify", methods=["GET", "POST"])
def resend_verify():
    if request.method == "GET":
        return render_template("client_resend_verify.html")

    email  = request.form.get("email", "").strip()
    result = resend_verification(email)

    if result["ok"]:
        flash("Verification email sent! Check your inbox.", "success")
    else:
        flash(result["error"], "error")

    return render_template("client_resend_verify.html")


@client_portal_bp.route("/login", methods=["GET", "POST"])
def login():
    if "client_id" in session:
        return redirect(url_for("client_portal.portal"))

    if request.method == "GET":
        return render_template("client_login.html")

    email    = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    result   = login_client(email, password)

    if not result["ok"]:
        if result.get("needs_verification"):
            flash(result["error"] + " <a href='/client/resend-verify'>Resend link</a>", "warning")
        else:
            flash(result["error"], "error")
        return render_template("client_login.html", email=email)

    return redirect(url_for("client_portal.portal"))


@client_portal_bp.route("/logout")
def logout():
    logout_client()
    flash("You have been logged out.", "info")
    return redirect(url_for("client_portal.login"))


# ── Portal (main dashboard) ───────────────────────────────────────────────────

@client_portal_bp.route("/portal")
@client_login_required
def portal():
    return render_template(
        "client_portal.html",
        company=session.get("client_company"),
        email=session.get("client_email"),
        tenant_id=_tenant(),
    )


# ── Analytics API ─────────────────────────────────────────────────────────────

@client_portal_bp.route("/api/analytics")
@client_login_required
def api_analytics():
    """
    Returns analytics data as JSON for Chart.js charts.

    Response shape:
    {
        "queries_per_day":   [{"date": "2025-01-01", "count": 12}, ...],
        "top_questions":     [{"question": "...", "count": 5}, ...],
        "answered_rate":     {"answered": 80, "unanswered": 20},
        "peak_hours":        [{"hour": 14, "count": 30}, ...],
        "total_queries":     142,
        "total_sessions":    38
    }
    """
    tenant_id = _tenant()
    events    = _load_analytics(tenant_id)

    if not events:
        return jsonify({
            "queries_per_day": [],
            "top_questions":   [],
            "answered_rate":   {"answered": 0, "unanswered": 0},
            "peak_hours":      [],
            "total_queries":   0,
            "total_sessions":  0,
        })

    # ── Queries per day (last 30 days) ────────────────────────────────────────
    cutoff   = datetime.now(timezone.utc) - timedelta(days=30)
    day_counter: Counter = Counter()
    hour_counter: Counter = Counter()
    question_counter: Counter = Counter()
    answered = unanswered = 0
    sessions: set = set()

    for ev in events:
        ts_raw = ev.get("timestamp") or ev.get("ts") or ""
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            ts = None

        if ts and ts >= cutoff:
            day_counter[ts.strftime("%Y-%m-%d")] += 1
            hour_counter[ts.hour] += 1

        q = ev.get("query") or ev.get("question") or ""
        if q:
            question_counter[q.strip().lower()[:120]] += 1

        if ev.get("session_id"):
            sessions.add(ev["session_id"])

        # Answered = response exists and is not the "no info" fallback
        resp = ev.get("response") or ev.get("answer") or ""
        if resp and "don't have information" not in resp.lower():
            answered += 1
        else:
            unanswered += 1

    # Fill missing days with 0
    queries_per_day = []
    for i in range(29, -1, -1):
        d = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        queries_per_day.append({"date": d, "count": day_counter.get(d, 0)})

    top_questions = [
        {"question": q, "count": c}
        for q, c in question_counter.most_common(10)
    ]

    peak_hours = [
        {"hour": h, "count": hour_counter.get(h, 0)}
        for h in range(24)
    ]

    return jsonify({
        "queries_per_day": queries_per_day,
        "top_questions":   top_questions,
        "answered_rate":   {"answered": answered, "unanswered": unanswered},
        "peak_hours":      peak_hours,
        "total_queries":   sum(day_counter.values()),
        "total_sessions":  len(sessions),
    })


# ── Chat history API ──────────────────────────────────────────────────────────

@client_portal_bp.route("/api/chat-history")
@client_login_required
def api_chat_history():
    """Return last 50 Q&A pairs for this tenant."""
    events  = _load_analytics(_tenant())
    history = []
    for ev in reversed(events[-200:]):
        q = ev.get("query") or ev.get("question") or ""
        a = ev.get("response") or ev.get("answer") or ""
        if q:
            history.append({
                "question":  q,
                "answer":    a,
                "timestamp": ev.get("timestamp") or ev.get("ts") or "",
                "session_id": ev.get("session_id", ""),
            })
        if len(history) >= 50:
            break
    return jsonify({"history": history})


# ── Documents API ─────────────────────────────────────────────────────────────

@client_portal_bp.route("/api/documents", methods=["GET"])
@client_login_required
def api_list_documents():
    """List all documents ingested for this tenant."""
    tenant_dir = _tenant_data_dir()
    files = []
    for f in sorted(tenant_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
            files.append({
                "name":     f.name,
                "size_kb":  round(f.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(
                    f.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            })
    return jsonify({"documents": files})


@client_portal_bp.route("/api/documents", methods=["POST"])
@client_login_required
def api_upload_document():
    """Upload a new document for this tenant."""
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file provided."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"ok": False, "error": "Empty filename."}), 400

    if not _allowed_file(file.filename):
        return jsonify({
            "ok": False,
            "error": f"Allowed types: {', '.join(ALLOWED_EXTENSIONS)}"
        }), 400

    filename = secure_filename(file.filename)
    save_path = _tenant_data_dir() / filename
    from app import save_upload_as_markdown
    md_path = save_upload_as_markdown(file, filename, str(_tenant_data_dir()))
    print(f"[DEBUG] tenant={_tenant()} path={save_path}")

    # Ingest into ChromaDB for this tenant
    try:
        from populate_database import load_documents, split_documents, add_to_chroma
        import query_data
        query_data._DB = None
        docs   = load_documents(str(_tenant_data_dir()))
        chunks = split_documents(docs)
        add_to_chroma(chunks, collection_name=_tenant())
        import query_data
        query_data._DB = None
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": f"Saved but ingestion failed: {traceback.format_exc()}"}), 500

    return jsonify({"ok": True, "filename": filename})


@client_portal_bp.route("/api/documents/<filename>", methods=["DELETE"])
@client_login_required
def api_delete_document(filename: str):
    """Delete a document and remove it from ChromaDB."""
    safe_name = secure_filename(filename)
    file_path = _tenant_data_dir() / safe_name

    if not file_path.exists():
        return jsonify({"ok": False, "error": "File not found."}), 404

    file_path.unlink()

    # Re-ingest remaining documents to rebuild the collection
    try:
        from populate_database import load_documents, split_documents, add_to_chroma
        import query_data
        query_data._DB = None
        import chromadb
        client = chromadb.PersistentClient(path="chroma")
        try:
            client.delete_collection(_tenant())
        except Exception:
            pass
        remaining = list(_tenant_data_dir().iterdir())
        if remaining:
            docs   = load_documents(str(_tenant_data_dir()))
            chunks = split_documents(docs)
            add_to_chroma(chunks, collection_name=_tenant())
            import query_data
            query_data._DB = None
    except Exception as e:
        return jsonify({"ok": False, "error": f"Deleted but re-ingestion failed: {e}"}), 500

    return jsonify({"ok": True})


# ── Web scraper API ───────────────────────────────────────────────────────────

@client_portal_bp.route("/api/scrape", methods=["POST"])
@client_login_required
def api_scrape():
    """Kick off a background website scrape for this tenant."""
    data      = request.get_json(silent=True) or {}
    url       = (data.get("url") or "").strip()
    max_pages = int(data.get("max_pages") or 50)
    tenant_id = _tenant()

    if not url or not url.startswith("http"):
        return jsonify({"ok": False, "error": "Provide a valid URL starting with http."}), 400

    # Reset progress log
    with _SCRAPE_LOCK:
        _SCRAPE_PROGRESS[tenant_id] = ["Starting scrape…"]

    def run_scrape():
        try:
            from scrape_web import scrape_full_website
            import sys, io

            # Capture stdout from scraper into progress log
            old_stdout = sys.stdout
            sys.stdout = buf = io.StringIO()

            scrape_full_website(
                start_url=url,
                base_filename=str(_tenant_data_dir() / "scraped"),
                max_pages=max_pages,
                batch_size=10,
                headless=True,
                wait_for="domcontentloaded",
                save_individual_pages=True,
                use_sitemap=True,
                resume=False,
            )

            sys.stdout = old_stdout
            output = buf.getvalue()

            with _SCRAPE_LOCK:
                for line in output.splitlines():
                    if line.strip():
                        _SCRAPE_PROGRESS[tenant_id].append(line)
                _SCRAPE_PROGRESS[tenant_id].append("__DONE__")

            # Ingest scraped files into ChromaDB
            from populate_database import load_documents, split_documents, add_to_chroma
            docs   = load_documents(str(_tenant_data_dir()))
            chunks = split_documents(docs)
            add_to_chroma(chunks, collection_name=tenant_id)
            import query_data
            query_data._DB = None

        except Exception as e:
            with _SCRAPE_LOCK:
                _SCRAPE_PROGRESS[tenant_id].append(f"Error: {e}")
                _SCRAPE_PROGRESS[tenant_id].append("__DONE__")

    thread = threading.Thread(target=run_scrape, daemon=True)
    thread.start()

    return jsonify({"ok": True, "message": "Scrape started. Stream /api/scrape/stream for progress."})


@client_portal_bp.route("/api/scrape/stream")
@client_login_required
def api_scrape_stream():
    """Server-Sent Events stream for real-time scrape progress."""
    tenant_id = _tenant()
    sent      = 0

    def generate():
        nonlocal sent
        import time
        while True:
            with _SCRAPE_LOCK:
                lines = _SCRAPE_PROGRESS.get(tenant_id, [])

            while sent < len(lines):
                line = lines[sent]
                sent += 1
                yield f"data: {json.dumps({'line': line})}\n\n"
                if line == "__DONE__":
                    return

            time.sleep(0.5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Settings API ──────────────────────────────────────────────────────────────

@client_portal_bp.route("/api/change-password", methods=["POST"])
@client_login_required
def api_change_password():
    data       = request.get_json(silent=True) or {}
    old_pw     = data.get("old_password", "")
    new_pw     = data.get("new_password", "")
    confirm_pw = data.get("confirm_password", "")

    if new_pw != confirm_pw:
        return jsonify({"ok": False, "error": "New passwords do not match."}), 400

    result = change_password(session["client_id"], old_pw, new_pw)
    return jsonify(result)


@client_portal_bp.route("/api/widget-code")
@client_login_required
def api_widget_code():
    tenant_id = _tenant()
    company   = session.get("client_company", tenant_id)
    
    # find api_key from tenants.json by matching name or tenant slug
    api_key = ""
    try:
        tenants = json.loads(Path("tenants.json").read_text("utf-8"))
        for t in tenants:
            name_slug = t.get("name", "").lower().replace(" ", "-")
            if t.get("name", "").upper() == company.upper() or name_slug == tenant_id:
                api_key = t.get("api_key", "")
                break
    except Exception:
        pass

    base_url = "http://localhost:8000"
    code = (
        f'<script>\n'
        f'  window.RagChatConfig = {{\n'
        f'    apiBase:       \'{base_url}\',\n'
        f'    apiKey:        \'{api_key}\',\n'
        f'    siteName:      \'{company}\',\n'
        f'    assistantName: \'{company} Assistant\',\n'
        f'  }};\n'
        f'</script>\n'
        f'<script src="{base_url}/widget/chatbot.js"></script>'
    )
    return jsonify({"code": code})