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
os.environ["PYTHONWARNINGS"] = "ignore"
logging.disable(logging.CRITICAL)

from flask import Flask, render_template, request
from query_data import query_rag_web   # import the web-compatible version

app = Flask(__name__, template_folder=".")

MODEL_NAME = "phi"

@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    query  = None

    if request.method == "POST":
        query = request.form.get("query", "").strip()
        if query:
            result = query_rag_web(query)

    return render_template("index.html", result=result, query=query, model=MODEL_NAME)

if __name__ == "__main__":
    app.run(debug=False, port=5000)
