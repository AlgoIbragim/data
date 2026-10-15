"""
ИнфоТренер: тесты по информатике с ролями, SQLite и серверной проверкой ответов.
"""

from __future__ import annotations

import csv
import io
import json
import random
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import Flask, Response, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
QUESTIONS_PATH = DATA_DIR / "questions.json"
DB_PATH = DATA_DIR / "infotrainer.db"
SECRET_PATH = DATA_DIR / "secret.key"

LOCALES_DIR = BASE_DIR / "locales"

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
OPTION_IDS = ("a", "b", "c", "d", "e", "f")
ROLES = ("student", "teacher")
DIFFICULTIES = ("easy", "medium", "hard")
ICONS = ("cpu", "code", "database", "hash", "globe", "shield", "zap", "book")

# Демо-аккаунты создаются только при пустой таблице users.
DEMO_USERS = (
    ("teacher", "Teacher123", "teacher"),
    ("student", "Student123", "student"),
)

TRANSLIT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str) -> str:
    """Транслитерация русских букв в латиницу, нижний регистр, дефисы для id тем."""
    lowered = text.strip().lower()
    chars = []
    for ch in lowered:
        if ch in TRANSLIT_MAP:
            chars.append(TRANSLIT_MAP[ch])
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


app = Flask(__name__)
app.config["SECRET_KEY"] = read_or_create_secret()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False  # локальный HTTP; для HTTPS поставьте True


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


def seed_from_json(db: sqlite3.Connection) -> None:
    """Первоначальное наполнение базы данных из data/questions.json с заполнением таблицы options."""
    if not QUESTIONS_PATH.exists():
        return
    try:
        with QUESTIONS_PATH.open(encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return

    topics = data.get("topics", [])
    for topic in topics:
        topic_id = str(topic.get("id") or "").strip()
        if not topic_id:
            continue
        title = str(topic.get("title") or "").strip()
        subtitle = str(topic.get("subtitle") or "").strip()
        icon = str(topic.get("icon") or "hash").strip()
        time_limit_sec = int(topic.get("time_limit_sec") or 480)

        db.execute(
            """
            INSERT OR REPLACE INTO topics (id, title, subtitle, icon, time_limit_sec, created_by)
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (topic_id, title, subtitle, icon, time_limit_sec),
        )

        for q in topic.get("questions", []):
            qid = str(q.get("id") or "").strip()
            if not qid:
                continue
            q_type = str(q.get("type") or "single").strip()
            text = str(q.get("text") or "").strip()
            options = q.get("options") or []
            correct = q.get("correct") or []
            difficulty = str(q.get("difficulty") or "medium").strip()
            if difficulty not in DIFFICULTIES:
                difficulty = "medium"
            explanation = str(q.get("explanation") or "").strip()

            cursor = db.execute(
                """
                INSERT OR IGNORE INTO questions (
                    topic_id, qid, type, text, options_json, correct_json, difficulty, explanation, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    topic_id,
                    qid,
                    q_type,
                    text,
                    json.dumps(options, ensure_ascii=False),
                    json.dumps(correct, ensure_ascii=False),
                    difficulty,
                    explanation,
                ),
            )
            q_row = db.execute("SELECT id FROM questions WHERE qid = ?", (qid,)).fetchone()
            if q_row:
                q_id = q_row["id"]
                db.execute("DELETE FROM options WHERE question_id = ?", (q_id,))
                for opt in options:
                    if not isinstance(opt, dict):
                        continue
                    opt_id = str(opt.get("id") or "").strip().lower()
                    opt_text = str(opt.get("text") or "").strip()
                    is_correct_val = 1 if opt_id in [str(c).lower() for c in correct] else 0
                    db.execute(
                        """
                        INSERT INTO options (question_id, option_id, option_text, is_correct)
                        VALUES (?, ?, ?, ?)
                        """,
                        (q_id, opt_id, opt_text, is_correct_val),
                    )


def init_db() -> None:
    """Создаёт таблицы, применяет миграции и сидирует темы и пользователей."""
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('student', 'teacher'))
        );

        CREATE TABLE IF NOT EXISTS topics (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            subtitle TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT 'hash',
            time_limit_sec INTEGER NOT NULL DEFAULT 480,
            created_by INTEGER,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id TEXT NOT NULL,
            qid TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL CHECK (type IN ('single', 'multiple')),
            text TEXT NOT NULL,
            options_json TEXT DEFAULT '[]',
            correct_json TEXT DEFAULT '[]',
            difficulty TEXT NOT NULL DEFAULT 'medium',
            explanation TEXT NOT NULL DEFAULT '',
            created_by INTEGER,
            FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            option_id TEXT NOT NULL,
            option_text TEXT NOT NULL,
            is_correct INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_options_qid ON options(question_id);

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
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )

    # Миграции для существующих баз данных
    try:
        db.execute("ALTER TABLE questions ADD COLUMN difficulty TEXT NOT NULL DEFAULT 'medium'")
    except sqlite3.OperationalError:
        pass

    try:
        db.execute("ALTER TABLE questions ADD COLUMN explanation TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # Демо-аккаунты
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

    # Сидирование из questions.json, если таблица topics пуста
    topic_count_row = db.execute("SELECT COUNT(*) AS cnt FROM topics").fetchone()
    if topic_count_row and topic_count_row["cnt"] == 0:
        seed_from_json(db)

    # Миграция существующих вопросов в таблицу options, если options пуста
    opt_count_row = db.execute("SELECT COUNT(*) AS cnt FROM options").fetchone()
    if opt_count_row and opt_count_row["cnt"] == 0:
        q_rows = db.execute("SELECT id, options_json, correct_json FROM questions").fetchall()
        for q in q_rows:
            q_id = q["id"]
            try:
                opts = json.loads(q["options_json"]) if q["options_json"] else []
                corr = json.loads(q["correct_json"]) if q["correct_json"] else []
            except Exception:
                continue
            for idx, opt in enumerate(opts):
                o_id = str(opt.get("id") or chr(ord("a") + idx)).lower()
                o_text = str(opt.get("text") or "").strip()
                is_corr = 1 if o_id in [str(c).lower() for c in corr] else 0
                db.execute(
                    """
                    INSERT INTO options (question_id, option_id, option_text, is_correct)
                    VALUES (?, ?, ?, ?)
                    """,
                    (q_id, o_id, o_text, is_corr),
                )

    db.commit()


def get_all_topics() -> list[dict[str, Any]]:
    """Возвращает список всех тем с количеством вопросов question_count только из БД."""
    db = get_db()
    rows = db.execute(
        """
        SELECT t.id, t.title, t.subtitle, t.icon, t.time_limit_sec, t.created_by,
               COUNT(q.id) AS question_count
        FROM topics t
        LEFT JOIN questions q ON q.topic_id = t.id
        GROUP BY t.id
        ORDER BY t.rowid ASC
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "subtitle": row["subtitle"],
            "icon": row["icon"],
            "time_limit_sec": row["time_limit_sec"],
            "created_by": row["created_by"],
            "question_count": row["question_count"],
        }
        for row in rows
    ]


def get_topic(topic_id: str) -> dict[str, Any] | None:
    """Возвращает тему вместе с полным списком вопросов и вариантов из таблицы options."""
    db = get_db()
    t = db.execute(
        "SELECT id, title, subtitle, icon, time_limit_sec, created_by FROM topics WHERE id = ?",
        (topic_id,),
    ).fetchone()
    if t is None:
        return None

    q_rows = db.execute(
        """
        SELECT id, qid, type, text, options_json, correct_json, difficulty, explanation, created_by
        FROM questions
        WHERE topic_id = ?
        ORDER BY id ASC
        """,
        (topic_id,),
    ).fetchall()

    questions = []
    for row in q_rows:
        q_db_id = row["id"]
        opt_rows = db.execute(
            """
            SELECT option_id, option_text, is_correct
            FROM options
            WHERE question_id = ?
            ORDER BY id ASC
            """,
            (q_db_id,),
        ).fetchall()

        if opt_rows:
            options = [
                {
                    "id": o["option_id"],
                    "text": o["option_text"],
                    "is_correct": bool(o["is_correct"]),
                }
                for o in opt_rows
            ]
            correct = [o["option_id"] for o in opt_rows if o["is_correct"]]
        else:
            # Fallback для совместимости
            try:
                raw_opts = json.loads(row["options_json"]) if row["options_json"] else []
                correct = json.loads(row["correct_json"]) if row["correct_json"] else []
            except Exception:
                raw_opts = []
                correct = []
            options = [
                {
                    "id": str(o.get("id") or ""),
                    "text": str(o.get("text") or ""),
                    "is_correct": str(o.get("id") or "").lower() in [str(c).lower() for c in correct],
                }
                for o in raw_opts
            ]

        questions.append(
            {
                "id": row["qid"],
                "qid": row["qid"],
                "db_id": q_db_id,
                "type": row["type"],
                "text": row["text"],
                "options": options,
                "correct": correct,
                "difficulty": row["difficulty"] if "difficulty" in row.keys() else "medium",
                "explanation": row["explanation"] if "explanation" in row.keys() else "",
                "created_by": row["created_by"],
            }
        )

    return {
        "id": t["id"],
        "title": t["title"],
        "subtitle": t["subtitle"],
        "icon": t["icon"],
        "time_limit_sec": t["time_limit_sec"],
        "created_by": t["created_by"],
        "questions": questions,
        "question_count": len(questions),
    }


def find_topic(topic_id: str) -> dict[str, Any] | None:
    """Алиас для get_topic для обратной совместимости."""
    return get_topic(topic_id)


def public_topic(topic: dict[str, Any]) -> dict[str, Any]:
    """Безопасное подмножество свойств темы для клиента."""
    return {
        "id": topic["id"],
        "title": topic["title"],
        "subtitle": topic.get("subtitle", ""),
        "icon": topic.get("icon", "hash"),
        "time_limit_sec": topic["time_limit_sec"],
        "question_count": topic.get("question_count", len(topic.get("questions", []))),
    }


def public_questions(topic: dict[str, Any]) -> list[dict[str, Any]]:
    """Список вопросов темы без ключей правильных ответов, перемешанный случайным образом."""
    questions = []
    for question in topic.get("questions", []):
        # Очищаем варианты от признака is_correct перед отправкой на клиент
        clean_options = [
            {"id": opt["id"], "text": opt["text"]}
            for opt in question.get("options", [])
        ]
        shuffled_options = list(clean_options)
        random.shuffle(shuffled_options)
        questions.append(
            {
                "id": question["id"],
                "type": question["type"],
                "text": question["text"],
                "options": shuffled_options,
                "difficulty": question.get("difficulty", "medium"),
            }
        )
    random.shuffle(questions)
    return questions


def is_correct(question: dict[str, Any], selected: list[str]) -> bool:
    """Безопасная сверка правильности: точное совпадение выбранных вариантов с правильными в БД."""
    correct_ids = set(question.get("correct", []))
    return set(selected) == correct_ids


def grade_attempt(topic: dict[str, Any], answers: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Проверка ответов на сервере с защитой от подделки и подробным разбором."""
    review = []
    for question in topic.get("questions", []):
        selected = answers.get(question["id"], [])
        if not isinstance(selected, list):
            selected = []
        valid_ids = {option["id"] for option in question.get("options", [])}
        cleaned = [item for item in selected if item in valid_ids]
        review.append(
            {
                "id": question["id"],
                "text": question["text"],
                "type": question["type"],
                "options": [
                    {"id": o["id"], "text": o["text"]}
                    for o in question.get("options", [])
                ],
                "selected": cleaned,
                "correct": question["correct"],
                "is_correct": is_correct(question, cleaned),
                "difficulty": question.get("difficulty", "medium"),
                "explanation": question.get("explanation", ""),
            }
        )
    return review


def password_errors(password: str) -> list[str]:
    """Правила надёжности: длина, цифра, строчная и заглавная буква."""
    errors = []
    if len(password) < 8:
        errors.append("Пароль должен содержать не менее 8 символов.")
    if not re.search(r"[0-9]", password):
        errors.append("Пароль должен содержать хотя бы одну цифру.")
    if not re.search(r"[a-zа-яё]", password):
        errors.append("Пароль должен содержать строчную букву.")
    if not re.search(r"[A-ZА-ЯЁ]", password):
        errors.append("Пароль должен содержать заглавную букву.")
    return errors


def current_user() -> sqlite3.Row | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_db().execute(
        "SELECT id, username, role FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()


def user_payload(user: sqlite3.Row) -> dict[str, Any]:
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


def login_required(role: str | None = None) -> Callable:
    """Декоратор API: сессия обязательна, роль — по необходимости."""

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any):
            user = current_user()
            if user is None:
                return jsonify({"error": "Нужна авторизация"}), 401
            if role and user["role"] != role:
                return jsonify({"error": "Недостаточно прав для этого действия"}), 403
            g.user = user
            return view(*args, **kwargs)

        return wrapped

    return decorator


def build_teacher_stats() -> dict[str, Any]:
    """Сводка по всем попыткам студентов на основе данных из SQLite."""
    db = get_db()
    rows = db.execute(
        """
        SELECT r.topic_id, r.topic_title, r.percent, r.duration_sec, r.finished_at,
               r.review_json, u.username
        FROM results r
        JOIN users u ON u.id = r.user_id
        ORDER BY r.finished_at DESC
        """
    ).fetchall()

    topics_list = get_all_topics()
    per_topic: dict[str, dict[str, Any]] = {}
    for topic in topics_list:
        topic_id = topic["id"]
        per_topic[topic_id] = {
            "id": topic_id,
            "title": topic["title"],
            "attempts": 0,
            "percent_sum": 0.0,
            "time_sum": 0,
            "missed_questions": {},
        }

    for row in rows:
        topic_id = row["topic_id"]
        if topic_id not in per_topic:
            per_topic[topic_id] = {
                "id": topic_id,
                "title": row["topic_title"],
                "attempts": 0,
                "percent_sum": 0.0,
                "time_sum": 0,
                "missed_questions": {},
            }
        bucket = per_topic[topic_id]
        bucket["attempts"] += 1
        bucket["percent_sum"] += float(row["percent"])
        bucket["time_sum"] += int(row["duration_sec"])
        try:
            review_items = json.loads(row["review_json"])
        except Exception:
            review_items = []
        for item in review_items:
            if item.get("is_correct"):
                continue
            qid = item.get("id", "")
            missed = bucket["missed_questions"]
            if qid not in missed:
                missed[qid] = {"id": qid, "text": item.get("text", qid), "errors": 0}
            missed[qid]["errors"] += 1

    topics_out = []
    weak_topics = []
    for bucket in per_topic.values():
        count = bucket["attempts"]
        avg = round(bucket["percent_sum"] / count, 1) if count else 0.0
        avg_time = round(bucket["time_sum"] / count) if count else 0
        hardest = sorted(
            bucket["missed_questions"].values(),
            key=lambda item: item["errors"],
            reverse=True,
        )[:3]
        row = {
            "id": bucket["id"],
            "title": bucket["title"],
            "attempts": count,
            "avg_percent": avg,
            "avg_time_sec": avg_time,
            "hardest_questions": hardest,
            "is_weak": count > 0 and avg < 60,
        }
        topics_out.append(row)
        if row["is_weak"]:
            weak_topics.append(row["title"])

    scored = db.execute(
        """
        SELECT r.score, r.max_score, r.percent, r.duration_sec, r.topic_title,
               r.finished_at, u.username AS student
        FROM results r
        JOIN users u ON u.id = r.user_id
        ORDER BY r.id DESC
        LIMIT 12
        """
    ).fetchall()
    recent = [dict(item) for item in scored]

    users = db.execute(
        """
        SELECT u.id, u.username, u.role,
               COUNT(r.id) AS attempts,
               ROUND(AVG(r.percent), 1) AS avg_percent
        FROM users u
        LEFT JOIN results r ON r.user_id = u.id
        GROUP BY u.id
        ORDER BY u.role DESC, u.username
        """
    ).fetchall()

    return {
        "total_attempts": len(rows),
        "topics": topics_out,
        "weak_topics": weak_topics,
        "recent": recent,
        "users": [dict(user) for user in users],
    }


with app.app_context():
    init_db()


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/locales/<lang>")
def api_locales(lang: str):
    """Отдаёт JSON-файл локализации (ru или kk)."""
    clean_lang = "kk" if lang == "kk" else "ru"
    loc_file = LOCALES_DIR / f"{clean_lang}.json"
    if not loc_file.exists():
        return jsonify({"error": "Locale not found"}), 404
    with loc_file.open(encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data)


# ============================================================================
# АВТОРИЗАЦИЯ И СЕССИИ
# ============================================================================


@app.get("/api/me")
def api_me():
    user = current_user()
    if user is None:
        return jsonify({"user": None})
    return jsonify({"user": user_payload(user)})


@app.post("/api/register")
def api_register():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    role = str(payload.get("role") or "student").strip().lower()

    if not USERNAME_RE.fullmatch(username):
        return jsonify({"error": "Логин: 3–32 символа, только латиница, цифры и _."}), 400
    if role not in ROLES:
        return jsonify({"error": "Роль может быть только student или teacher."}), 400

    issues = password_errors(password)
    if issues:
        return jsonify({"error": " ".join(issues), "details": issues}), 400

    db = get_db()
    exists = db.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
    ).fetchone()
    if exists:
        return jsonify({"error": "Такой логин уже занят. Выберите другой."}), 409

    cursor = db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, generate_password_hash(password), role),
    )
    db.commit()

    session.clear()
    session.permanent = True
    session["user_id"] = cursor.lastrowid
    user = current_user()
    return jsonify({"user": user_payload(user)}), 201


@app.post("/api/login")
def api_login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")

    row = get_db().execute(
        "SELECT id, username, role, password_hash FROM users WHERE username = ? COLLATE NOCASE",
        (username,),
    ).fetchone()
    if row is None:
        return jsonify({"error": "Пользователь с таким логином не найден."}), 401
    if not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "Неверный пароль."}), 401

    session.clear()
    session.permanent = True
    session["user_id"] = row["id"]
    return jsonify({"user": {"id": row["id"], "username": row["username"], "role": row["role"]}})


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


# ============================================================================
# ТЕМЫ (ДЛЯ ВСЕХ АВТОРИЗОВАННЫХ И СТУДЕНТОВ)
# ============================================================================


@app.get("/api/topics")
@login_required()
def api_topics():
    topics = [public_topic(t) for t in get_all_topics()]
    return jsonify({"topics": topics})


@app.get("/api/topics/<topic_id>")
@login_required("student")
def api_topic(topic_id: str):
    topic = get_topic(topic_id)
    if topic is None:
        return jsonify({"error": "Тема не найдена"}), 404
    return jsonify({"topic": public_topic(topic), "questions": public_questions(topic)})


# ============================================================================
# CRUD ТЕМ (ПРЕПОДАВАТЕЛЬ)
# ============================================================================


@app.post("/api/topics/manage")
@login_required("teacher")
def api_create_topic():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()
    subtitle = str(payload.get("subtitle") or "").strip()
    icon = str(payload.get("icon") or "hash").strip().lower()
    if icon not in ICONS:
        icon = "hash"

    if len(title) < 2:
        return jsonify({"error": "Название темы должно содержать не менее 2 символов."}), 400

    time_limit_raw = payload.get("time_limit_sec", 480)
    try:
        time_limit_sec = int(time_limit_raw)
        if time_limit_sec < 30 or time_limit_sec > 7200:
            time_limit_sec = 480
    except (TypeError, ValueError):
        time_limit_sec = 480

    base_slug = slugify(title)
    slug = base_slug
    db = get_db()
    counter = 1
    while db.execute("SELECT 1 FROM topics WHERE id = ?", (slug,)).fetchone():
        slug = f"{base_slug}-{counter}"
        counter += 1

    db.execute(
        """
        INSERT INTO topics (id, title, subtitle, icon, time_limit_sec, created_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (slug, title, subtitle, icon, time_limit_sec, g.user["id"]),
    )
    db.commit()

    new_topic = get_topic(slug)
    return jsonify({"ok": True, "topic": public_topic(new_topic)}), 201


@app.put("/api/topics/manage/<topic_id>")
@login_required("teacher")
def api_update_topic(topic_id: str):
    db = get_db()
    existing = db.execute(
        "SELECT id, title, subtitle, icon, time_limit_sec FROM topics WHERE id = ?",
        (topic_id,),
    ).fetchone()
    if existing is None:
        return jsonify({"error": "Тема не найдена."}), 404

    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or existing["title"]).strip()
    subtitle = str(payload.get("subtitle", existing["subtitle"])).strip()
    icon = str(payload.get("icon") or existing["icon"]).strip().lower()
    if icon not in ICONS:
        icon = existing["icon"]

    if len(title) < 2:
        return jsonify({"error": "Название темы должно содержать не менее 2 символов."}), 400

    time_limit_raw = payload.get("time_limit_sec", existing["time_limit_sec"])
    try:
        time_limit_sec = int(time_limit_raw)
        if time_limit_sec < 30 or time_limit_sec > 7200:
            time_limit_sec = existing["time_limit_sec"]
    except (TypeError, ValueError):
        time_limit_sec = existing["time_limit_sec"]

    db.execute(
        """
        UPDATE topics
        SET title = ?, subtitle = ?, icon = ?, time_limit_sec = ?
        WHERE id = ?
        """,
        (title, subtitle, icon, time_limit_sec, topic_id),
    )
    db.commit()

    updated = get_topic(topic_id)
    return jsonify({"ok": True, "topic": public_topic(updated)})


@app.delete("/api/topics/manage/<topic_id>")
@login_required("teacher")
def api_delete_topic(topic_id: str):
    db = get_db()
    existing = db.execute("SELECT id FROM topics WHERE id = ?", (topic_id,)).fetchone()
    if existing is None:
        return jsonify({"error": "Тема не найдена."}), 404

    db.execute("DELETE FROM questions WHERE topic_id = ?", (topic_id,))
    db.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
    db.commit()

    return jsonify({"ok": True})


# ============================================================================
# CRUD ВОПРОСОВ (ПРЕПОДАВАТЕЛЬ)
# ============================================================================


@app.get("/api/questions/list")
@login_required("teacher")
def api_questions_list():
    topic_id = request.args.get("topic_id", "").strip()
    search = request.args.get("search", "").strip()
    difficulty = request.args.get("difficulty", "").strip()

    conditions = []
    params: list[Any] = []

    if topic_id:
        conditions.append("q.topic_id = ?")
        params.append(topic_id)

    if difficulty:
        conditions.append("q.difficulty = ?")
        params.append(difficulty)

    if search:
        conditions.append("q.text LIKE ?")
        params.append(f"%{search}%")

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT q.id, q.topic_id, q.qid, q.type, q.text, q.options_json,
               q.correct_json, q.difficulty, q.explanation, q.created_by,
               t.title AS topic_title
        FROM questions q
        LEFT JOIN topics t ON t.id = q.topic_id
        {where_sql}
        ORDER BY q.id DESC
    """
    db = get_db()
    rows = db.execute(query, params).fetchall()
    questions = []
    for r in rows:
        q_db_id = r["id"]
        opt_rows = db.execute(
            """
            SELECT option_id, option_text, is_correct
            FROM options
            WHERE question_id = ?
            ORDER BY id ASC
            """,
            (q_db_id,),
        ).fetchall()

        if opt_rows:
            options = [
                {
                    "id": o["option_id"],
                    "text": o["option_text"],
                    "is_correct": bool(o["is_correct"]),
                }
                for o in opt_rows
            ]
            correct = [o["option_id"] for o in opt_rows if o["is_correct"]]
        else:
            try:
                raw_opts = json.loads(r["options_json"]) if r["options_json"] else []
                correct = json.loads(r["correct_json"]) if r["correct_json"] else []
            except Exception:
                raw_opts = []
                correct = []
            options = [
                {
                    "id": str(o.get("id") or ""),
                    "text": str(o.get("text") or ""),
                    "is_correct": str(o.get("id") or "").lower() in [str(c).lower() for c in correct],
                }
                for o in raw_opts
            ]

        questions.append(
            {
                "id": r["qid"],
                "qid": r["qid"],
                "topic_id": r["topic_id"],
                "topic_title": r["topic_title"] or r["topic_id"],
                "type": r["type"],
                "text": r["text"],
                "options": options,
                "correct": correct,
                "difficulty": r["difficulty"],
                "explanation": r["explanation"],
                "created_by": r["created_by"],
            }
        )
    return jsonify({"questions": questions})


@app.post("/api/questions")
@login_required("teacher")
def api_add_question():
    """Добавление вопроса преподавателем в выбранную тему с динамическими вариантами."""
    payload = request.get_json(silent=True) or {}
    topic_id = str(payload.get("topic_id") or "").strip()
    q_type = str(payload.get("type") or "").strip()
    text = str(payload.get("text") or "").strip()
    raw_options = payload.get("options")
    raw_correct = payload.get("correct") or []
    difficulty = str(payload.get("difficulty") or "medium").strip().lower()
    explanation = str(payload.get("explanation") or "").strip()

    if difficulty not in DIFFICULTIES:
        difficulty = "medium"

    db = get_db()
    if get_topic(topic_id) is None:
        return jsonify({"error": "Тема не найдена."}), 404
    if q_type not in ("single", "multiple"):
        return jsonify({"error": "Тип вопроса: single или multiple."}), 400
    if len(text) < 8:
        return jsonify({"error": "Текст вопроса слишком короткий (минимум 8 символов)."}), 400
    if not isinstance(raw_options, list):
        return jsonify({"error": "Некорректный формат вариантов ответов."}), 400

    # Обрабатываем динамические варианты (от 2 до 6)
    cleaned_options = []
    correct_list = [str(c).strip().lower() for c in raw_correct]

    for idx, opt in enumerate(raw_options[:6]):
        if not isinstance(opt, dict):
            continue
        opt_text = str(opt.get("text") or "").strip()
        if not opt_text:
            continue
        opt_id = str(opt.get("id") or chr(ord("a") + idx)).strip().lower()
        is_corr = bool(opt.get("is_correct")) or (opt_id in correct_list)
        cleaned_options.append({
            "id": opt_id,
            "text": opt_text[:300],
            "is_correct": is_corr,
        })

    if len(cleaned_options) < 2:
        return jsonify({"error": "Нужно указать минимум 2 варианта ответа."}), 400

    correct_count = sum(1 for o in cleaned_options if o["is_correct"])
    if q_type == "single" and correct_count != 1:
        return jsonify({"error": "Для одиночного выбора отметьте ровно один правильный вариант."}), 400
    if q_type == "multiple" and correct_count < 1:
        return jsonify({"error": "Для множественного выбора отметьте хотя бы один правильный вариант."}), 400

    qid = f"custom-{uuid.uuid4().hex[:10]}"
    cursor = db.execute(
        """
        INSERT INTO questions (
            topic_id, qid, type, text, options_json, correct_json, difficulty, explanation, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            topic_id,
            qid,
            q_type,
            text[:600],
            json.dumps([{"id": o["id"], "text": o["text"]} for o in cleaned_options], ensure_ascii=False),
            json.dumps([o["id"] for o in cleaned_options if o["is_correct"]], ensure_ascii=False),
            difficulty,
            explanation[:1000],
            g.user["id"],
        ),
    )
    new_q_id = cursor.lastrowid

    # Вставляем варианты в нормализованную таблицу options
    for opt in cleaned_options:
        db.execute(
            """
            INSERT INTO options (question_id, option_id, option_text, is_correct)
            VALUES (?, ?, ?, ?)
            """,
            (new_q_id, opt["id"], opt["text"], 1 if opt["is_correct"] else 0),
        )

    db.commit()
    return jsonify({"ok": True, "id": qid, "qid": qid}), 201


@app.put("/api/questions/<qid>")
@login_required("teacher")
def api_update_question(qid: str):
    """Обновление вопроса и его вариантов в таблице options."""
    db = get_db()
    existing = db.execute(
        """
        SELECT id, topic_id, type, text, options_json, correct_json, difficulty, explanation
        FROM questions
        WHERE qid = ?
        """,
        (qid,),
    ).fetchone()
    if existing is None:
        return jsonify({"error": "Вопрос не найден."}), 404

    q_db_id = existing["id"]
    payload = request.get_json(silent=True) or {}
    topic_id = str(payload.get("topic_id") or existing["topic_id"]).strip()
    q_type = str(payload.get("type") or existing["type"]).strip()
    text = str(payload.get("text") or existing["text"]).strip()
    raw_options = payload.get("options")
    raw_correct = payload.get("correct") or []

    difficulty = str(payload.get("difficulty") or existing["difficulty"]).strip().lower()
    if difficulty not in DIFFICULTIES:
        difficulty = existing["difficulty"]
    explanation = str(payload.get("explanation", existing["explanation"])).strip()

    if get_topic(topic_id) is None:
        return jsonify({"error": "Тема не найдена."}), 404
    if q_type not in ("single", "multiple"):
        return jsonify({"error": "Тип вопроса: single или multiple."}), 400
    if len(text) < 8:
        return jsonify({"error": "Текст вопроса слишком короткий (минимум 8 символов)."}), 400

    correct_list = [str(c).strip().lower() for c in raw_correct]
    cleaned_options = []

    if isinstance(raw_options, list):
        for idx, opt in enumerate(raw_options[:6]):
            if not isinstance(opt, dict):
                continue
            opt_text = str(opt.get("text") or "").strip()
            if not opt_text:
                continue
            opt_id = str(opt.get("id") or chr(ord("a") + idx)).strip().lower()
            is_corr = bool(opt.get("is_correct")) or (opt_id in correct_list)
            cleaned_options.append({
                "id": opt_id,
                "text": opt_text[:300],
                "is_correct": is_corr,
            })

    if len(cleaned_options) < 2:
        return jsonify({"error": "Нужно указать минимум 2 варианта ответа."}), 400

    correct_count = sum(1 for o in cleaned_options if o["is_correct"])
    if q_type == "single" and correct_count != 1:
        return jsonify({"error": "Для одиночного выбора отметьте ровно один правильный вариант."}), 400
    if q_type == "multiple" and correct_count < 1:
        return jsonify({"error": "Для множественного выбора отметьте хотя бы один правильный вариант."}), 400

    # Обновляем вопрос
    db.execute(
        """
        UPDATE questions
        SET topic_id = ?, type = ?, text = ?, options_json = ?,
            correct_json = ?, difficulty = ?, explanation = ?
        WHERE id = ?
        """,
        (
            topic_id,
            q_type,
            text[:600],
            json.dumps([{"id": o["id"], "text": o["text"]} for o in cleaned_options], ensure_ascii=False),
            json.dumps([o["id"] for o in cleaned_options if o["is_correct"]], ensure_ascii=False),
            difficulty,
            explanation[:1000],
            q_db_id,
        ),
    )

    # Перезаписываем варианты в options
    db.execute("DELETE FROM options WHERE question_id = ?", (q_db_id,))
    for opt in cleaned_options:
        db.execute(
            """
            INSERT INTO options (question_id, option_id, option_text, is_correct)
            VALUES (?, ?, ?, ?)
            """,
            (q_db_id, opt["id"], opt["text"], 1 if opt["is_correct"] else 0),
        )

    db.commit()
    return jsonify({"ok": True, "qid": qid})


@app.delete("/api/questions/<qid>")
@login_required("teacher")
def api_delete_question(qid: str):
    """Удаление вопроса и его вариантов из options."""
    db = get_db()
    existing = db.execute("SELECT id FROM questions WHERE qid = ?", (qid,)).fetchone()
    if existing is None:
        return jsonify({"error": "Вопрос не найден."}), 404

    q_db_id = existing["id"]
    db.execute("DELETE FROM options WHERE question_id = ?", (q_db_id,))
    db.execute("DELETE FROM questions WHERE id = ?", (q_db_id,))
    db.commit()
    return jsonify({"ok": True})


# ============================================================================
# ИМПОРТ И ЭКСПОРТ (ПРЕПОДАВАТЕЛЬ)
# ============================================================================


@app.get("/api/export/json")
@login_required("teacher")
def api_export_json():
    """Экспорт всех тем и вопросов в формате JSON."""
    topics_data = []
    for t in get_all_topics():
        full_t = get_topic(t["id"])
        if full_t:
            topics_data.append(
                {
                    "id": full_t["id"],
                    "title": full_t["title"],
                    "subtitle": full_t["subtitle"],
                    "icon": full_t["icon"],
                    "time_limit_sec": full_t["time_limit_sec"],
                    "questions": [
                        {
                            "id": q["id"],
                            "type": q["type"],
                            "text": q["text"],
                            "options": q["options"],
                            "correct": q["correct"],
                            "difficulty": q.get("difficulty", "medium"),
                            "explanation": q.get("explanation", ""),
                        }
                        for q in full_t.get("questions", [])
                    ],
                }
            )

    payload = {
        "format": "infotrainer-v2",
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "topics": topics_data,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(
        content,
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="infotrainer_export.json"'},
    )


@app.get("/api/export/csv")
@login_required("teacher")
def api_export_csv():
    """Экспорт всех вопросов в формате CSV с поддержкой UTF-8 BOM."""
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(
        [
            "topic_id",
            "topic_title",
            "qid",
            "type",
            "difficulty",
            "text",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
            "correct",
            "explanation",
        ]
    )

    db = get_db()
    rows = db.execute(
        """
        SELECT q.qid, q.topic_id, q.type, q.text, q.options_json,
               q.correct_json, q.difficulty, q.explanation, t.title AS topic_title
        FROM questions q
        LEFT JOIN topics t ON t.id = q.topic_id
        ORDER BY t.rowid, q.id
        """
    ).fetchall()

    for row in rows:
        options = json.loads(row["options_json"]) if row["options_json"] else []
        opts_map = {opt["id"].lower(): opt["text"] for opt in options if isinstance(opt, dict) and "id" in opt}
        correct_list = json.loads(row["correct_json"]) if row["correct_json"] else []
        correct_str = ",".join(correct_list)

        writer.writerow(
            [
                row["topic_id"],
                row["topic_title"] or "",
                row["qid"],
                row["type"],
                row["difficulty"],
                row["text"],
                opts_map.get("a", ""),
                opts_map.get("b", ""),
                opts_map.get("c", ""),
                opts_map.get("d", ""),
                correct_str,
                row["explanation"],
            ]
        )

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="infotrainer_questions.csv"'},
    )


@app.post("/api/import/json")
@login_required("teacher")
def api_import_json():
    """Импорт тем и вопросов из JSON. Дубликаты по qid пропускаются."""
    payload = request.get_json(silent=True) or {}
    topics_data = payload.get("topics")
    if not isinstance(topics_data, list):
        return jsonify({"error": "Неверный формат: ожидается список topics."}), 400

    db = get_db()
    imported_topics = 0
    imported_questions = 0
    skipped_questions = 0

    for t in topics_data:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        topic_id = str(t.get("id") or "").strip() or slugify(title)
        if not topic_id or not title:
            continue
        subtitle = str(t.get("subtitle") or "").strip()
        icon = str(t.get("icon") or "hash").strip().lower()
        if icon not in ICONS:
            icon = "hash"
        time_limit_sec = int(t.get("time_limit_sec") or 480)

        existing_topic = db.execute("SELECT id FROM topics WHERE id = ?", (topic_id,)).fetchone()
        if existing_topic is None:
            db.execute(
                """
                INSERT INTO topics (id, title, subtitle, icon, time_limit_sec, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (topic_id, title, subtitle, icon, time_limit_sec, g.user["id"]),
            )
            imported_topics += 1
        else:
            db.execute(
                """
                UPDATE topics
                SET title = ?, subtitle = ?, icon = ?, time_limit_sec = ?
                WHERE id = ?
                """,
                (title, subtitle, icon, time_limit_sec, topic_id),
            )

        for q in t.get("questions", []):
            if not isinstance(q, dict):
                continue
            qid = str(q.get("id") or q.get("qid") or "").strip()
            if not qid:
                continue

            existing_q = db.execute("SELECT id FROM questions WHERE qid = ?", (qid,)).fetchone()
            if existing_q is not None:
                skipped_questions += 1
                continue

            q_type = str(q.get("type") or "single").strip()
            if q_type not in ("single", "multiple"):
                q_type = "single"
            text = str(q.get("text") or "").strip()
            options = q.get("options") or []
            correct = q.get("correct") or []
            difficulty = str(q.get("difficulty") or "medium").strip().lower()
            if difficulty not in DIFFICULTIES:
                difficulty = "medium"
            explanation = str(q.get("explanation") or "").strip()

            cursor = db.execute(
                """
                INSERT INTO questions (
                    topic_id, qid, type, text, options_json, correct_json, difficulty, explanation, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_id,
                    qid,
                    q_type,
                    text,
                    json.dumps(options, ensure_ascii=False),
                    json.dumps(correct, ensure_ascii=False),
                    difficulty,
                    explanation,
                    g.user["id"],
                ),
            )
            new_q_id = cursor.lastrowid
            correct_set = {str(c).strip().lower() for c in correct}
            for idx, opt in enumerate(options):
                if isinstance(opt, dict):
                    opt_id = str(opt.get("id") or chr(ord("a") + idx)).strip().lower()
                    opt_text = str(opt.get("text") or "").strip()
                else:
                    opt_id = chr(ord("a") + idx)
                    opt_text = str(opt).strip()
                is_corr = 1 if opt_id in correct_set else 0
                db.execute(
                    """
                    INSERT INTO options (question_id, option_id, option_text, is_correct)
                    VALUES (?, ?, ?, ?)
                    """,
                    (new_q_id, opt_id, opt_text, is_corr),
                )
            imported_questions += 1

    db.commit()
    return jsonify(
        {
            "ok": True,
            "imported_topics": imported_topics,
            "imported_questions": imported_questions,
            "skipped_questions": skipped_questions,
        }
    )


@app.post("/api/import/csv")
@login_required("teacher")
def api_import_csv():
    """Импорт тем и вопросов из CSV-файла. Дубликаты по qid пропускаются."""
    file = request.files.get("file")
    if file is None:
        return jsonify({"error": "CSV-файл не загружен."}), 400

    try:
        raw = file.read()
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"error": "Не удалось декодировать файл. Ожидается UTF-8."}), 400

    reader = csv.DictReader(io.StringIO(text))

    db = get_db()
    imported_topics = 0
    imported_questions = 0
    skipped_questions = 0
    seen_topics: set[str] = set()

    for row in reader:
        topic_id = (row.get("topic_id") or "").strip()
        topic_title = (row.get("topic_title") or "").strip()
        if not topic_id or not topic_title:
            continue

        if topic_id not in seen_topics:
            existing_topic = db.execute(
                "SELECT id FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            if existing_topic is None:
                db.execute(
                    """
                    INSERT INTO topics (id, title, subtitle, icon, time_limit_sec, created_by)
                    VALUES (?, ?, '', 'hash', 480, ?)
                    """,
                    (topic_id, topic_title, g.user["id"]),
                )
                imported_topics += 1
            seen_topics.add(topic_id)

        qid = (row.get("qid") or "").strip()
        if not qid:
            continue

        existing_q = db.execute(
            "SELECT id FROM questions WHERE qid = ?", (qid,)
        ).fetchone()
        if existing_q is not None:
            skipped_questions += 1
            continue

        q_type = (row.get("type") or "single").strip()
        if q_type not in ("single", "multiple"):
            q_type = "single"

        difficulty = (row.get("difficulty") or "medium").strip().lower()
        if difficulty not in DIFFICULTIES:
            difficulty = "medium"

        text = (row.get("text") or "").strip()
        explanation = (row.get("explanation") or "").strip()

        options = []
        for key in ("option_a", "option_b", "option_c", "option_d"):
            val = (row.get(key) or "").strip()
            if val:
                options.append(val)

        correct_raw = (row.get("correct") or "").strip()
        correct = [c.strip().lower() for c in correct_raw.split(",") if c.strip()]

        cursor = db.execute(
            """
            INSERT INTO questions (
                topic_id, qid, type, text, options_json, correct_json, difficulty, explanation, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_id,
                qid,
                q_type,
                text,
                json.dumps(options, ensure_ascii=False),
                json.dumps(correct, ensure_ascii=False),
                difficulty,
                explanation,
                g.user["id"],
            ),
        )
        new_q_id = cursor.lastrowid
        for idx, opt_text in enumerate(options):
            opt_id = chr(ord("a") + idx)
            is_corr = 1 if opt_id in correct else 0
            db.execute(
                """
                INSERT INTO options (question_id, option_id, option_text, is_correct)
                VALUES (?, ?, ?, ?)
                """,
                (new_q_id, opt_id, opt_text, is_corr),
            )
        imported_questions += 1

    db.commit()
    return jsonify(
        {
            "ok": True,
            "imported_topics": imported_topics,
            "imported_questions": imported_questions,
            "skipped_questions": skipped_questions,
        }
    )


# ============================================================================
# ТЕСТИРОВАНИЕ И РЕЗУЛЬТАТЫ
# ============================================================================


@app.post("/api/submit")
@login_required("student")
def api_submit():
    payload = request.get_json(silent=True) or {}
    topic_id = payload.get("topic_id")
    answers = payload.get("answers") or {}
    duration_sec = payload.get("duration_sec")

    topic = get_topic(str(topic_id or ""))
    if topic is None:
        return jsonify({"error": "Тема не найдена"}), 404
    if not isinstance(answers, dict):
        return jsonify({"error": "Некорректный формат ответов"}), 400

    limit = int(topic["time_limit_sec"])
    try:
        duration = int(duration_sec)
    except (TypeError, ValueError):
        duration = limit
    duration = max(0, min(duration, limit))

    review = grade_attempt(topic, {str(key): value for key, value in answers.items()})
    max_score = len(review)
    score = sum(1 for item in review if item["is_correct"])
    percent = round(100.0 * score / max_score, 1) if max_score else 0.0
    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    compact_review = [
        {
            "id": item["id"],
            "text": item["text"],
            "is_correct": item["is_correct"],
            "explanation": item.get("explanation", ""),
            "difficulty": item.get("difficulty", "medium"),
        }
        for item in review
    ]

    db = get_db()
    db.execute(
        """
        INSERT INTO results (
            user_id, topic_id, topic_title, score, max_score, percent,
            duration_sec, finished_at, review_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.user["id"],
            topic["id"],
            topic["title"],
            score,
            max_score,
            percent,
            duration,
            finished_at,
            json.dumps(compact_review, ensure_ascii=False),
        ),
    )
    db.commit()

    return jsonify(
        {
            "student": g.user["username"],
            "topic": public_topic(topic),
            "score": score,
            "max_score": max_score,
            "percent": percent,
            "duration_sec": duration,
            "review": review,
        }
    )


@app.get("/api/my-results")
@login_required("student")
def api_my_results():
    rows = get_db().execute(
        """
        SELECT topic_title, score, max_score, percent, duration_sec, finished_at
        FROM results
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (g.user["id"],),
    ).fetchall()
    return jsonify({"results": [dict(row) for row in rows]})


@app.get("/api/stats")
@login_required("teacher")
def api_stats():
    return jsonify(build_teacher_stats())


# ============================================================================
# КАРТА КОМПЕТЕНЦИЙ
# ============================================================================


def _build_competency_data(user_id: int) -> dict[str, Any]:
    """Общая логика анализа успеваемости студента по всем темам."""
    db = get_db()
    rows = db.execute(
        """
        SELECT id, topic_id, topic_title, score, max_score, percent,
               duration_sec, finished_at, review_json
        FROM results
        WHERE user_id = ?
        ORDER BY id ASC
        """,
        (user_id,),
    ).fetchall()

    all_topics = get_all_topics()

    results_by_topic: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        t_id = r["topic_id"]
        results_by_topic.setdefault(t_id, []).append(r)

    topics_analysis = []
    strengths = []
    weaknesses = []
    declining_topics = []
    improving_topics = []

    for topic in all_topics:
        t_id = topic["id"]
        t_title = topic["title"]
        t_results = results_by_topic.get(t_id, [])
        attempts = len(t_results)

        if attempts > 0:
            avg_percent = round(
                sum(float(r["percent"]) for r in t_results) / attempts, 1
            )

            if attempts < 2:
                trend = "new"
            else:
                mid = attempts // 2
                first_half = t_results[:mid]
                second_half = t_results[mid:]
                avg_first = sum(float(r["percent"]) for r in first_half) / len(first_half)
                avg_second = sum(float(r["percent"]) for r in second_half) / len(second_half)
                diff = avg_second - avg_first
                if diff >= 5.0:
                    trend = "improving"
                    improving_topics.append(t_title)
                elif diff <= -5.0:
                    trend = "declining"
                    declining_topics.append(t_title)
                else:
                    trend = "stable"

            if avg_percent >= 80.0:
                level = "excellent"
                strengths.append(t_title)
            elif avg_percent >= 60.0:
                level = "good"
            elif avg_percent >= 40.0:
                level = "attention"
                weaknesses.append(t_title)
            else:
                level = "weak"
                weaknesses.append(t_title)

            missed_map: dict[str, dict[str, Any]] = {}
            for r in t_results:
                try:
                    items = json.loads(r["review_json"])
                except Exception:
                    items = []
                for item in items:
                    if not item.get("is_correct"):
                        qid = item.get("id", "")
                        if qid not in missed_map:
                            missed_map[qid] = {
                                "id": qid,
                                "text": item.get("text", qid),
                                "count": 0,
                            }
                        missed_map[qid]["count"] += 1

            worst_questions = sorted(
                missed_map.values(),
                key=lambda x: x["count"],
                reverse=True,
            )[:3]
        else:
            avg_percent = 0.0
            trend = "new"
            level = "weak"
            worst_questions = []

        topics_analysis.append(
            {
                "id": t_id,
                "title": t_title,
                "attempts": attempts,
                "percent": avg_percent,
                "trend": trend,
                "level": level,
                "worst_questions": worst_questions,
            }
        )

    total_attempts = len(rows)
    overall_percent = (
        round(sum(float(r["percent"]) for r in rows) / total_attempts, 1)
        if total_attempts > 0
        else 0.0
    )

    recommendations = []
    if total_attempts == 0:
        recommendations.append(
            "Ещё нет ни одной попытки. Нужно пройти хотя бы один тест!"
        )
    else:
        if weaknesses:
            recommendations.append(
                f"Рекомендуется повторить западающие темы: {', '.join(weaknesses)}."
            )
        if declining_topics:
            recommendations.append(
                f"Обратите внимание на снижение результатов: {', '.join(declining_topics)}."
            )
        if improving_topics:
            recommendations.append(
                f"Отличная положительная динамика: {', '.join(improving_topics)}!"
            )
        if strengths and not weaknesses:
            recommendations.append(
                "Превосходный уровень знаний! Закрепляйте результат новыми попытками."
            )
        untested = [t["title"] for t in topics_analysis if t["attempts"] == 0]
        if untested:
            recommendations.append(
                f"Не забудьте проверить знания по: {', '.join(untested[:3])}."
            )

    return {
        "topics": topics_analysis,
        "recommendations": recommendations,
        "overall_percent": overall_percent,
        "total_attempts": total_attempts,
        "strengths": strengths,
        "weaknesses": weaknesses,
    }


@app.get("/api/competency-map")
@login_required("student")
def api_competency_map():
    """Анализ успеваемости текущего студента по всем темам."""
    return jsonify(_build_competency_data(g.user["id"]))


@app.get("/api/student-competency/<int:student_id>")
@login_required("teacher")
def api_student_competency(student_id: int):
    """Анализ успеваемости конкретного ученика (для преподавателя)."""
    db = get_db()
    student = db.execute(
        "SELECT id, username, role FROM users WHERE id = ?",
        (student_id,),
    ).fetchone()
    if student is None:
        return jsonify({"error": "Пользователь не найден."}), 404
    if student["role"] != "student":
        return jsonify({"error": "Этот пользователь не является учеником."}), 400

    data = _build_competency_data(student_id)
    data["username"] = student["username"]
    return jsonify(data)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
