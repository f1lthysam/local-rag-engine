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
from urllib.parse import urlparse
os.environ["PYTHONWARNINGS"] = "ignore"
logging.disable(logging.CRITICAL)

from flask import Flask, render_template, request
from populate_database import add_to_chroma, load_documents, split_documents
import query_data
from scrape_web import scrape_and_save, scrape_full_website

app = Flask(__name__, template_folder=".")
app.config["TEMPLATES_AUTO_RELOAD"] = True

MODEL_NAME = "gemini-3.1-flash-lite"

@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    query  = None
    ingest = None

    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            result = query_data.query_rag_web(query)

    return render_template(
        "index.html",
        result=result,
        query=query,
        ingest=ingest,
        url="",
        model=MODEL_NAME,
    )


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
        ingest=ingest,
        url=url,
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