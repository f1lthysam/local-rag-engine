# How to wire the client portal into app.py
# Add these lines to your existing app.py

# ── 1. At the top of app.py, add these imports ────────────────────────────────

from client_auth import register_client_auth
from client_portal import client_portal_bp


# ── 2. After `app = Flask(...)`, add these two lines ──────────────────────────

app.register_blueprint(client_portal_bp)   # registers all /client/* routes
register_client_auth(app)                  # sets up mail + creates DB


# ── 3. Install new dependencies ───────────────────────────────────────────────
#
#   pip install bcrypt flask-mail itsdangerous
#
# Then add to your .env file:
#
#   MAIL_USERNAME=yourgmail@gmail.com
#   MAIL_PASSWORD=xxxx xxxx xxxx xxxx
#   MAIL_DEFAULT_SENDER=yourgmail@gmail.com


# ── 4. Move HTML files into templates/ ────────────────────────────────────────
#
#   client_portal.html       → templates/client_portal.html
#   (from client_auth_pages.html, split into three files:)
#   client_login.html        → templates/client_login.html
#   client_register.html     → templates/client_register.html
#   client_resend_verify.html → templates/client_resend_verify.html


# ── 5. What gets created automatically ───────────────────────────────────────
#
#   client_users.db    → SQLite database for client accounts (auto-created)
#
#   Routes now available:
#   GET/POST  /client/register
#   GET       /client/verify/<token>
#   GET/POST  /client/resend-verify
#   GET/POST  /client/login
#   GET       /client/logout
#   GET       /client/portal          ← main dashboard (login required)
#   GET       /client/api/analytics
#   GET       /client/api/chat-history
#   GET/POST  /client/api/documents
#   DELETE    /client/api/documents/<name>
#   POST      /client/api/scrape
#   GET       /client/api/scrape/stream
#   POST      /client/api/change-password
#   GET       /client/api/widget-code


# ── Example of what app.py looks like after wiring ───────────────────────────

"""
app = Flask(__name__, template_folder="templates")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.secret_key = os.getenv("FLASK_SECRET_KEY", "local-rag-dev-secret")

# ← ADD THESE TWO LINES
app.register_blueprint(client_portal_bp)
register_client_auth(app)

# ... rest of your existing app.py unchanged ...
"""
