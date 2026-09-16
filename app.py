"""
DocIntel AI - Flask application
Includes:
- Local account authentication
- Gmail OTP verification
- SQLite database
- PDF document upload
- RAG document querying
- Chat history
- Settings / Help / Contact
"""

import os
import json
import sqlite3
import uuid
import secrets
import smtplib
import ssl
import certifi
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from werkzeug.utils import secure_filename

from dotenv import load_dotenv

import rag_engine


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# PATHS / CONFIG
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(BASE_DIR, "docintel.db")

ALLOWED_EXTENSIONS = {"pdf"}

MAX_CONTENT_LENGTH = 25 * 1024 * 1024

OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_SECONDS = 60


os.makedirs(UPLOAD_DIR, exist_ok=True)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "DOCINTEL_SECRET_KEY",
    "docintel-local-dev-secret-change-me"
)

app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    # Documents
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            num_pages INTEGER,
            num_chunks INTEGER,
            uploaded_at TEXT
        )
    """)

    # Chat history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            sources TEXT,
            mode TEXT,
            asked_at TEXT
        )
    """)

    # Users
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    # Contact messages
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contact_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new'
        )
    """)

    # ========================================================
    # OTP CHALLENGES
    # ========================================================

    conn.execute("""
        CREATE TABLE IF NOT EXISTS otp_challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            email TEXT NOT NULL,

            purpose TEXT NOT NULL,

            user_id INTEGER,

            name TEXT,

            password_hash TEXT,

            otp_hash TEXT NOT NULL,

            expires_at TEXT NOT NULL,

            attempts INTEGER NOT NULL DEFAULT 0,

            last_sent_at TEXT NOT NULL,

            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# GENERAL HELPERS
# ============================================================

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def utc_now():
    return datetime.utcnow()


def iso_now():
    return utc_now().isoformat(timespec="seconds")


def parse_datetime(value):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def mask_email(email):
    """
    Example:
    aflah@gmail.com -> a***h@gmail.com
    """
    try:
        name, domain = email.split("@", 1)

        if len(name) <= 2:
            masked_name = name[0] + "***"
        else:
            masked_name = (
                name[0]
                + "***"
                + name[-1]
            )

        return masked_name + "@" + domain

    except Exception:
        return email


# ============================================================
# OTP
# ============================================================

def generate_otp():
    return str(
        secrets.randbelow(900000) + 100000
    )


def hash_otp(otp):
    return generate_password_hash(otp)


def verify_otp_hash(otp_hash, otp):
    return check_password_hash(otp_hash, otp)


def send_otp_email(email, otp, purpose):
    """
    Sends OTP through Gmail SMTP.

    Required environment variables:

    DOCINTEL_MAIL_USERNAME
    DOCINTEL_MAIL_PASSWORD

    Optional:

    DOCINTEL_MAIL_FROM
    """

    sender = os.environ.get(
        "DOCINTEL_MAIL_FROM",
        os.environ.get("DOCINTEL_MAIL_USERNAME")
    )

    password = os.environ.get(
        "DOCINTEL_MAIL_PASSWORD"
    )

    if not sender or not password:
        raise RuntimeError(
            "Gmail SMTP is not configured. "
            "Set DOCINTEL_MAIL_USERNAME and "
            "DOCINTEL_MAIL_PASSWORD in .env"
        )

    if purpose == "signup":
        subject = "DocIntel AI — Verify your email"

        heading = "Verify your DocIntel account"

        intro = (
            "Use the verification code below to complete "
            "your DocIntel AI account registration."
        )

    else:
        subject = "DocIntel AI — Your verification code"

        heading = "Your DocIntel security code"

        intro = (
            "Use the verification code below to complete "
            "your DocIntel AI sign in."
        )

    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = sender
    message["To"] = email

    message.set_content(
        f"""
DocIntel AI
Document Intelligence Platform

{heading}

{intro}

Your verification code:

{otp}

This code expires in {OTP_EXPIRY_MINUTES} minutes.

For your security:
• Do not share this code with anyone.
• DocIntel will never ask you for this code.
• If you did not request this verification, you can safely ignore this email.

—
DocIntel AI
Secure document intelligence
        """.strip()
    )

    context = ssl.create_default_context(cafile=certifi.where())

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=context
    ) as server:

        server.login(
            sender,
            password
        )

        server.send_message(message)


def create_otp_challenge(
    email,
    purpose,
    user_id=None,
    name=None,
    password_hash=None
):
    """
    Creates a new OTP challenge.
    """

    conn = get_db()

    # Remove previous challenge for same email/purpose
    conn.execute(
        """
        DELETE FROM otp_challenges
        WHERE email=? AND purpose=?
        """,
        (email, purpose)
    )

    otp = generate_otp()

    now = utc_now()

    expires = now + timedelta(
        minutes=OTP_EXPIRY_MINUTES
    )

    conn.execute(
        """
        INSERT INTO otp_challenges
        (
            email,
            purpose,
            user_id,
            name,
            password_hash,
            otp_hash,
            expires_at,
            attempts,
            last_sent_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            email,
            purpose,
            user_id,
            name,
            password_hash,
            hash_otp(otp),
            expires.isoformat(timespec="seconds"),
            0,
            now.isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds")
        )
    )

    conn.commit()
    conn.close()

    send_otp_email(
        email,
        otp,
        purpose
    )


def resend_otp(email, purpose):
    """
    Resends an OTP only after cooldown.
    """

    conn = get_db()

    challenge = conn.execute(
        """
        SELECT *
        FROM otp_challenges
        WHERE email=? AND purpose=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (email, purpose)
    ).fetchone()

    if not challenge:
        conn.close()

        return False, "Verification session expired. Please start again."

    last_sent = parse_datetime(
        challenge["last_sent_at"]
    )

    if last_sent:
        elapsed = (
            utc_now() - last_sent
        ).total_seconds()

        if elapsed < OTP_RESEND_SECONDS:

            remaining = int(
                OTP_RESEND_SECONDS - elapsed
            )

            conn.close()

            return (
                False,
                f"Please wait {remaining} seconds before requesting another code."
            )

    otp = generate_otp()

    now = utc_now()

    expires = now + timedelta(
        minutes=OTP_EXPIRY_MINUTES
    )

    conn.execute(
        """
        UPDATE otp_challenges

        SET
            otp_hash=?,
            expires_at=?,
            attempts=0,
            last_sent_at=?

        WHERE id=?
        """,
        (
            hash_otp(otp),
            expires.isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"),
            challenge["id"]
        )
    )

    conn.commit()
    conn.close()

    try:

        send_otp_email(
            email,
            otp,
            purpose
        )

        return True, "A new verification code has been sent."

    except Exception as exc:

        return False, str(exc)


def get_otp_challenge(email, purpose):
    conn = get_db()

    challenge = conn.execute(
        """
        SELECT *
        FROM otp_challenges
        WHERE email=? AND purpose=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (email, purpose)
    ).fetchone()

    conn.close()

    return challenge


# ============================================================
# USER
# ============================================================

def current_user():

    user_id = session.get("user_id")

    if not user_id:
        return None

    conn = get_db()

    user = conn.execute(
        """
        SELECT
            id,
            name,
            email,
            is_admin,
            created_at
        FROM users
        WHERE id=?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    return user


# ============================================================
# AUTH DECORATORS
# ============================================================

def login_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        if not current_user():

            flash(
                "Please sign in to upload and manage documents.",
                "info"
            )

            return redirect(
                url_for(
                    "login",
                    next=request.path
                )
            )

        return view(*args, **kwargs)

    return wrapped


def admin_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        user = current_user()

        if not user:

            return redirect(
                url_for(
                    "login",
                    next=request.path
                )
            )

        if not user["is_admin"]:

            flash(
                "Contact inbox is available to administrators only.",
                "error"
            )

            return redirect(
                url_for("settings_page")
            )

        return view(*args, **kwargs)

    return wrapped


# ============================================================
# TEMPLATE CONTEXT
# ============================================================

@app.context_processor
def inject_user():

    return {
        "current_user": current_user(),
        "global_model_name": getattr(
            rag_engine,
            "OLLAMA_MODEL",
            "local-llm"
        )
    }


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    conn = get_db()

    doc_count = conn.execute(
        "SELECT COUNT(*) AS c FROM documents"
    ).fetchone()["c"]

    chunk_count = conn.execute(
        """
        SELECT COALESCE(
            SUM(num_chunks),
            0
        ) AS c
        FROM documents
        """
    ).fetchone()["c"]

    conn.close()

    return render_template(
        "index.html",
        doc_count=doc_count,
        chunk_count=chunk_count,
        model_name=getattr(
            rag_engine,
            "OLLAMA_MODEL",
            "local-llm"
        )
    )


# ============================================================
# SIGNUP
# ============================================================

@app.route("/signup", methods=["GET", "POST"])
def signup():

    if current_user():
        return redirect(
            url_for("home")
        )

    if request.method == "POST":

        name = (
            request.form.get("name")
            or ""
        ).strip()

        email = (
            request.form.get("email")
            or ""
        ).strip().lower()

        password = (
            request.form.get("password")
            or ""
        )

        confirm = (
            request.form.get("confirm_password")
            or ""
        )

        if (
            not name
            or "@" not in email
            or len(password) < 6
        ):

            flash(
                "Enter your name, a valid email, and a password of at least 6 characters.",
                "error"
            )

            return render_template(
                "auth.html",
                mode="signup"
            )

        if password != confirm:

            flash(
                "Passwords do not match.",
                "error"
            )

            return render_template(
                "auth.html",
                mode="signup"
            )

        conn = get_db()

        exists = conn.execute(
            """
            SELECT id
            FROM users
            WHERE email=?
            """,
            (email,)
        ).fetchone()

        conn.close()

        if exists:

            flash(
                "An account with that email already exists. Please log in.",
                "error"
            )

            return redirect(
                url_for("login")
            )

        # Hash password before putting it into OTP challenge
        password_hash = generate_password_hash(
            password
        )

        try:

            create_otp_challenge(
                email=email,
                purpose="signup",
                name=name,
                password_hash=password_hash
            )

            session["pending_signup_email"] = email

            flash(
                "Verification code sent to your email.",
                "success"
            )

            return redirect(
                url_for(
                    "verify_otp",
                    purpose="signup"
                )
            )

        except Exception as exc:

            flash(
                f"Could not send verification email: {exc}",
                "error"
            )

    return render_template(
        "auth.html",
        mode="signup"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if current_user():

        return redirect(
            url_for("home")
        )

    if request.method == "POST":

        email = (
            request.form.get("email")
            or ""
        ).strip().lower()

        password = (
            request.form.get("password")
            or ""
        )

        next_url = (
            request.args.get("next")
            or request.form.get("next")
            or url_for("home")
        )

        if not next_url.startswith("/"):
            next_url = url_for("home")

        conn = get_db()

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE email=?
            """,
            (email,)
        ).fetchone()

        conn.close()

        if (
            not user
            or not check_password_hash(
                user["password_hash"],
                password
            )
        ):

            flash(
                "Incorrect email or password.",
                "error"
            )

            return render_template(
                "auth.html",
                mode="login",
                next=next_url
            )

        # Password is correct.
        # Now require email OTP.

        try:

            create_otp_challenge(
                email=email,
                purpose="login",
                user_id=user["id"]
            )

            session["pending_login_email"] = email
            session["pending_login_next"] = next_url

            flash(
                "A verification code has been sent to your email.",
                "success"
            )

            return redirect(
                url_for(
                    "verify_otp",
                    purpose="login"
                )
            )

        except Exception as exc:

            flash(
                f"Could not send verification email: {exc}",
                "error"
            )

    return render_template(
        "auth.html",
        mode="login",
        next=request.args.get(
            "next",
            ""
        )
    )


# ============================================================
# VERIFY OTP
# ============================================================

@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():

    purpose = (
        request.args.get("purpose")
        or "login"
    )

    if purpose not in {
        "login",
        "signup"
    }:

        return redirect(
            url_for("login")
        )

    if purpose == "login":

        email = session.get(
            "pending_login_email"
        )

    else:

        email = session.get(
            "pending_signup_email"
        )

    if not email:

        flash(
            "Your verification session has expired. Please start again.",
            "error"
        )

        return redirect(
            url_for(
                "login"
                if purpose == "login"
                else "signup"
            )
        )

    if request.method == "POST":

        otp = (
            request.form.get("otp")
            or ""
        ).strip()

        # Remove spaces
        otp = otp.replace(" ", "")

        if (
            len(otp) != OTP_LENGTH
            or not otp.isdigit()
        ):

            flash(
                "Enter the 6-digit verification code.",
                "error"
            )

            return render_template(
                "auth.html",
                mode="verify",
                purpose=purpose,
                email=mask_email(email)
            )

        challenge = get_otp_challenge(
            email,
            purpose
        )

        if not challenge:

            flash(
                "Verification code expired. Please request a new one.",
                "error"
            )

            return render_template(
                "auth.html",
                mode="verify",
                purpose=purpose,
                email=mask_email(email)
            )

        # Check attempts

        if challenge["attempts"] >= OTP_MAX_ATTEMPTS:

            flash(
                "Too many incorrect attempts. Please request a new code.",
                "error"
            )

            return render_template(
                "auth.html",
                mode="verify",
                purpose=purpose,
                email=mask_email(email)
            )

        # Check expiration

        expires_at = parse_datetime(
            challenge["expires_at"]
        )

        if (
            not expires_at
            or utc_now() > expires_at
        ):

            flash(
                "This verification code has expired. Please request a new one.",
                "error"
            )

            return render_template(
                "auth.html",
                mode="verify",
                purpose=purpose,
                email=mask_email(email)
            )

        # Verify OTP

        if not verify_otp_hash(
            challenge["otp_hash"],
            otp
        ):

            conn = get_db()

            conn.execute(
                """
                UPDATE otp_challenges
                SET attempts = attempts + 1
                WHERE id=?
                """,
                (challenge["id"],)
            )

            conn.commit()
            conn.close()

            remaining = max(
                0,
                OTP_MAX_ATTEMPTS
                - challenge["attempts"]
                - 1
            )

            flash(
                f"Incorrect verification code. {remaining} attempt(s) remaining.",
                "error"
            )

            return render_template(
                "auth.html",
                mode="verify",
                purpose=purpose,
                email=mask_email(email)
            )

        # ====================================================
        # LOGIN VERIFIED
        # ====================================================

        if purpose == "login":

            user_id = challenge["user_id"]

            # Clear old session
            session.clear()

            # Create authenticated session
            session["user_id"] = user_id

            next_url = (
                request.form.get("next")
                or url_for("home")
            )

            if not next_url.startswith("/"):
                next_url = url_for("home")

            # Delete OTP challenge
            conn = get_db()

            conn.execute(
                """
                DELETE FROM otp_challenges
                WHERE id=?
                """,
                (challenge["id"],)
            )

            conn.commit()
            conn.close()

            flash(
                "Email verified. Welcome back to DocIntel.",
                "success"
            )

            return redirect(
                next_url
            )

        # ====================================================
        # SIGNUP VERIFIED
        # ====================================================

        if purpose == "signup":

            conn = get_db()

            # Check again in case account was created elsewhere
            existing = conn.execute(
                """
                SELECT id
                FROM users
                WHERE email=?
                """,
                (email,)
            ).fetchone()

            if existing:

                conn.close()

                session.clear()

                flash(
                    "An account with this email already exists. Please log in.",
                    "error"
                )

                return redirect(
                    url_for("login")
                )

            # First registered user becomes admin
            count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM users
                """
            ).fetchone()["c"]

            is_first = 1 if count == 0 else 0

            cur = conn.execute(
                """
                INSERT INTO users
                (
                    name,
                    email,
                    password_hash,
                    is_admin,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    challenge["name"],
                    email,
                    challenge["password_hash"],
                    is_first,
                    iso_now()
                )
            )

            user_id = cur.lastrowid

            conn.execute(
                """
                DELETE FROM otp_challenges
                WHERE id=?
                """,
                (challenge["id"],)
            )

            conn.commit()
            conn.close()

            session.clear()

            session["user_id"] = user_id

            flash(
                "Email verified. Your DocIntel account has been created.",
                "success"
            )

            return redirect(
                url_for("home")
            )

    return render_template(
        "auth.html",
        mode="verify",
        purpose=purpose,
        email=mask_email(email),
        next=session.get(
            "pending_login_next",
            ""
        )
    )


# ============================================================
# RESEND OTP
# ============================================================

@app.route("/resend-otp", methods=["POST"])
def resend_otp_route():

    purpose = (
        request.form.get("purpose")
        or "login"
    )

    if purpose == "login":

        email = session.get(
            "pending_login_email"
        )

    else:

        email = session.get(
            "pending_signup_email"
        )

    if not email:

        flash(
            "Verification session expired. Please start again.",
            "error"
        )

        return redirect(
            url_for(
                "login"
                if purpose == "login"
                else "signup"
            )
        )

    success, message = resend_otp(
        email,
        purpose
    )

    flash(
        message,
        "success" if success else "error"
    )

    return redirect(
        url_for(
            "verify_otp",
            purpose=purpose
        )
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    flash(
        "You have been logged out.",
        "success"
    )

    return redirect(
        url_for("home")
    )


# ============================================================
# UPLOAD PAGE
# ============================================================

@app.route("/upload")
@login_required
def upload_page():

    conn = get_db()

    docs = conn.execute(
        """
        SELECT *
        FROM documents
        ORDER BY uploaded_at DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "upload.html",
        docs=docs
    )


# ============================================================
# UPLOAD DOCUMENTS
# ============================================================

@app.route("/upload", methods=["POST"])
@login_required
def upload_files():

    files = request.files.getlist("files")

    if (
        not files
        or all(
            f.filename == ""
            for f in files
        )
    ):

        flash(
            "Please choose at least one PDF file.",
            "error"
        )

        return redirect(
            url_for("upload_page")
        )

    added = 0
    skipped = []

    conn = get_db()

    for f in files:

        if not f.filename:
            continue

        if not allowed_file(
            f.filename
        ):

            skipped.append(
                f.filename
            )

            continue

        doc_id = uuid.uuid4().hex[:12]

        safe_name = secure_filename(
            f.filename
        )

        save_path = os.path.join(
            UPLOAD_DIR,
            f"{doc_id}_{safe_name}"
        )

        f.save(save_path)

        try:

            chunks = rag_engine.process_pdf(
                save_path
            )

            if not chunks:

                skipped.append(
                    f"{safe_name} (no readable text)"
                )

                os.remove(save_path)

                continue

            num_pages = len(
                {
                    c["page"]
                    for c in chunks
                }
            )

            num_chunks = (
                rag_engine.store.add_document(
                    doc_id,
                    safe_name,
                    chunks
                )
            )

            conn.execute(
                """
                INSERT INTO documents
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    safe_name,
                    num_pages,
                    num_chunks,
                    iso_now()
                )
            )

            added += 1

        except Exception as exc:

            skipped.append(
                f"{safe_name} ({exc})"
            )

            if os.path.exists(
                save_path
            ):

                os.remove(
                    save_path
                )

    conn.commit()
    conn.close()

    if added:

        flash(
            f"Processed {added} document(s) successfully.",
            "success"
        )

    if skipped:

        flash(
            "Some files were not indexed: "
            + ", ".join(skipped),
            "error"
        )

    return redirect(
        url_for("documents_page")
    )


# ============================================================
# DOCUMENTS
# ============================================================

@app.route("/documents")
def documents_page():

    conn = get_db()

    docs = conn.execute(
        """
        SELECT *
        FROM documents
        ORDER BY uploaded_at DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "documents.html",
        docs=docs
    )


# ============================================================
# DELETE DOCUMENT
# ============================================================

@app.route(
    "/documents/<doc_id>/delete",
    methods=["POST"]
)
@login_required
def delete_document(doc_id):

    rag_engine.store.delete_document(
        doc_id
    )

    conn = get_db()

    row = conn.execute(
        """
        SELECT filename
        FROM documents
        WHERE doc_id=?
        """,
        (doc_id,)
    ).fetchone()

    conn.execute(
        """
        DELETE FROM documents
        WHERE doc_id=?
        """,
        (doc_id,)
    )

    conn.commit()
    conn.close()

    if row:

        prefix = f"{doc_id}_"

        for name in os.listdir(
            UPLOAD_DIR
        ):

            if name.startswith(
                prefix
            ):

                try:
                    os.remove(
                        os.path.join(
                            UPLOAD_DIR,
                            name
                        )
                    )

                except OSError:
                    pass

    flash(
        "Document removed.",
        "success"
    )

    return redirect(
        url_for("documents_page")
    )


# ============================================================
# ASK AI
# ============================================================

@app.route(
    "/api/ask",
    methods=["POST"]
)
def api_ask():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    question = (
        data.get("question")
        or ""
    ).strip()

    if not question:

        return jsonify(
            {
                "error":
                "Please enter a question."
            }
        ), 400

    result = (
        rag_engine.answer_question(
            question
        )
    )

    conn = get_db()

    conn.execute(
        """
        INSERT INTO chat_history
        (
            question,
            answer,
            sources,
            mode,
            asked_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            question,
            result["answer"],
            json.dumps(
                result.get(
                    "sources",
                    []
                )
            ),
            result.get("mode"),
            iso_now()
        )
    )

    conn.commit()
    conn.close()

    return jsonify(result)


# ============================================================
# HEALTH
# ============================================================

@app.route("/api/health")
def api_health():

    ollama = False

    try:

        ollama = (
            rag_engine.ollama_available()
        )

    except Exception:
        pass

    return jsonify(
        {
            "rag_ready":
                not rag_engine.store.is_empty(),

            "llm_available":
                ollama,

            "model":
                getattr(
                    rag_engine,
                    "OLLAMA_MODEL",
                    "local-llm"
                )
        }
    )


# ============================================================
# HISTORY
# ============================================================

@app.route("/history")
def history_page():

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM chat_history
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    history = []

    for r in rows:

        history.append(
            {
                "id": r["id"],
                "question": r["question"],
                "answer": r["answer"],
                "sources":
                    json.loads(
                        r["sources"]
                    )
                    if r["sources"]
                    else [],
                "mode": r["mode"],
                "asked_at": r["asked_at"]
            }
        )

    return render_template(
        "history.html",
        history=history
    )


@app.route(
    "/history/<int:entry_id>/delete",
    methods=["POST"]
)
def delete_history_entry(entry_id):

    conn = get_db()

    conn.execute(
        """
        DELETE FROM chat_history
        WHERE id=?
        """,
        (entry_id,)
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("history_page")
    )


@app.route(
    "/history/clear",
    methods=["POST"]
)
def clear_history():

    conn = get_db()

    conn.execute(
        "DELETE FROM chat_history"
    )

    conn.commit()
    conn.close()

    flash(
        "History cleared.",
        "success"
    )

    return redirect(
        url_for("history_page")
    )


# ============================================================
# SETTINGS
# ============================================================

@app.route("/settings")
@login_required
def settings_page():

    return render_template(
        "settings.html",
        user=current_user()
    )


# ============================================================
# HELP
# ============================================================

@app.route("/help")
def help_page():

    return render_template(
        "help.html"
    )


# ============================================================
# CONTACT
# ============================================================

@app.route(
    "/contact",
    methods=["GET", "POST"]
)
def contact_page():

    if request.method == "POST":

        name = (
            request.form.get("name")
            or ""
        ).strip()

        email = (
            request.form.get("email")
            or ""
        ).strip()

        subject = (
            request.form.get("subject")
            or "General enquiry"
        ).strip()

        message = (
            request.form.get("message")
            or ""
        ).strip()

        if (
            not name
            or not email
            or not message
            or "@" not in email
        ):

            flash(
                "Please provide a valid name, email and message.",
                "error"
            )

            return render_template(
                "contact.html",
                name=name,
                email=email,
                subject=subject,
                message=message
            )

        conn = get_db()

        conn.execute(
            """
            INSERT INTO contact_messages
            (
                name,
                email,
                subject,
                message,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                name,
                email,
                subject,
                message,
                iso_now()
            )
        )

        conn.commit()
        conn.close()

        flash(
            "Your message was sent successfully.",
            "success"
        )

        return redirect(
            url_for("contact_page")
        )

    return render_template(
        "contact.html"
    )


# ============================================================
# CONTACT INBOX
# ============================================================

@app.route("/contact-inbox")
@admin_required
def contact_inbox():

    conn = get_db()

    messages = conn.execute(
        """
        SELECT *
        FROM contact_messages
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "contact_inbox.html",
        messages=messages
    )


@app.route(
    "/contact-inbox/<int:message_id>/read",
    methods=["POST"]
)
@admin_required
def contact_mark_read(message_id):

    conn = get_db()

    conn.execute(
        """
        UPDATE contact_messages
        SET status='read'
        WHERE id=?
        """,
        (message_id,)
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("contact_inbox")
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True,
        port=5000
    )
