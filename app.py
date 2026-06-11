"""
app.py — Flask server with Admin Dashboard + RAG Studio
"""
from client_auth import register_client_auth
from client_portal import client_portal_bp

import warnings
warnings.filterwarnings("ignore")
import os, json, logging, secrets, re as _re
import datetime
from uuid import uuid4
from urllib.parse import urlparse
from pathlib import Path
os.environ["PYTHONWARNINGS"] = "ignore"
logging.disable(logging.CRITICAL)

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from populate_database import add_to_chroma, load_documents, split_documents
import query_data
from scrape_web import scrape_and_save, scrape_full_website
from usage_analytics import record_query_event, usage_summary

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin_oaHHpo_leC9Kr5p_I5DCjdLJ-HTLf-pAQBR3U0RQD1Y")

app = Flask(__name__, template_folder="templates")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["is_admin"] = True
            session.permanent = True
            return redirect(url_for("dashboard"))
        error = "Invalid credentials."
    return render_template("admin_login.html", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))


app.register_blueprint(client_portal_bp)
register_client_auth(app)

app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = os.getenv("FLASK_SECRET_KEY", "local-rag-dev-secret")

MODEL_NAME  = "gemini-3.1-flash-lite"
MAX_CHAT_TURNS = 20
CHAT_SESSIONS  = {}
DATA_PATH      = "data"
TENANTS_FILE   = Path("tenants.json")
HISTORY_DIR    = Path("chat_histories")
PROMPT_SETTINGS_FILE = Path("prompt_settings.json")
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt", ".docx", ".doc"}

# ── Guardrails ─────────────────────────────────────────────────────────────────
GUARDRAIL_RULES = [
    (r"ignore (all |previous |prior |above |your )?(instructions?|rules?|guidelines?|constraints?|prompt)",
     "Jailbreak attempt: cannot ignore system instructions"),
    (r"pretend (you are|to be|you're|you re)",
     "Jailbreak attempt: cannot impersonate other systems"),
    (r"you are now|from now on you|forget (everything|all|your)",
     "Jailbreak attempt: cannot override core behaviour"),
    (r"\bDAN\b|do anything now|no restrictions|without restrictions|bypass",
     "Jailbreak attempt: restricted keywords detected"),
    (r"disregard (your |all |any )?(rules?|guidelines?|instructions?|training)",
     "Jailbreak attempt: cannot disregard training"),
    (r"\b(harm|hurt|kill|attack|destroy|illegal|weapon|exploit)\b",
     "Harmful intent: prohibited keywords detected"),
    (r"system prompt|<\|.*\|>|\[INST\]|###\s*(instruction|system)",
     "Prompt injection: reserved tokens not allowed"),
    (r"do not use (the |any )?(context|document|retriev)",
     "Guardrail: cannot disable document context retrieval"),
]

def validate_prompt_settings(role: str, constraints: str):
    violations = []
    combined   = (role + " " + constraints).lower()
    for pattern, message in GUARDRAIL_RULES:
        if _re.search(pattern, combined, _re.IGNORECASE):
            violations.append(message)
    if len(role) > 500:
        violations.append("Role must be under 500 characters")
    if len(constraints) > 1000:
        violations.append("Configuration must be under 1000 characters")
    return violations

def load_prompt_settings() -> dict:
    if not PROMPT_SETTINGS_FILE.exists():
        return {"draft": None, "published": None}
    try:
        return json.loads(PROMPT_SETTINGS_FILE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"draft": None, "published": None}

def save_prompt_settings(data: dict) -> None:
    PROMPT_SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# ── Tenant helpers ─────────────────────────────────────────────────────────────
def load_tenants() -> list:
    if not TENANTS_FILE.exists():
        return []
    try:
        return json.loads(TENANTS_FILE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

def save_tenants(tenants: list) -> None:
    TENANTS_FILE.write_text(json.dumps(tenants, indent=2, ensure_ascii=False), encoding="utf-8")

def find_tenant(tenant_id: str) -> dict | None:
    return next((t for t in load_tenants() if t["id"] == tenant_id), None)

def tenant_stats(tenant: dict) -> dict:
    HISTORY_DIR.mkdir(exist_ok=True)
    sessions = list(HISTORY_DIR.glob("chat_history_*.json"))
    datasets = list(Path(DATA_PATH).glob("*.md")) if Path(DATA_PATH).exists() else []
    return {"session_count": len(sessions), "dataset_count": len(datasets)}

def dashboard_stats() -> dict:
    datasets  = [f for f in Path(DATA_PATH).rglob("*") if f.is_file()] if Path(DATA_PATH).exists() else []
    histories = list(HISTORY_DIR.glob("chat_history_*.json")) if HISTORY_DIR.exists() else []
    usage     = usage_summary()
    return {
        "dataset_count":      len(datasets),
        "chat_session_count": len(histories),
        **usage,
    }

# ── Session helpers ────────────────────────────────────────────────────────────
def get_session_id():
    if "chat_session_id" not in session:
        session["chat_session_id"] = uuid4().hex
    return session["chat_session_id"]

def get_chat_history():
    return CHAT_SESSIONS.setdefault(get_session_id(), [])

def append_chat_turn(query, result):
    history = get_chat_history()
    history.append({"query": query, "result": result})
    del history[:-MAX_CHAT_TURNS]
    record_query_event(
        source="studio",
        session_id=get_session_id(),
        query=query,
        result=result,
        dataset=get_selected_dataset(),
    )

def get_available_datasets(tenant_id=None):
    if tenant_id:
        data_dir = Path(f"data/{tenant_id}")
        if not data_dir.exists():
            return []
        return sorted([f.name for f in data_dir.glob("*.md")])
    data_dir = Path(DATA_PATH)
    if not data_dir.exists():
        return []
    datasets = sorted([f.name for f in data_dir.glob("*.md")])
    for tenant_dir in data_dir.iterdir():
        if tenant_dir.is_dir():
            for f in tenant_dir.glob("*.md"):
                datasets.append(f"{tenant_dir.name}/{f.name}")
    return sorted(datasets)

def get_selected_dataset():
    return session.get("selected_dataset", None)

def get_active_tenant_id():
    return session.get("active_tenant_id", None)

# ── Dashboard ──────────────────────────────────────────────────────────────────
@app.route("/")
def dashboard():
    tenants = load_tenants()
    stats   = {t["id"]: tenant_stats(t) for t in tenants}
    return render_template("dashboard.html", tenants=tenants, stats=stats, dashboard_stats=dashboard_stats())

@app.route("/api/tenants", methods=["GET"])
def api_list_tenants():
    return jsonify(load_tenants())

@app.route("/api/usage-summary", methods=["GET"])
def api_usage_summary():
    return jsonify(dashboard_stats())

@app.route("/api/tenants", methods=["POST"])
def api_create_tenant():
    data    = request.get_json(force=True) or {}
    name    = (data.get("name") or "").strip()
    website = (data.get("website") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    tenant = {
        "tenant_id":     data.get("tenant_id", name.lower().replace(" ", "-")).strip().lower().replace(" ", "-"),
        "id":            uuid4().hex[:12],
        "name":          name,
        "website":       website,
        "api_key":       "ak_" + secrets.token_urlsafe(24),
        "created_at":    datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes":         data.get("notes", ""),
        "request_limit": int(data.get("request_limit", 1000)),
        "requests_used": 0,
        "blocked":       False,
    }
    tenants = load_tenants()
    tenant_id = data.get("tenant_id", name.lower().replace(" ", "-")).strip().lower().replace(" ", "-")
    if any(t.get("tenant_id") == tenant_id for t in tenants):
        return jsonify({"error": f"Tenant ID '{tenant_id}' already exists."}), 400
    tenants.append(tenant)
    save_tenants(tenants)
    return jsonify(tenant), 201

@app.route("/api/tenants/<tenant_id>", methods=["DELETE"])
def api_delete_tenant(tenant_id):
    tenants = load_tenants()
    deleted_tenant = next((t for t in tenants if t["id"] == tenant_id), None)
    if not deleted_tenant:
        return jsonify({"error": "not found"}), 404
    updated = [t for t in tenants if t["id"] != tenant_id]
    save_tenants(updated)

    tenant_slug = deleted_tenant.get("tenant_id") or deleted_tenant.get("name", "").lower().replace(" ", "-")

    # Delete client account from SQLite
    try:
        from client_auth import get_db
        with get_db() as conn:
            conn.execute("DELETE FROM clients WHERE company_name = ? OR tenant_id = ?",
                         (deleted_tenant.get("name", ""), tenant_slug))
            conn.commit()
    except Exception as e:
        print(f"[app.py] Could not delete client account: {e}")

    # Delete chat histories
    try:
        for f in HISTORY_DIR.glob("chat_history_*.json"):
            try:
                import json as _json
                d = _json.loads(f.read_text("utf-8"))
                if d.get("api_key") == deleted_tenant.get("api_key"):
                    f.unlink()
            except Exception:
                continue
    except Exception as e:
        print(f"[app.py] Could not delete chat histories: {e}")

    # Delete ChromaDB collection
    try:
        import chromadb
        client = chromadb.PersistentClient(path="chroma")
        client.delete_collection(tenant_slug)
    except Exception as e:
        print(f"[app.py] Could not delete ChromaDB collection: {e}")

    # Delete data folder
    try:
        import shutil
        data_dir = Path(DATA_PATH) / tenant_slug
        if data_dir.exists():
            shutil.rmtree(data_dir)
    except Exception as e:
        print(f"[app.py] Could not delete data folder: {e}")

    return jsonify({"deleted": True})       

@app.route("/api/tenants/<tenant_id>/regenerate-key", methods=["POST"])
def api_regenerate_key(tenant_id):
    tenants = load_tenants()
    for t in tenants:
        if t["id"] == tenant_id:
            t["api_key"] = "ak_" + secrets.token_urlsafe(24)
            save_tenants(tenants)
            return jsonify({"api_key": t["api_key"]})
    return jsonify({"error": "not found"}), 404

@app.route("/api/tenants/<tenant_id>/limit", methods=["POST"])
def api_set_limit(tenant_id):
    data  = request.get_json(force=True) or {}
    limit = data.get("request_limit")
    if limit is None or not isinstance(limit, (int, float)) or int(limit) < 0:
        return jsonify({"error": "request_limit must be a non-negative integer"}), 400
    tenants = load_tenants()
    for t in tenants:
        if t["id"] == tenant_id:
            t["request_limit"] = int(limit)
            if t.get("requests_used", 0) < int(limit):
                t["blocked"] = False
            save_tenants(tenants)
            return jsonify({"ok": True, "request_limit": t["request_limit"]})
    return jsonify({"error": "not found"}), 404

@app.route("/api/tenants/<tenant_id>/block", methods=["POST"])
def api_block_tenant(tenant_id):
    tenants = load_tenants()
    for t in tenants:
        if t["id"] == tenant_id:
            t["blocked"] = True
            save_tenants(tenants)
            return jsonify({"ok": True, "blocked": True})
    return jsonify({"error": "not found"}), 404

@app.route("/api/tenants/<tenant_id>/unblock", methods=["POST"])
def api_unblock_tenant(tenant_id):
    tenants = load_tenants()
    for t in tenants:
        if t["id"] == tenant_id:
            t["blocked"] = False
            save_tenants(tenants)
            return jsonify({"ok": True, "blocked": False})
    return jsonify({"error": "not found"}), 404

@app.route("/api/tenants/<tenant_id>/reset-usage", methods=["POST"])
def api_reset_usage(tenant_id):
    tenants = load_tenants()
    for t in tenants:
        if t["id"] == tenant_id:
            t["requests_used"] = 0
            t["blocked"]       = False
            save_tenants(tenants)
            return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404

# ── Prompt Settings API ────────────────────────────────────────────────────────
@app.route("/api/prompt-settings", methods=["GET"])
def api_get_prompt_settings():
    return jsonify(load_prompt_settings())

@app.route("/api/prompt-settings/draft", methods=["POST"])
def api_save_draft():
    data        = request.get_json(force=True) or {}
    role        = (data.get("role") or "").strip()
    constraints = (data.get("constraints") or "").strip()
    settings          = load_prompt_settings()
    settings["draft"] = {
        "role":        role,
        "constraints": constraints,
        "saved_at":    datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status":      "draft",
    }
    save_prompt_settings(settings)
    return jsonify({"ok": True, "draft": settings["draft"]})

@app.route("/api/prompt-settings/validate", methods=["POST"])
def api_validate_prompt():
    data        = request.get_json(force=True) or {}
    role        = (data.get("role") or "").strip()
    constraints = (data.get("constraints") or "").strip()
    violations  = validate_prompt_settings(role, constraints)
    return jsonify({"ok": len(violations) == 0, "violations": violations})

@app.route("/api/prompt-settings/publish", methods=["POST"])
def api_publish_prompt():
    data        = request.get_json(force=True) or {}
    role        = (data.get("role") or "").strip()
    constraints = (data.get("constraints") or "").strip()
    violations  = validate_prompt_settings(role, constraints)
    if violations:
        return jsonify({"ok": False, "violations": violations}), 400
    now       = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    settings  = load_prompt_settings()
    published = {"role": role, "constraints": constraints, "published_at": now, "status": "published"}
    settings["published"] = published
    settings["draft"]     = None
    save_prompt_settings(settings)
    query_data._PROMPT_TEMPLATE = None
    return jsonify({"ok": True, "published": published})

@app.route("/api/prompt-settings/reset", methods=["POST"])
def api_reset_prompt():
    save_prompt_settings({"draft": None, "published": None})
    query_data._PROMPT_TEMPLATE = None
    return jsonify({"ok": True})

# ── Studio ─────────────────────────────────────────────────────────────────────
@app.route("/studio", methods=["GET", "POST"])
def studio():
    result       = None
    query        = None
    ingest       = None
    chat_history = get_chat_history()
    if chat_history:
        result = chat_history[-1].get("result")
    if request.method == "POST":
        query    = request.form.get("query", "").strip()
        selected = get_selected_dataset()
        if query:
            result = query_data.query_rag_web(
                query,
                chat_history=chat_history,
                dataset_filter=selected,
                collection_name=get_active_tenant_id() if get_active_tenant_id() else None,
            )
            append_chat_turn(query, result)
    return render_template("studio.html", result=result, query=query,
        chat_history=chat_history, ingest=ingest, url="", model=MODEL_NAME,
        datasets=get_available_datasets(get_active_tenant_id()), selected_dataset=get_selected_dataset())

@app.route("/select-dataset", methods=["POST"])
def select_dataset():
    chosen    = request.form.get("dataset", "").strip()
    available = get_available_datasets()
    if chosen == "__all__" or chosen not in available:
        session.pop("selected_dataset", None)
    else:
        session["selected_dataset"] = chosen
    CHAT_SESSIONS[get_session_id()] = []
    return redirect(url_for("studio"))

@app.route("/select-tenant", methods=["POST"])
def select_tenant():
    tenant_id = request.form.get("tenant_id", "").strip()
    if find_tenant(tenant_id):
        session["active_tenant_id"] = tenant_id
    else:
        session.pop("active_tenant_id", None)
    session.pop("selected_dataset", None)
    CHAT_SESSIONS[get_session_id()] = []
    return redirect(url_for("studio"))

@app.route("/clear-chat", methods=["POST"])
def clear_chat():
    CHAT_SESSIONS[get_session_id()] = []
    return redirect(url_for("studio"))

@app.route("/images/<path:filename>")
def images(filename):
    return send_from_directory("images", filename)

@app.route("/ingest-url", methods=["POST"])
def ingest_url():
    url    = request.form.get("url", "").strip()
    ingest = None
    try:
        tenant_id  = get_active_tenant_id()
        data_path  = f"data/{tenant_id}" if tenant_id else DATA_PATH
        collection = tenant_id if tenant_id else "default"
        os.makedirs(data_path, exist_ok=True)
        filename    = filename_from_url(url)
        saved_paths = scrape_full_website(url, filename)
        documents   = load_documents(data_path)
        chunks      = split_documents(documents)
        add_to_chroma(chunks, collection_name=collection)
        query_data._DB = None
        ingest = {"ok": True, "message": f"Scraped {len(saved_paths)} page(s) from {url} and indexed them.",
                  "path": ", ".join(saved_paths[:3]) + ("..." if len(saved_paths) > 3 else "")}
    except Exception as exc:
        ingest = {"ok": False, "message": f"Could not scrape/index that URL: {exc}", "path": None}
    return render_template("studio.html", result=None, query=None,
        chat_history=get_chat_history(), ingest=ingest, url=url, model=MODEL_NAME,
        datasets=get_available_datasets(), selected_dataset=get_selected_dataset())

@app.route("/upload-file", methods=["POST"])
def upload_file():
    uploaded_file = request.files.get("document")
    ingest        = None
    try:
        if not uploaded_file or not uploaded_file.filename:
            raise ValueError("choose a PDF, Word, text, or Markdown file")
        original_name = secure_filename(uploaded_file.filename)
        extension     = os.path.splitext(original_name)[1].lower()
        if extension not in ALLOWED_UPLOAD_EXTENSIONS:
            raise ValueError("only PDF, Word, text, and Markdown files are supported")
        os.makedirs(DATA_PATH, exist_ok=True)
        tenant_id  = get_active_tenant_id()
        data_path  = f"data/{tenant_id}" if tenant_id else DATA_PATH
        collection = tenant_id if tenant_id else "default"
        os.makedirs(data_path, exist_ok=True)
        saved_path = save_upload_as_markdown(uploaded_file, original_name, data_path)
        documents  = load_documents(data_path)
        chunks     = split_documents(documents)
        add_to_chroma(chunks, collection_name=collection)
        query_data._DB = None
        ingest = {"ok": True, "message": f"Uploaded, converted to Markdown, and indexed {original_name}.", "path": saved_path}
    except Exception as exc:
        ingest = {"ok": False, "message": f"Could not upload/index that file: {exc}", "path": None}
    return render_template("studio.html", result=None, query=None,
        chat_history=get_chat_history(), ingest=ingest, url="", model=MODEL_NAME,
        datasets=get_available_datasets(), selected_dataset=get_selected_dataset())

@app.route("/history/clear-all", methods=["POST"])
def clear_all_histories():
    HISTORY_DIR.mkdir(exist_ok=True)
    deleted = 0
    for f in HISTORY_DIR.glob("chat_history_*.json"):
        try:
            f.unlink(); deleted += 1
        except OSError:
            continue
    return jsonify({"deleted": True, "deleted_count": deleted})

# ── File helpers ───────────────────────────────────────────────────────────────
def save_upload_as_markdown(uploaded_file, original_name: str, data_path: str = DATA_PATH) -> str:
    extension     = Path(original_name).suffix.lower()
    markdown_name = unique_markdown_filename(Path(original_name).stem, data_path)
    markdown_path = os.path.join(data_path, markdown_name)
    if extension in {".md", ".markdown"}:   content = read_uploaded_text(uploaded_file)
    elif extension == ".txt":               content = text_to_markdown(read_uploaded_text(uploaded_file), original_name)
    elif extension == ".pdf":               content = pdf_to_markdown(uploaded_file, original_name)
    elif extension in {".docx", ".doc"}:    content = docx_to_markdown(uploaded_file, original_name)
    else:                                   raise ValueError("unsupported file type")
    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write(content.strip() + "\n")
    return markdown_path

def unique_markdown_filename(base_name: str, data_path: str = DATA_PATH) -> str:
    safe_base = secure_filename(base_name) or "uploaded-document"
    candidate = f"{safe_base}.md"; counter = 2
    while os.path.exists(os.path.join(data_path, candidate)):
        candidate = f"{safe_base}-{counter}.md"; counter += 1
    return candidate

def read_uploaded_text(uploaded_file) -> str:
    raw = uploaded_file.read()
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try: return raw.decode(enc)
        except UnicodeDecodeError: continue
    return raw.decode("utf-8", errors="replace")

def text_to_markdown(text: str, original_name: str) -> str:
    title = Path(original_name).stem.replace("_", " ").replace("-", " ").strip()
    return f"# {title or 'Uploaded document'}\n\n{text.strip()}"

def pdf_to_markdown(uploaded_file, original_name: str) -> str:
    uploaded_file.stream.seek(0)
    reader   = PdfReader(uploaded_file.stream)
    title    = Path(original_name).stem.replace("_", " ").replace("-", " ").strip()
    sections = [f"# {title or 'Uploaded PDF'}"]
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text: sections.append(f"## Page {i}\n\n{text}")
    if len(sections) == 1: raise ValueError("could not extract readable text from that PDF")
    return "\n\n".join(sections)

def docx_to_markdown(uploaded_file, original_name: str) -> str:
    if Path(original_name).suffix.lower() == ".doc":
        raise ValueError("legacy .doc files are not supported; please save it as .docx")
    try: from docx import Document
    except ImportError as exc: raise ValueError("Word uploads need python-docx installed") from exc
    uploaded_file.stream.seek(0)
    doc   = Document(uploaded_file.stream)
    title = Path(original_name).stem.replace("_", " ").replace("-", " ").strip()
    lines = [f"# {title or 'Uploaded Word document'}"]
    for p in doc.paragraphs:
        t = p.text.strip()
        if t: lines.append(t)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells): lines.append("| " + " | ".join(cells) + " |")
    if len(lines) == 1: raise ValueError("could not extract readable text from that Word document")
    return "\n\n".join(lines)

def validate_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("enter a full http:// or https:// URL")

def filename_from_url(url: str):
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    path   = parsed.path.strip("/").replace("/", "-")
    base   = f"{domain}-{path}" if path else domain
    safe   = "".join(c if c.isalnum() or c in "-_." else "-" for c in base)
    return safe[:80]

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)