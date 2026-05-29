"""
app.py — Flask frontend server for the RAG chatbot.

Run:
    pip install flask
    python app.py

Then open: http://localhost:5000
"""

import warnings
warnings.filterwarnings("ignore")
import os, logging
from uuid import uuid4
from urllib.parse import urlparse
os.environ["PYTHONWARNINGS"] = "ignore"
logging.disable(logging.CRITICAL)

from flask import Flask, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename
from populate_database import add_to_chroma, load_documents, split_documents
import query_data
from scrape_web import scrape_and_save, scrape_full_website

app = Flask(__name__, template_folder=".")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = os.getenv("FLASK_SECRET_KEY", "local-rag-dev-secret")

MODEL_NAME = "gemini-3.1-flash-lite"
MAX_CHAT_TURNS = 20
CHAT_SESSIONS = {}
DATA_PATH = "data"
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".md"}


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


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    query  = None
    ingest = None
    chat_history = get_chat_history()
    if chat_history:
        result = chat_history[-1].get("result")

    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            result = query_data.query_rag_web(query, chat_history=chat_history)
            append_chat_turn(query, result)

    return render_template(
        "index.html",
        result=result,
        query=query,
        chat_history=chat_history,
        ingest=ingest,
        url="",
        model=MODEL_NAME,
    )


@app.route("/clear-chat", methods=["POST"])
def clear_chat():
    CHAT_SESSIONS[get_session_id()] = []
    return redirect(url_for("index"))


@app.route("/images/<path:filename>")
def images(filename):
    return send_from_directory("images", filename)


@app.route("/ingest-url", methods=["POST"])
def ingest_url():
    url = request.form.get("url", "").strip()
    ingest = None

    try:
        validate_url(url)
        filename = filename_from_url(url)

        # Scrape the full website (follows internal links up to 20 pages)
        saved_paths = scrape_full_website(url, filename)

        documents = load_documents()
        chunks = split_documents(documents)
        add_to_chroma(chunks)
        query_data._DB = None

        ingest = {
            "ok": True,
            "message": f"Scraped {len(saved_paths)} page(s) from {url} and indexed them.",
            "path": ", ".join(saved_paths[:3]) + ("..." if len(saved_paths) > 3 else ""),
        }
    except Exception as exc:
        ingest = {
            "ok": False,
            "message": f"Could not scrape/index that URL: {exc}",
            "path": None,
        }

    return render_template(
        "index.html",
        result=None,
        query=None,
        chat_history=get_chat_history(),
        ingest=ingest,
        url=url,
        model=MODEL_NAME,
    )


@app.route("/upload-file", methods=["POST"])
def upload_file():
    uploaded_file = request.files.get("document")
    ingest = None

    try:
        if not uploaded_file or not uploaded_file.filename:
            raise ValueError("choose a PDF or Markdown file")

        original_name = secure_filename(uploaded_file.filename)
        extension = os.path.splitext(original_name)[1].lower()
        if extension not in ALLOWED_UPLOAD_EXTENSIONS:
            raise ValueError("only .pdf and .md files are supported")

        os.makedirs(DATA_PATH, exist_ok=True)
        saved_path = os.path.join(DATA_PATH, original_name)
        uploaded_file.save(saved_path)

        documents = load_documents()
        chunks = split_documents(documents)
        add_to_chroma(chunks)
        query_data._DB = None

        ingest = {
            "ok": True,
            "message": f"Uploaded and indexed {original_name}.",
            "path": saved_path,
        }
    except Exception as exc:
        ingest = {
            "ok": False,
            "message": f"Could not upload/index that file: {exc}",
            "path": None,
        }

    return render_template(
        "index.html",
        result=None,
        query=None,
        chat_history=get_chat_history(),
        ingest=ingest,
        url="",
        model=MODEL_NAME,
    )


def validate_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("enter a full http:// or https:// URL")


def filename_from_url(url: str):
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    path = parsed.path.strip("/").replace("/", "-")
    base = f"{domain}-{path}" if path else domain
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in base)
    return f"{safe[:80]}"

if __name__ == "__main__":
    app.run(debug=True, port=5000)
