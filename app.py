"""
ИнфоТренер: тесты по информатике с ролями, SQLite и серверной проверкой ответов.
Версия 2.0 с улучшенной безопасностью, серверными попытками и группами.
"""

from __future__ import annotations

import csv
import functools
import io
import json
import os
import random
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Flask, Response, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

# Configuration
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
QUESTIONS_PATH = DATA_DIR / "questions.json"
DB_PATH = DATA_DIR / "infotrainer.db"
SECRET_PATH = DATA_DIR / "secret.key"
LOCALES_DIR = BASE_DIR / "locales"

# Validation patterns
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u0400-\u04FF]{3,32}$")
OPTION_IDS = ("a", "b", "c", "d", "e", "f")
ROLES = ("student", "teacher", "admin")
DIFFICULTIES = ("easy", "medium", "hard")
ICONS = ("cpu", "code", "database", "hash", "globe", "shield", "zap", "book", "network", "palette")

# Demo accounts only created in demo mode
DEMO_USERS = (
    ("teacher", "Teacher123", "teacher"),
    ("student", "Student123", "student"),
)

# Rate limiting storage
_rate_limit_store: dict[str, list[float]] = {}


def slugify(text: str) -> str:
    """Транслитерация русских букв в латиницу, нижний регистр, дефисы для id тем."""
    translit_map = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    lowered = text.strip().lower()
    chars = []
    for ch in lowered:
        if ch in translit_map:
            chars.append(translit_map[ch])
        elif ch.isalnum() or ch in (" ", "-", "_"):
            chars.append(ch)
    joined = "".join(chars)
    joined = re.sub(r"[\s_]+", "-", joined)
    joined = re.sub(r"[^a-z0-9\-]", "", joined)
    joined = re.sub(r"-+", "-", joined).strip("-")
    return joined or "topic"


def read_or_create_secret() -> str:
    """Постоянный ключ сессий, чтобы вход не сбрасывался при перезапуске."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text(encoding="utf-8").strip()
    value = secrets.token_hex(32)
    SECRET_PATH.write_text(value, encoding="utf-8")
    return value


# Flask app initialization
app = Flask(__name__)
app.config["SECRET_KEY"] = read_or_create_secret()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("HTTPS", "false").lower() == "true"

# Demo mode check
IS_DEMO_MODE = os.getenv("APP_ENV", "demo").lower() == "demo"
TEACHER_INVITE_CODE = os.getenv("TEACHER_INVITE_CODE", secrets.token_urlsafe(16))


def get_db() -> sqlite3.Connection:
    """Одно соединение SQLite на запрос, с внешними ключами."""
    if "db" not in g:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(DB_PATH)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


@app.teardown_appcontext
def close_db(_error: BaseException | None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    """Создаёт таблицы, применяет миграции и сидирует темы и пользователей."""
    db = get_db()
    
    # Create tables with proper schema
    db.executescript(
        """
        -- Users table
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('student', 'teacher', 'admin')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT
        );

        -- Topics table
        CREATE TABLE IF NOT EXISTS topics (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            subtitle TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT 'hash',
            time_limit_sec INTEGER NOT NULL DEFAULT 480,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );

        -- Questions table
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id TEXT NOT NULL,
            qid TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL CHECK (type IN ('single', 'multiple')),
            text TEXT NOT NULL,
            options_json TEXT DEFAULT '[]',
            correct_json TEXT DEFAULT '[]',
            difficulty TEXT NOT NULL DEFAULT 'medium' CHECK (difficulty IN ('easy', 'medium', 'hard')),
            explanation TEXT NOT NULL DEFAULT '',
            bloom_level TEXT DEFAULT 'understand' CHECK (bloom_level IN ('remember', 'understand', 'apply', 'analyze')),
            learning_outcome TEXT DEFAULT '',
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );

        -- Options table
        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            option_id TEXT NOT NULL,
            option_text TEXT NOT NULL,
            is_correct INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_options_qid ON options(question_id);

        -- Results table
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            topic_id TEXT NOT NULL,
            topic_title TEXT NOT NULL,
            score INTEGER NOT NULL,
            max_score INTEGER NOT NULL,
            percent REAL NOT NULL,
            duration_sec INTEGER NOT NULL,
            finished_at TEXT NOT NULL,
            review_json TEXT NOT NULL,
            attempt_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_results_user ON results(user_id);
        CREATE INDEX IF NOT EXISTS idx_results_topic ON results(topic_id);

        -- Test attempts table (server-side attempts)
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            topic_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            deadline_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'submitted', 'expired')),
            question_order_json TEXT NOT NULL,
            answers_json TEXT NOT NULL DEFAULT '{}',
            duration_sec INTEGER,
            suspicious_flag INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_attempts_user ON attempts(user_id);
        CREATE INDEX IF NOT EXISTS idx_attempts_status ON attempts(status);

        -- Groups table
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            course TEXT,
            specialty TEXT,
            invite_code TEXT NOT NULL UNIQUE,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
        );

        -- Group members table
        CREATE TABLE IF NOT EXISTS group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(group_id, user_id),
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        -- Assignments table
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            topic_id TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            deadline_at TEXT,
            max_attempts INTEGER DEFAULT 1,
            mode TEXT DEFAULT 'practice' CHECK (mode IN ('practice', 'exam')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )

    # Demo accounts only in demo mode
    if IS_DEMO_MODE:
        for name, password, role in DEMO_USERS:
            found = db.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if found is None:
                db.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                    (name, generate_password_hash(password), role),
                )

    db.commit()


# Security utilities
def check_rate_limit(identifier: str, max_attempts: int = 5, window_sec: int = 300) -> tuple[bool, int]:
    """Check if the identifier has exceeded rate limit. Returns (allowed, remaining)."""
    now = time.time()
    if identifier not in _rate_limit_store:
        _rate_limit_store[identifier] = []
    
    # Clean old entries
    _rate_limit_store[identifier] = [
        t for t in _rate_limit_store[identifier] 
        if now - t < window_sec
    ]
    
    attempts = len(_rate_limit_store[identifier])
    if attempts >= max_attempts:
        return False, 0
    
    return True, max_attempts - attempts


def record_attempt(identifier: str):
    """Record a failed attempt for rate limiting."""
    now = time.time()
    if identifier not in _rate_limit_store:
        _rate_limit_store[identifier] = []
    _rate_limit_store[identifier].append(now)


def require_csrf(f: Callable) -> Callable:
    """Decorator to require CSRF token or X-Requested-With header for state-changing operations."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # Check for X-Requested-With header (common for AJAX)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return f(*args, **kwargs)
        
        # Check for CSRF token in header
        csrf_token = request.headers.get("X-CSRF-Token")
        if csrf_token and csrf_token == session.get("csrf_token"):
            return f(*args, **kwargs)
        
        # For GET requests, allow without CSRF
        if request.method == "GET":
            return f(*args, **kwargs)
        
        return jsonify({"error": "CSRF validation failed", "code": "CSRF_ERROR"}), 403
    return decorated


def login_required(f: Callable) -> Callable:
    """Decorator to require login."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized", "code": "AUTH_REQUIRED"}), 401
        return f(*args, **kwargs)
    return decorated


def role_required(*roles: str):
    """Decorator factory to require specific role(s)."""
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                return jsonify({"error": "Unauthorized", "code": "AUTH_REQUIRED"}), 401
            db = get_db()
            user = db.execute(
                "SELECT role FROM users WHERE id = ?", (session["user_id"],)
            ).fetchone()
            if not user or user["role"] not in roles:
                return jsonify({"error": "Forbidden", "code": "INSUFFICIENT_PRIVILEGES"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# Routes
@app.route("/")
def index():
    """Render main page."""
    # Generate CSRF token for session
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return render_template("index.html")


@app.route("/api/config")
def get_config():
    """Get public configuration."""
    return jsonify({
        "demo_mode": IS_DEMO_MODE,
        "csrf_token": session.get("csrf_token"),
    })


@app.route("/api/locales/<lang>")
def get_locale(lang: str):
    """Get translation file."""
    safe_lang = "kk" if lang == "kk" else "ru"
    file_path = LOCALES_DIR / f"{safe_lang}.json"
    if file_path.exists():
        return file_path.read_text(encoding="utf-8"), 200, {"Content-Type": "application/json"}
    return jsonify({}), 200


@app.route("/api/register", methods=["POST"])
@require_csrf
def register():
    """Register new user. Teacher role requires invite code."""
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = (data.get("role") or "student").strip().lower()
    invite_code = (data.get("invite_code") or "").strip()

    # Validate username
    if not USERNAME_RE.match(username):
        return jsonify({
            "error": "Invalid username. Use 3-32 letters, numbers, or Cyrillic characters.",
            "code": "INVALID_USERNAME"
        }), 400

    # Validate password
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters.", "code": "WEAK_PASSWORD"}), 400
    if not (re.search(r"[a-z]", password) and re.search(r"[A-Z]", password) and re.search(r"\d", password)):
        return jsonify({
            "error": "Password must contain uppercase, lowercase, and a number.",
            "code": "WEAK_PASSWORD"
        }), 400

    # Role validation - teacher requires invite code
    if role == "teacher":
        if not invite_code or invite_code != TEACHER_INVITE_CODE:
            return jsonify({
                "error": "Invalid or missing teacher invite code.",
                "code": "INVALID_INVITE_CODE"
            }), 403
    else:
        role = "student"  # Default to student if not teacher with valid code

    db = get_db()
    
    # Check if username exists
    existing = db.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
        (username,)
    ).fetchone()
    if existing:
        return jsonify({"error": "Username already taken.", "code": "USERNAME_EXISTS"}), 409

    # Create user
    password_hash = generate_password_hash(password)
    cursor = db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, password_hash, role)
    )
    db.commit()

    return jsonify({
        "success": True,
        "message": "Registration successful. Please log in.",
        "user_id": cursor.lastrowid
    }), 201


@app.route("/api/login", methods=["POST"])
@require_csrf
def login():
    """Login user with rate limiting."""
    data = request.get_json() or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    
    # Rate limiting by IP + username combination
    client_id = f"{request.remote_addr}:{username.lower()}"
    allowed, remaining = check_rate_limit(client_id, max_attempts=5, window_sec=300)
    
    if not allowed:
        return jsonify({
            "error": "Too many login attempts. Please try again in 5 minutes.",
            "code": "RATE_LIMIT_EXCEEDED"
        }), 429

    db = get_db()
    user = db.execute(
        "SELECT id, username, password_hash, role FROM users WHERE username = ? COLLATE NOCASE",
        (username,)
    ).fetchone()

    # Generic error message to prevent username enumeration
    if not user or not check_password_hash(user["password_hash"], password):
        record_attempt(client_id)
        return jsonify({
            "error": "Invalid username or password.",
            "code": "INVALID_CREDENTIALS"
        }), 401

    # Update last login
    db.execute(
        "UPDATE users SET last_login = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), user["id"])
    )
    db.commit()

    # Set session
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]

    # Regenerate CSRF token on login
    session["csrf_token"] = secrets.token_urlsafe(32)

    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"]
        },
        "csrf_token": session["csrf_token"]
    })


@app.route("/api/logout", methods=["POST"])
@require_csrf
def logout():
    """Logout user."""
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully."})


@app.route("/api/me")
@login_required
def get_current_user():
    """Get current user info."""
    db = get_db()
    user = db.execute(
        "SELECT id, username, role, created_at FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()
    
    if not user:
        return jsonify({"error": "User not found", "code": "USER_NOT_FOUND"}), 404
    
    return jsonify({
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "created_at": user["created_at"],
        "csrf_token": session.get("csrf_token")
    })


# Initialize database on first request
@app.before_request
def ensure_db():
    """Ensure database is initialized."""
    if not hasattr(g, '_db_initialized'):
        init_db()
        g._db_initialized = True


# Topic and Question API Routes

@app.route("/api/topics")
@login_required
def get_topics():
    """Get all topics with question counts."""
    db = get_db()
    rows = db.execute(
        """
        SELECT t.id, t.title, t.subtitle, t.icon, t.time_limit_sec,
               COUNT(q.id) AS question_count
        FROM topics t
        LEFT JOIN questions q ON q.topic_id = t.id
        GROUP BY t.id
        ORDER BY t.rowid ASC
        """
    ).fetchall()
    
    return jsonify([
        {
            "id": row["id"],
            "title": row["title"],
            "subtitle": row["subtitle"],
            "icon": row["icon"],
            "time_limit_sec": row["time_limit_sec"],
            "question_count": row["question_count"]
        }
        for row in rows
    ])


@app.route("/api/topics/<topic_id>")
@login_required
def get_topic(topic_id: str):
    """Get topic with all questions (for test)."""
    db = get_db()
    
    # Get topic info
    topic = db.execute(
        "SELECT id, title, subtitle, icon, time_limit_sec FROM topics WHERE id = ?",
        (topic_id,)
    ).fetchone()
    
    if not topic:
        return jsonify({"error": "Topic not found", "code": "TOPIC_NOT_FOUND"}), 404
    
    # Get questions with options
    q_rows = db.execute(
        """
        SELECT id, qid, type, text, difficulty, explanation, bloom_level
        FROM questions
        WHERE topic_id = ?
        ORDER BY id ASC
        """,
        (topic_id,)
    ).fetchall()
    
    questions = []
    for row in q_rows:
        # Get options for this question
        opt_rows = db.execute(
            """
            SELECT option_id, option_text
            FROM options
            WHERE question_id = ?
            ORDER BY id ASC
            """,
            (row["id"],)
        ).fetchall()
        
        options = [
            {"id": o["option_id"], "text": o["option_text"]}
            for o in opt_rows
        ]
        
        questions.append({
            "id": row["qid"],
            "qid": row["qid"],
            "type": row["type"],
            "text": row["text"],
            "difficulty": row["difficulty"],
            "explanation": row["explanation"],
            "bloom_level": row["bloom_level"],
            "options": options
        })
    
    return jsonify({
        "id": topic["id"],
        "title": topic["title"],
        "subtitle": topic["subtitle"],
        "icon": topic["icon"],
        "time_limit_sec": topic["time_limit_sec"],
        "question_count": len(questions),
        "questions": questions
    })


# Attempts API (Server-side test attempts)

@app.route("/api/attempts", methods=["POST"])
@login_required
def create_attempt():
    """Create a new test attempt."""
    data = request.get_json() or {}
    topic_id = (data.get("topic_id") or "").strip()
    
    if not topic_id:
        return jsonify({"error": "Topic ID is required", "code": "MISSING_TOPIC"}), 400
    
    db = get_db()
    
    # Get topic info
    topic = db.execute(
        "SELECT time_limit_sec FROM topics WHERE id = ?",
        (topic_id,)
    ).fetchone()
    
    if not topic:
        return jsonify({"error": "Topic not found", "code": "TOPIC_NOT_FOUND"}), 404
    
    # Get questions for this topic
    q_rows = db.execute(
        "SELECT id, qid FROM questions WHERE topic_id = ?",
        (topic_id,)
    ).fetchall()
    
    if not q_rows:
        return jsonify({"error": "No questions available for this topic", "code": "NO_QUESTIONS"}), 400
    
    # Create random question order
    question_order = [row["qid"] for row in q_rows]
    random.shuffle(question_order)
    
    # Calculate deadline
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=topic["time_limit_sec"])
    
    # Create attempt
    cursor = db.execute(
        """
        INSERT INTO attempts (user_id, topic_id, started_at, deadline_at, question_order_json, answers_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session["user_id"],
            topic_id,
            now.isoformat(),
            deadline.isoformat(),
            json.dumps(question_order),
            json.dumps({})
        )
    )
    db.commit()
    
    return jsonify({
        "success": True,
        "attempt_id": cursor.lastrowid,
        "started_at": now.isoformat(),
        "deadline_at": deadline.isoformat(),
        "time_remaining": topic["time_limit_sec"]
    })


@app.route("/api/attempts/<int:attempt_id>")
@login_required
def get_attempt(attempt_id: int):
    """Get attempt status and details."""
    db = get_db()
    
    attempt = db.execute(
        """
        SELECT id, topic_id, started_at, deadline_at, finished_at, status,
               question_order_json, answers_json
        FROM attempts
        WHERE id = ? AND user_id = ?
        """,
        (attempt_id, session["user_id"])
    ).fetchone()
    
    if not attempt:
        return jsonify({"error": "Attempt not found", "code": "ATTEMPT_NOT_FOUND"}), 404
    
    # Calculate remaining time
    deadline = datetime.fromisoformat(attempt["deadline_at"])
    now = datetime.now(timezone.utc)
    remaining = max(0, int((deadline - now).total_seconds()))
    
    # Check if expired
    status = attempt["status"]
    if status == "in_progress" and remaining <= 0:
        status = "expired"
        db.execute(
            "UPDATE attempts SET status = 'expired' WHERE id = ?",
            (attempt_id,)
        )
        db.commit()
    
    return jsonify({
        "id": attempt["id"],
        "topic_id": attempt["topic_id"],
        "started_at": attempt["started_at"],
        "deadline_at": attempt["deadline_at"],
        "finished_at": attempt["finished_at"],
        "status": status,
        "question_order": json.loads(attempt["question_order_json"]),
        "answers": json.loads(attempt["answers_json"]),
        "time_remaining": remaining
    })


@app.route("/api/attempts/<int:attempt_id>/answer", methods=["PUT"])
@login_required
def save_answer(attempt_id: int):
    """Save an answer for the attempt (auto-save)."""
    data = request.get_json() or {}
    question_id = (data.get("question_id") or "").strip()
    answer = data.get("answer")  # Can be string or list
    
    if not question_id:
        return jsonify({"error": "Question ID is required", "code": "MISSING_QUESTION"}), 400
    
    db = get_db()
    
    # Get attempt
    attempt = db.execute(
        """
        SELECT deadline_at, status, answers_json
        FROM attempts
        WHERE id = ? AND user_id = ?
        """,
        (attempt_id, session["user_id"])
    ).fetchone()
    
    if not attempt:
        return jsonify({"error": "Attempt not found", "code": "ATTEMPT_NOT_FOUND"}), 404
    
    if attempt["status"] != "in_progress":
        return jsonify({"error": "Attempt is not active", "code": "ATTEMPT_INACTIVE"}), 400
    
    # Check deadline
    deadline = datetime.fromisoformat(attempt["deadline_at"])
    if datetime.now(timezone.utc) > deadline:
        db.execute(
            "UPDATE attempts SET status = 'expired' WHERE id = ?",
            (attempt_id,)
        )
        db.commit()
        return jsonify({"error": "Time limit exceeded", "code": "TIME_EXPIRED"}), 400
    
    # Update answers
    answers = json.loads(attempt["answers_json"])
    answers[question_id] = answer
    
    db.execute(
        "UPDATE attempts SET answers_json = ? WHERE id = ?",
        (json.dumps(answers), attempt_id)
    )
    db.commit()
    
    return jsonify({"success": True, "saved": True})


@app.route("/api/attempts/<int:attempt_id>/submit", methods=["POST"])
@login_required
def submit_attempt(attempt_id: int):
    """Submit the attempt and calculate results."""
    db = get_db()
    
    # Get attempt
    attempt = db.execute(
        """
        SELECT topic_id, started_at, deadline_at, status, question_order_json, answers_json
        FROM attempts
        WHERE id = ? AND user_id = ?
        """,
        (attempt_id, session["user_id"])
    ).fetchone()
    
    if not attempt:
        return jsonify({"error": "Attempt not found", "code": "ATTEMPT_NOT_FOUND"}), 404
    
    if attempt["status"] not in ("in_progress", "expired"):
        return jsonify({"error": "Attempt already submitted", "code": "ALREADY_SUBMITTED"}), 400
    
    # Calculate duration
    started = datetime.fromisoformat(attempt["started_at"])
    finished = datetime.now(timezone.utc)
    duration = int((finished - started).total_seconds())
    
    # Check for suspicious speed (less than 2 seconds per question)
    question_order = json.loads(attempt["question_order_json"])
    suspicious = len(question_order) > 0 and duration < len(question_order) * 2
    
    # Get questions with correct answers
    q_rows = db.execute(
        """
        SELECT q.qid, q.type, q.text, q.explanation, q.difficulty,
               o.option_id, o.option_text, o.is_correct
        FROM questions q
        JOIN options o ON o.question_id = q.id
        WHERE q.topic_id = ?
        ORDER BY q.id ASC
        """,
        (attempt["topic_id"],)
    ).fetchall()
    
    # Build question data
    questions_data = {}
    for row in q_rows:
        qid = row["qid"]
        if qid not in questions_data:
            questions_data[qid] = {
                "qid": qid,
                "type": row["type"],
                "text": row["text"],
                "explanation": row["explanation"],
                "difficulty": row["difficulty"],
                "options": [],
                "correct": []
            }
        questions_data[qid]["options"].append({
            "id": row["option_id"],
            "text": row["option_text"]
        })
        if row["is_correct"]:
            questions_data[qid]["correct"].append(row["option_id"])
    
    # Calculate score using partial scoring for multiple choice
    answers = json.loads(attempt["answers_json"])
    score = 0
    max_score = len(questions_data)
    review = []
    
    for qid, qdata in questions_data.items():
        user_answer = answers.get(qid, [] if qdata["type"] == "multiple" else "")
        correct = qdata["correct"]
        
        if qdata["type"] == "single":
            is_correct = user_answer == correct[0] if correct else False
            if is_correct:
                score += 1
        else:  # multiple - partial scoring
            if correct:
                selected_correct = sum(1 for a in (user_answer if isinstance(user_answer, list) else [user_answer]) if a in correct)
                selected_incorrect = sum(1 for a in (user_answer if isinstance(user_answer, list) else [user_answer]) if a not in correct)
                question_score = max(0, (selected_correct - selected_incorrect) / len(correct))
                score += question_score
        
        review.append({
            "qid": qid,
            "text": qdata["text"],
            "type": qdata["type"],
            "user_answer": user_answer,
            "correct": correct,
            "explanation": qdata["explanation"],
            "difficulty": qdata["difficulty"]
        })
    
    percent = round((score / max_score) * 100, 1) if max_score > 0 else 0
    
    # Get topic title
    topic_row = db.execute(
        "SELECT title FROM topics WHERE id = ?", (attempt["topic_id"],)
    ).fetchone()
    topic_title = topic_row["title"] if topic_row else attempt["topic_id"]
    
    # Save result
    db.execute(
        """
        INSERT INTO results 
        (user_id, topic_id, topic_title, score, max_score, percent, duration_sec, finished_at, review_json, attempt_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session["user_id"],
            attempt["topic_id"],
            topic_title,
            int(score),
            max_score,
            percent,
            duration,
            finished.isoformat(),
            json.dumps(review, ensure_ascii=False),
            attempt_id
        )
    )
    
    # Update attempt status
    db.execute(
        """
        UPDATE attempts 
        SET status = 'submitted', finished_at = ?, duration_sec = ?, suspicious_flag = ?
        WHERE id = ?
        """,
        (finished.isoformat(), duration, 1 if suspicious else 0, attempt_id)
    )
    
    db.commit()
    
    return jsonify({
        "success": True,
        "result": {
            "score": round(score, 2),
            "max_score": max_score,
            "percent": percent,
            "duration_sec": duration,
            "suspicious": suspicious,
            "review": review
        }
    })


@app.route("/api/results")
@login_required
def get_results():
    """Get user's test results."""
    db = get_db()
    rows = db.execute(
        """
        SELECT r.id, r.topic_id, r.topic_title, r.score, r.max_score, r.percent,
               r.duration_sec, r.finished_at, a.suspicious_flag
        FROM results r
        LEFT JOIN attempts a ON a.id = r.attempt_id
        WHERE r.user_id = ?
        ORDER BY r.finished_at DESC
        """,
        (session["user_id"],)
    ).fetchall()
    
    return jsonify([
        {
            "id": row["id"],
            "topic_id": row["topic_id"],
            "topic_title": row["topic_title"],
            "score": row["score"],
            "max_score": row["max_score"],
            "percent": row["percent"],
            "duration_sec": row["duration_sec"],
            "finished_at": row["finished_at"],
            "suspicious": bool(row["suspicious_flag"])
        }
        for row in rows
    ])


@app.route("/api/results/<int:result_id>")
@login_required
def get_result_detail(result_id: int):
    """Get detailed result with review."""
    db = get_db()
    row = db.execute(
        """
        SELECT r.topic_id, r.topic_title, r.score, r.max_score, r.percent,
               r.duration_sec, r.finished_at, r.review_json
        FROM results r
        WHERE r.id = ? AND r.user_id = ?
        """,
        (result_id, session["user_id"])
    ).fetchone()
    
    if not row:
        return jsonify({"error": "Result not found", "code": "RESULT_NOT_FOUND"}), 404
    
    return jsonify({
        "topic_id": row["topic_id"],
        "topic_title": row["topic_title"],
        "score": row["score"],
        "max_score": row["max_score"],
        "percent": row["percent"],
        "duration_sec": row["duration_sec"],
        "finished_at": row["finished_at"],
        "review": json.loads(row["review_json"])
    })


# Teacher/admin routes

@app.route("/api/teacher/stats")
@role_required("teacher", "admin")
def get_teacher_stats():
    """Get teacher dashboard stats."""
    db = get_db()
    
    # Total students
    student_count = db.execute(
        "SELECT COUNT(*) AS cnt FROM users WHERE role = 'student'"
    ).fetchone()["cnt"]
    
    # Total topics
    topic_count = db.execute(
        "SELECT COUNT(*) AS cnt FROM topics"
    ).fetchone()["cnt"]
    
    # Total questions
    question_count = db.execute(
        "SELECT COUNT(*) AS cnt FROM questions"
    ).fetchone()["cnt"]
    
    # Recent attempts
    recent_attempts = db.execute(
        """
        SELECT a.id, a.started_at, a.status, t.title AS topic_title,
               u.username
        FROM attempts a
        JOIN topics t ON t.id = a.topic_id
        JOIN users u ON u.id = a.user_id
        ORDER BY a.started_at DESC
        LIMIT 10
        """
    ).fetchall()
    
    return jsonify({
        "stats": {
            "students": student_count,
            "topics": topic_count,
            "questions": question_count
        },
        "recent_attempts": [
            {
                "id": row["id"],
                "started_at": row["started_at"],
                "status": row["status"],
                "topic_title": row["topic_title"],
                "username": row["username"]
            }
            for row in recent_attempts
        ]
    })


@app.route("/api/teacher/questions", methods=["POST"])
@role_required("teacher", "admin")
def create_question():
    """Create a new question."""
    data = request.get_json() or {}
    
    topic_id = (data.get("topic_id") or "").strip()
    qid = (data.get("qid") or "").strip()
    q_type = (data.get("type") or "single").strip()
    text = (data.get("text") or "").strip()
    options = data.get("options") or []
    correct = data.get("correct") or []
    difficulty = (data.get("difficulty") or "medium").strip()
    explanation = (data.get("explanation") or "").strip()
    
    # Validation
    if not topic_id or not text or not options or not correct:
        return jsonify({"error": "Missing required fields", "code": "MISSING_FIELDS"}), 400
    
    if difficulty not in DIFFICULTIES:
        difficulty = "medium"
    
    if q_type not in ("single", "multiple"):
        q_type = "single"
    
    # Generate qid if not provided
    if not qid:
        qid = f"{topic_id}_{secrets.token_hex(4)}"
    
    db = get_db()
    
    # Check if qid exists
    existing = db.execute(
        "SELECT id FROM questions WHERE qid = ?",
        (qid,)
    ).fetchone()
    
    if existing:
        return jsonify({"error": "Question ID already exists", "code": "QID_EXISTS"}), 409
    
    # Insert question
    cursor = db.execute(
        """
        INSERT INTO questions 
        (topic_id, qid, type, text, options_json, correct_json, difficulty, explanation, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            topic_id, qid, q_type, text,
            json.dumps(options, ensure_ascii=False),
            json.dumps(correct, ensure_ascii=False),
            difficulty, explanation, session["user_id"]
        )
    )
    
    question_db_id = cursor.lastrowid
    
    # Insert options
    for opt in options:
        opt_id = opt.get("id", "")
        opt_text = opt.get("text", "")
        is_correct = 1 if opt_id in correct else 0
        
        db.execute(
            """
            INSERT INTO options (question_id, option_id, option_text, is_correct)
            VALUES (?, ?, ?, ?)
            """,
            (question_db_id, opt_id, opt_text, is_correct)
        )
    
    db.commit()
    
    return jsonify({
        "success": True,
        "question_id": qid,
        "message": "Question created successfully"
    }), 201


# Invite code endpoint for teachers
@app.route("/api/invite-code")
@login_required
def get_invite_code():
    """Get teacher invite code (admin only)."""
    # Check if user is admin
    db = get_db()
    user = db.execute(
        "SELECT role FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()
    
    if not user or user["role"] not in ("admin", "teacher"):
        return jsonify({"error": "Forbidden", "code": "INSUFFICIENT_PRIVILEGES"}), 403
    
    return jsonify({
        "invite_code": TEACHER_INVITE_CODE if user["role"] == "admin" else "[REDACTED]"
    })


# Additional API routes for topics, questions, attempts, and results

@app.route("/api/topics")
@login_required
def get_topics():
    """Get all topics with question counts."""
    db = get_db()
    rows = db.execute(
        """
        SELECT t.id, t.title, t.subtitle, t.icon, t.time_limit_sec,
               COUNT(q.id) AS question_count
        FROM topics t
        LEFT JOIN questions q ON q.topic_id = t.id
        GROUP BY t.id
        ORDER BY t.rowid ASC
        """
    ).fetchall()
    
    return jsonify([{
        "id": row["id"],
        "title": row["title"],
        "subtitle": row["subtitle"],
        "icon": row["icon"],
        "time_limit_sec": row["time_limit_sec"],
        "question_count": row["question_count"]
    } for row in rows])


@app.route("/api/topics/<topic_id>")
@login_required
def get_topic(topic_id: str):
    """Get topic with questions for test."""
    db = get_db()
    
    # Get topic info
    topic = db.execute(
        "SELECT id, title, subtitle, icon, time_limit_sec FROM topics WHERE id = ?",
        (topic_id,)
    ).fetchone()
    
    if not topic:
        return jsonify({"error": "Topic not found", "code": "TOPIC_NOT_FOUND"}), 404
    
    # Get questions with options
    q_rows = db.execute(
        """
        SELECT q.id, q.qid, q.type, q.text, q.difficulty, q.explanation, q.bloom_level
        FROM questions q
        WHERE q.topic_id = ?
        ORDER BY q.id ASC
        """,
        (topic_id,)
    ).fetchall()
    
    questions = []
    for row in q_rows:
        # Get options for this question
        opt_rows = db.execute(
            """
            SELECT option_id, option_text
            FROM options
            WHERE question_id = ?
            ORDER BY id ASC
            """,
            (row["id"],)
        ).fetchall()
        
        options = [{"id": o["option_id"], "text": o["option_text"]} for o in opt_rows]
        
        questions.append({
            "id": row["qid"],
            "type": row["type"],
            "text": row["text"],
            "difficulty": row["difficulty"],
            "explanation": row["explanation"],
            "bloom_level": row["bloom_level"],
            "options": options
        })
    
    return jsonify({
        "topic": {
            "id": topic["id"],
            "title": topic["title"],
            "subtitle": topic["subtitle"],
            "icon": topic["icon"],
            "time_limit_sec": topic["time_limit_sec"]
        },
        "questions": questions
    })


@app.route("/api/attempts", methods=["POST"])
@login_required
def create_attempt():
    """Create a new test attempt (server-side timer start)."""
    data = request.get_json() or {}
    topic_id = data.get("topic_id")
    
    if not topic_id:
        return jsonify({"error": "Topic ID required", "code": "MISSING_TOPIC"}), 400
    
    db = get_db()
    
    # Get topic time limit
    topic = db.execute(
        "SELECT time_limit_sec FROM topics WHERE id = ?",
        (topic_id,)
    ).fetchone()
    
    if not topic:
        return jsonify({"error": "Topic not found", "code": "TOPIC_NOT_FOUND"}), 404
    
    # Check for existing in-progress attempt
    existing = db.execute(
        """
        SELECT id, deadline_at FROM attempts
        WHERE user_id = ? AND topic_id = ? AND status = 'in_progress'
        ORDER BY id DESC LIMIT 1
        """,
        (session["user_id"], topic_id)
    ).fetchone()
    
    if existing:
        # Return existing attempt
        return jsonify({
            "attempt_id": existing["id"],
            "deadline_at": existing["deadline_at"],
            "message": "Resuming existing attempt"
        })
    
    # Create new attempt
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=topic["time_limit_sec"])
    
    # Get question order (shuffle on server)
    q_rows = db.execute(
        "SELECT qid FROM questions WHERE topic_id = ?",
        (topic_id,)
    ).fetchall()
    
    question_ids = [row["qid"] for row in q_rows]
    random.shuffle(question_ids)
    
    cursor = db.execute(
        """
        INSERT INTO attempts (user_id, topic_id, started_at, deadline_at, question_order_json, answers_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session["user_id"],
            topic_id,
            now.isoformat(),
            deadline.isoformat(),
            json.dumps(question_ids),
            json.dumps({})
        )
    )
    db.commit()
    
    return jsonify({
        "attempt_id": cursor.lastrowid,
        "started_at": now.isoformat(),
        "deadline_at": deadline.isoformat(),
        "time_limit_sec": topic["time_limit_sec"]
    }), 201


@app.route("/api/attempts/<int:attempt_id>/answer", methods=["PUT"])
@login_required
def save_answer(attempt_id: int):
    """Auto-save an answer during a test."""
    data = request.get_json() or {}
    question_id = data.get("question_id")
    answer = data.get("answer")
    
    if question_id is None or answer is None:
        return jsonify({"error": "Question ID and answer required", "code": "MISSING_DATA"}), 400
    
    db = get_db()
    
    # Get attempt and verify ownership
    attempt = db.execute(
        """
        SELECT id, deadline_at, status, answers_json 
        FROM attempts 
        WHERE id = ? AND user_id = ?
        """,
        (attempt_id, session["user_id"])
    ).fetchone()
    
    if not attempt:
        return jsonify({"error": "Attempt not found", "code": "ATTEMPT_NOT_FOUND"}), 404
    
    if attempt["status"] != "in_progress":
        return jsonify({"error": "Attempt is not in progress", "code": "ATTEMPT_COMPLETED"}), 400
    
    # Check deadline
    deadline = datetime.fromisoformat(attempt["deadline_at"])
    if datetime.now(timezone.utc) > deadline:
        # Mark as expired
        db.execute(
            "UPDATE attempts SET status = 'expired' WHERE id = ?",
            (attempt_id,)
        )
        db.commit()
        return jsonify({"error": "Time limit exceeded", "code": "TIME_EXPIRED"}), 400
    
    # Update answers
    answers = json.loads(attempt["answers_json"])
    answers[question_id] = answer
    
    db.execute(
        "UPDATE attempts SET answers_json = ? WHERE id = ?",
        (json.dumps(answers), attempt_id)
    )
    db.commit()
    
    return jsonify({"success": True, "message": "Answer saved"})


@app.route("/api/attempts/<int:attempt_id>/submit", methods=["POST"])
@login_required
def submit_attempt(attempt_id: int):
    """Submit a test attempt and calculate results."""
    db = get_db()
    
    # Get attempt
    attempt = db.execute(
        """
        SELECT id, topic_id, started_at, deadline_at, status, question_order_json, answers_json
        FROM attempts
        WHERE id = ? AND user_id = ?
        """,
        (attempt_id, session["user_id"])
    ).fetchone()
    
    if not attempt:
        return jsonify({"error": "Attempt not found", "code": "ATTEMPT_NOT_FOUND"}), 404
    
    if attempt["status"] != "in_progress":
        return jsonify({"error": "Attempt already submitted", "code": "ALREADY_SUBMITTED"}), 400
    
    # Check if deadline passed
    now = datetime.now(timezone.utc)
    deadline = datetime.fromisoformat(attempt["deadline_at"])
    time_expired = now > deadline
    
    # Calculate duration
    started_at = datetime.fromisoformat(attempt["started_at"])
    duration_sec = int((now - started_at).total_seconds())
    
    # Get questions with correct answers
    question_order = json.loads(attempt["question_order_json"])
    answers = json.loads(attempt["answers_json"])
    
    # Get topic info
    topic = db.execute(
        "SELECT title FROM topics WHERE id = ?",
        (attempt["topic_id"],)
    ).fetchone()
    
    topic_title = topic["title"] if topic else "Unknown"
    
    # Calculate score
    score = 0
    max_score = 0
    review = []
    
    for qid in question_order:
        # Get question data
        q_row = db.execute(
            """
            SELECT q.id, q.type, q.text, q.explanation
            FROM questions q
            WHERE q.qid = ?
            """,
            (qid,)
        ).fetchone()
        
        if not q_row:
            continue
        
        # Get correct options
        opt_rows = db.execute(
            """
            SELECT option_id, option_text, is_correct
            FROM options
            WHERE question_id = ?
            """,
            (q_row["id"],)
        ).fetchall()
        
        correct_ids = [o["option_id"] for o in opt_rows if o["is_correct"]]
        all_options = [{"id": o["option_id"], "text": o["option_text"]} for o in opt_rows]
        
        # Get user's answer
        user_answer = answers.get(qid, [])
        if not isinstance(user_answer, list):
            user_answer = [user_answer]
        
        # Calculate points for this question
        if q_row["type"] == "single":
            max_score += 1
            if user_answer and user_answer[0] in correct_ids:
                score += 1
                q_score = 1
            else:
                q_score = 0
        else:  # multiple
            max_score += len(correct_ids)
            q_score = 0
            for ans in user_answer:
                if ans in correct_ids:
                    score += 1
                    q_score += 1
                else:
                    # Penalty for wrong answer
                    q_score -= 1
            q_score = max(0, q_score)
        
        review.append({
            "question_id": qid,
            "question_text": q_row["text"],
            "user_answer": user_answer,
            "correct_answer": correct_ids,
            "options": all_options,
            "explanation": q_row["explanation"],
            "score": q_score,
            "max_score": len(correct_ids) if q_row["type"] == "multiple" else 1
        })
    
    # Calculate percentage
    percent = round((score / max_score) * 100, 1) if max_score > 0 else 0
    
    # Flag suspiciously fast attempts
    suspicious = False
    if duration_sec < len(question_order) * 2:  # Less than 2 seconds per question
        suspicious = True
    
    # Update attempt status
    db.execute(
        """
        UPDATE attempts 
        SET status = ?, finished_at = ?, duration_sec = ?, suspicious_flag = ?
        WHERE id = ?
        """,
        ("expired" if time_expired else "submitted", now.isoformat(), duration_sec, 1 if suspicious else 0, attempt_id)
    )
    
    # Save result
    review_json = json.dumps({
        "questions": review,
        "started_at": attempt["started_at"],
        "finished_at": now.isoformat(),
        "time_expired": time_expired,
        "suspicious": suspicious
    }, ensure_ascii=False)
    
    db.execute(
        """
        INSERT INTO results (user_id, topic_id, topic_title, score, max_score, percent, duration_sec, finished_at, review_json, attempt_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (session["user_id"], attempt["topic_id"], topic_title, score, max_score, percent, duration_sec, now.isoformat(), review_json, attempt_id)
    )
    
    db.commit()
    
    return jsonify({
        "success": True,
        "attempt_id": attempt_id,
        "score": score,
        "max_score": max_score,
        "percent": percent,
        "duration_sec": duration_sec,
        "time_expired": time_expired,
        "suspicious": suspicious,
        "review": review
    })


@app.route("/api/results")
@login_required
def get_results():
    """Get user's test results."""
    db = get_db()
    rows = db.execute(
        """
        SELECT r.id, r.topic_id, r.topic_title, r.score, r.max_score, r.percent,
               r.duration_sec, r.finished_at
        FROM results r
        WHERE r.user_id = ?
        ORDER BY r.finished_at DESC
        """,
        (session["user_id"],)
    ).fetchall()
    
    return jsonify([{
        "id": row["id"],
        "topic_id": row["topic_id"],
        "topic_title": row["topic_title"],
        "score": row["score"],
        "max_score": row["max_score"],
        "percent": row["percent"],
        "duration_sec": row["duration_sec"],
        "finished_at": row["finished_at"]
    } for row in rows])


@app.route("/api/results/<int:result_id>")
@login_required
def get_result_detail(result_id: int):
    """Get detailed result with review."""
    db = get_db()
    row = db.execute(
        """
        SELECT r.*, t.title as topic_title
        FROM results r
        JOIN topics t ON t.id = r.topic_id
        WHERE r.id = ? AND r.user_id = ?
        """,
        (result_id, session["user_id"])
    ).fetchone()
    
    if not row:
        return jsonify({"error": "Result not found", "code": "RESULT_NOT_FOUND"}), 404
    
    review = json.loads(row["review_json"])
    
    return jsonify({
        "id": row["id"],
        "topic_id": row["topic_id"],
        "topic_title": row["topic_title"],
        "score": row["score"],
        "max_score": row["max_score"],
        "percent": row["percent"],
        "duration_sec": row["duration_sec"],
        "finished_at": row["finished_at"],
        "review": review
    })


# Teacher/admin routes
@app.route("/api/admin/questions", methods=[GET", "POST"])
@role_required("teacher", "admin")
def admin_questions():
    """List or create questions (teacher/admin only)."""
    db = get_db()
    
    if request.method == "GET":
        topic_id = request.args.get("topic_id")
        
        query = """
            SELECT q.id, q.qid, q.topic_id, q.type, q.text, q.difficulty, 
                   q.explanation, q.bloom_level, q.created_at,
                   t.title as topic_title
            FROM questions q
            JOIN topics t ON t.id = q.topic_id
        """
        params = []
        
        if topic_id:
            query += " WHERE q.topic_id = ?"
            params.append(topic_id)
        
        query += " ORDER BY q.id DESC"
        
        rows = db.execute(query, params).fetchall()
        
        return jsonify([{
            "id": row["id"],
            "qid": row["qid"],
            "topic_id": row["topic_id"],
            "topic_title": row["topic_title"],
            "type": row["type"],
            "text": row["text"][:100] + "..." if len(row["text"]) > 100 else row["text"],
            "difficulty": row["difficulty"],
            "bloom_level": row["bloom_level"],
            "created_at": row["created_at"]
        } for row in rows])
    
    else:  # POST
        data = request.get_json() or {}
        
        # Validate required fields
        topic_id = data.get("topic_id")
        q_type = data.get("type", "single")
        text = (data.get("text") or "").strip()
        options = data.get("options", [])
        correct = data.get("correct", [])
        difficulty = data.get("difficulty", "medium")
        explanation = (data.get("explanation") or "").strip()
        bloom_level = data.get("bloom_level", "understand")
        
        if not topic_id or not text or not options or not correct:
            return jsonify({
                "error": "Missing required fields: topic_id, text, options, correct",
                "code": "MISSING_FIELDS"
            }), 400
        
        # Generate unique qid
        qid = f"{topic_id}_{secrets.token_hex(4)}"
        
        db = get_db()
        
        # Insert question
        cursor = db.execute(
            """
            INSERT INTO questions (topic_id, qid, type, text, options_json, correct_json, 
                                 difficulty, explanation, bloom_level, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_id, qid, q_type, text,
                json.dumps(options, ensure_ascii=False),
                json.dumps(correct, ensure_ascii=False),
                difficulty, explanation, bloom_level,
                session["user_id"]
            )
        )
        
        question_id = cursor.lastrowid
        
        # Insert options
        for opt in options:
            opt_id = opt.get("id", "")
            opt_text = opt.get("text", "")
            is_correct = 1 if opt_id in correct else 0
            
            db.execute(
                """
                INSERT INTO options (question_id, option_id, option_text, is_correct)
                VALUES (?, ?, ?, ?)
                """,
                (question_id, opt_id, opt_text, is_correct)
            )
        
        db.commit()
        
        return jsonify({
            "success": True,
            "question_id": question_id,
            "qid": qid,
            "message": "Question created successfully"
        }), 201


@app.route("/api/admin/stats")
@role_required("teacher", "admin")
def admin_stats():
    """Get admin statistics."""
    db = get_db()
    
    # Total counts
    counts = db.execute(
        """
        SELECT 
            (SELECT COUNT(*) FROM users WHERE role = 'student') as student_count,
            (SELECT COUNT(*) FROM users WHERE role = 'teacher') as teacher_count,
            (SELECT COUNT(*) FROM topics) as topic_count,
            (SELECT COUNT(*) FROM questions) as question_count,
            (SELECT COUNT(*) FROM results) as result_count
        """
    ).fetchone()
    
    # Recent results
    recent = db.execute(
        """
        SELECT r.id, r.topic_title, r.percent, r.finished_at, u.username
        FROM results r
        JOIN users u ON u.id = r.user_id
        ORDER BY r.finished_at DESC
        LIMIT 10
        """
    ).fetchall()
    
    return jsonify({
        "counts": {
            "students": counts["student_count"],
            "teachers": counts["teacher_count"],
            "topics": counts["topic_count"],
            "questions": counts["question_count"],
            "results": counts["result_count"]
        },
        "recent_results": [{
            "id": r["id"],
            "topic_title": r["topic_title"],
            "percent": r["percent"],
            "finished_at": r["finished_at"],
            "username": r["username"]
        } for r in recent]
    })


# Additional utility routes
@app.route("/api/competency")
@login_required
def get_competency():
    """Get user's competency map across topics."""
    db = get_db()
    
    # Get results by topic
    rows = db.execute(
        """
        SELECT 
            r.topic_id,
            r.topic_title,
            AVG(r.percent) as avg_percent,
            COUNT(r.id) as attempt_count,
            MAX(r.percent) as best_percent
        FROM results r
        WHERE r.user_id = ?
        GROUP BY r.topic_id
        """,
        (session["user_id"],)
    ).fetchall()
    
    topics = []
    for row in rows:
        avg = round(row["avg_percent"], 1)
        best = round(row["best_percent"], 1)
        
        # Determine level
        if best >= 80:
            level = "excellent"
        elif best >= 60:
            level = "good"
        elif best >= 40:
            level = "attention"
        else:
            level = "weak"
        
        # Determine trend
        if avg >= best * 0.95:
            trend = "stable"
        elif avg < best * 0.8:
            trend = "improving" if best > avg else "declining"
        else:
            trend = "stable"
        
        topics.append({
            "topic_id": row["topic_id"],
            "topic_title": row["topic_title"],
            "avg_percent": avg,
            "best_percent": best,
            "attempt_count": row["attempt_count"],
            "level": level,
            "trend": trend
        })
    
    # Calculate overall score
    if topics:
        overall = round(sum(t["best_percent"] for t in topics) / len(topics), 1)
    else:
        overall = 0
    
    # Generate recommendations
    recommendations = []
    weak_topics = [t for t in topics if t["level"] in ["weak", "attention"]]
    
    if weak_topics:
        weak_names = ", ".join([t["topic_title"] for t in weak_topics[:3]])
        recommendations.append(f"Уделите внимание темам: {weak_names}")
    
    if any(t["trend"] == "declining" for t in topics):
        recommendations.append("Ваши результаты снижаются. Рекомендуем повторение.")
    
    if not recommendations:
        recommendations.append("Отличная работа! Продолжайте в том же духе.")
    
    return jsonify({
        "overall_score": overall,
        "topics": topics,
        "recommendations": recommendations,
        "strengths": [t["topic_title"] for t in topics if t["level"] == "excellent"],
        "weaknesses": [t["topic_title"] for t in weak_topics]
    })


# Teacher invite code route
@app.route("/api/teacher-invite")
@role_required("admin")
def get_teacher_invite():
    """Get teacher invite code (admin only)."""
    return jsonify({
        "invite_code": TEACHER_INVITE_CODE,
        "env_variable": "TEACHER_INVITE_CODE"
    })


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
