"""
client_auth.py — Client authentication for the RAG SaaS platform.

Handles:
  - Client registration with email + bcrypt password hashing
  - Email verification via Gmail (Flask-Mail + itsdangerous token)
  - Login / logout with Flask session
  - SQLite storage for client accounts
  - Session guard decorator for protected portal routes

Install dependencies first:
  pip install bcrypt flask-mail itsdangerous

Add to your .env:
  MAIL_USERNAME=yourgmail@gmail.com
  MAIL_PASSWORD=xxxx xxxx xxxx xxxx   (Gmail App Password, NOT your real password)
  MAIL_DEFAULT_SENDER=yourgmail@gmail.com
"""

import os
import sqlite3
import bcrypt
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import session, redirect, url_for, flash, current_app
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

# ── Constants ─────────────────────────────────────────────────────────────────

DB_PATH          = Path("client_users.db")
TOKEN_EXPIRY_SEC = 3600          # verification link expires in 1 hour
SALT             = "email-verify-salt"

mail = Mail()   # initialised in create_app / register_client_auth(app)


# ── App wiring ────────────────────────────────────────────────────────────────

def register_client_auth(app):
    """
    Call this once in app.py after creating your Flask app:

        from client_auth import register_client_auth
        register_client_auth(app)
    """
    app.config.setdefault("MAIL_SERVER",   "smtp.gmail.com")
    app.config.setdefault("MAIL_PORT",     587)
    app.config.setdefault("MAIL_USE_TLS",  True)
    app.config["MAIL_USERNAME"]       = os.getenv("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"]       = os.getenv("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

    mail.init_app(app)
    init_db()


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    """Return a SQLite connection. Creates the DB file if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # rows behave like dicts
    return conn


def init_db():
    """Create the clients table if it doesn't already exist."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    UNIQUE NOT NULL,
                password_hash TEXT    NOT NULL,
                tenant_id     TEXT    UNIQUE NOT NULL,
                company_name  TEXT    NOT NULL,
                is_verified   INTEGER NOT NULL DEFAULT 0,
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT    NOT NULL,
                verified_at   TEXT
            )
        """)
        conn.commit()
    print("[client_auth] Database ready →", DB_PATH)


# ── Token helpers ─────────────────────────────────────────────────────────────

def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key)


def generate_verification_token(email: str) -> str:
    return _serializer().dumps(email, salt=SALT)


def confirm_verification_token(token: str) -> str | None:
    """
    Returns the email address if the token is valid and not expired.
    Returns None if invalid or expired.
    """
    try:
        email = _serializer().loads(token, salt=SALT, max_age=TOKEN_EXPIRY_SEC)
        return email
    except (SignatureExpired, BadSignature):
        return None


# ── Email sender ──────────────────────────────────────────────────────────────

def send_verification_email(email: str, token: str):
    """Send the verification link to the client's email address."""
    verify_url = url_for("client_portal.verify_email", token=token, _external=True)

    msg = Message(
        subject="Verify your RAG Portal account",
        recipients=[email],
        html=f"""
        <div style="font-family: sans-serif; max-width: 480px; margin: auto;">
            <h2 style="color: #1a1a2e;">Almost there!</h2>
            <p>Click the button below to verify your email address and activate your account.</p>
            <a href="{verify_url}"
               style="display:inline-block; padding:12px 28px; background:#4f46e5;
                      color:#fff; border-radius:8px; text-decoration:none;
                      font-weight:600; margin:16px 0;">
                Verify Email
            </a>
            <p style="color:#888; font-size:13px;">
                This link expires in 1 hour.<br>
                If you didn't sign up, ignore this email.
            </p>
        </div>
        """,
    )
    mail.send(msg)


# ── Registration ──────────────────────────────────────────────────────────────

def register_client(email: str, password: str, company_name: str, tenant_id: str) -> dict:
    """
    Register a new client.

    Returns:
        {"ok": True}  on success
        {"ok": False, "error": "reason"}  on failure
    """
    email = email.strip().lower()

    # Basic validation
    if not email or "@" not in email:
        return {"ok": False, "error": "Invalid email address."}
    if len(password) < 8:
        return {"ok": False, "error": "Password must be at least 8 characters."}
    if not company_name.strip():
        return {"ok": False, "error": "Company name is required."}

    # Hash the password — bcrypt never stores plaintext
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO clients
                    (email, password_hash, tenant_id, company_name, is_verified, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (email, pw_hash, tenant_id, company_name.strip(),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
    except sqlite3.IntegrityError as e:
        if "email" in str(e):
            return {"ok": False, "error": "An account with this email already exists."}
        if "tenant_id" in str(e):
            return {"ok": False, "error": "Tenant ID already taken. Choose another."}
        return {"ok": False, "error": "Registration failed. Please try again."}

    # Send verification email
    try:
        token = generate_verification_token(email)
        send_verification_email(email, token)
    except Exception as e:
        # Account created but email failed — client can request resend later
        print(f"[client_auth] Email send failed: {e}")
        return {"ok": True, "email_sent": False}

    return {"ok": True, "email_sent": True}


# ── Email verification ────────────────────────────────────────────────────────

def verify_client_email(token: str) -> dict:
    """
    Confirm a verification token and mark the account as verified.

    Returns:
        {"ok": True,  "email": email}   on success
        {"ok": False, "error": reason}  on failure
    """
    email = confirm_verification_token(token)
    if not email:
        return {"ok": False, "error": "Verification link is invalid or has expired."}

    with get_db() as conn:
        row = conn.execute(
            "SELECT is_verified FROM clients WHERE email = ?", (email,)
        ).fetchone()

        if not row:
            return {"ok": False, "error": "Account not found."}
        if row["is_verified"]:
            return {"ok": True, "email": email, "already_verified": True}

        conn.execute(
            "UPDATE clients SET is_verified = 1, verified_at = ? WHERE email = ?",
            (datetime.now(timezone.utc).isoformat(), email),
        )
        conn.commit()

    return {"ok": True, "email": email, "already_verified": False}


# ── Resend verification ───────────────────────────────────────────────────────

def resend_verification(email: str) -> dict:
    email = email.strip().lower()
    with get_db() as conn:
        row = conn.execute(
            "SELECT is_verified FROM clients WHERE email = ?", (email,)
        ).fetchone()

    if not row:
        return {"ok": False, "error": "No account found with that email."}
    if row["is_verified"]:
        return {"ok": False, "error": "This account is already verified."}

    try:
        token = generate_verification_token(email)
        send_verification_email(email, token)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"Could not send email: {e}"}


# ── Login ─────────────────────────────────────────────────────────────────────

def login_client(email: str, password: str) -> dict:
    """
    Verify credentials and populate Flask session on success.

    Returns:
        {"ok": True}              on success (session is now set)
        {"ok": False, "error"}    on failure
    """
    email = email.strip().lower()

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE email = ?", (email,)
        ).fetchone()

    if not row:
        return {"ok": False, "error": "Invalid email or password."}

    if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return {"ok": False, "error": "Invalid email or password."}

    if not row["is_verified"]:
        return {"ok": False, "error": "Please verify your email before logging in.",
                "needs_verification": True}

    if not row["is_active"]:
        return {"ok": False, "error": "Your account has been deactivated. Contact support."}

    # Populate session
    session["client_id"]      = row["id"]
    session["client_email"]   = row["email"]
    session["client_tenant"]  = row["tenant_id"]
    session["client_company"] = row["company_name"]
    session.permanent = True

    return {"ok": True}


# ── Logout ────────────────────────────────────────────────────────────────────

def logout_client():
    session.pop("client_id",      None)
    session.pop("client_email",   None)
    session.pop("client_tenant",  None)
    session.pop("client_company", None)


# ── Password change ───────────────────────────────────────────────────────────

def change_password(client_id: int, old_password: str, new_password: str) -> dict:
    if len(new_password) < 8:
        return {"ok": False, "error": "New password must be at least 8 characters."}

    with get_db() as conn:
        row = conn.execute(
            "SELECT password_hash FROM clients WHERE id = ?", (client_id,)
        ).fetchone()

    if not row:
        return {"ok": False, "error": "Account not found."}

    if not bcrypt.checkpw(old_password.encode(), row["password_hash"].encode()):
        return {"ok": False, "error": "Current password is incorrect."}

    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    with get_db() as conn:
        conn.execute(
            "UPDATE clients SET password_hash = ? WHERE id = ?",
            (new_hash, client_id),
        )
        conn.commit()

    return {"ok": True}


# ── Session guard decorator ───────────────────────────────────────────────────

def client_login_required(f):
    """
    Decorator for portal routes. Redirects to login if not authenticated.

    Usage:
        @app.route("/client/portal")
        @client_login_required
        def portal():
            ...
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if "client_id" not in session:
            flash("Please log in to access your portal.", "warning")
            return redirect(url_for("client_portal.login"))
        return f(*args, **kwargs)
    return decorated


# ── Admin helpers ─────────────────────────────────────────────────────────────

def get_all_clients() -> list[dict]:
    """Used by admin dashboard to list all registered clients."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, company_name, tenant_id, is_verified, is_active, created_at "
            "FROM clients ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_client_by_tenant(tenant_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
    return dict(row) if row else None


def deactivate_client(client_id: int):
    with get_db() as conn:
        conn.execute("UPDATE clients SET is_active = 0 WHERE id = ?", (client_id,))
        conn.commit()
