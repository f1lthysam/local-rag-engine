"""
run_servers.py — Start Flask (:5000) and FastAPI (:8000) together.

Usage:
    python run_servers.py

Stop with Ctrl+C — kills both processes cleanly.
"""
import subprocess
import sys
import signal
import os

procs = []

def shutdown(sig, frame):
    print("\n[run_servers] Shutting down both servers...")
    for p in procs:
        p.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

print("[run_servers] Starting Flask on :5000 ...")
flask_proc = subprocess.Popen(
    [sys.executable, "app.py"],
    env={**os.environ, "FLASK_ENV": "development"},
)
procs.append(flask_proc)

print("[run_servers] Starting FastAPI on :8000 ...")
uvicorn_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"],
)
procs.append(uvicorn_proc)

print("[run_servers] Both servers running. Press Ctrl+C to stop.\n")

# Wait — exit if either process dies unexpectedly
for p in procs:
    p.wait()