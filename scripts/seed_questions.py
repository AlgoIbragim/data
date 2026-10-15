#!/usr/bin/env python3
"""
Скрипт для импорта расширенного банка вопросов в базу данных.
Запуск: python scripts/seed_questions.py
"""

import json
import sqlite3
import sys
from pathlib import Path

# Пути
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "infotrainer.db"
QUESTIONS_PATH = DATA_DIR / "questions_expanded.json"


def get_db():
    """Подключение к базе данных."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def import_topics_and_questions():
    """Импорт тем и вопросов из JSON файла."""
    if not QUESTIONS_PATH.exists():
        print(f"❌ Файл не найден: {QUESTIONS_PATH}")
        return False
    
    with open(QUESTIONS_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    db = get_db()
    topics_count = 0
    questions_count = 0
    
    try:
        for topic_data in data.get("topics", []):
            topic_id = topic_data["id"]
            
            # Проверяем, существует ли тема
            existing = db.execute(
                "SELECT id FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            
            if existing:
                # Обновляем существующую тему
                db.execute(
                    """
                    UPDATE topics 
                    SET title = ?, subtitle = ?, icon = ?, time_limit_sec = ?
                    WHERE id = ?
                    """,
                    (
                        topic_data["title"],
                        topic_data["subtitle"],
                        topic_data["icon"],
                        topic_data["time_limit_sec"],
                        topic_id
                    )
                )
            else:
                # Создаем новую тему
                db.execute(
                    """
                    INSERT INTO topics (id, title, subtitle, icon, time_limit_sec)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        topic_id,
                        topic_data["title"],
                        topic_data["subtitle"],
                        topic_data["icon"],
                        topic_data["time_limit_sec"]
                    )
                )
                topics_count += 1
            
            # Импортируем вопросы для этой темы
            for q_data in topic_data.get("questions", []):
                qid = q_data["id"]
                
                # Проверяем, существует ли вопрос
                existing_q = db.execute(
                    "SELECT id FROM questions WHERE qid = ?", (qid,)
                ).fetchone()
                
                options_json = json.dumps(q_data.get("options", []), ensure_ascii=False)
                correct_json = json.dumps(q_data.get("correct", []), ensure_ascii=False)
                
                if existing_q:
                    # Обновляем существующий вопрос
                    db.execute(
                        """
                        UPDATE questions 
                        SET type = ?, text = ?, options_json = ?, correct_json = ?,
                            difficulty = ?, explanation = ?, bloom_level = ?
                        WHERE qid = ?
                        """,
                        (
                            q_data["type"],
                            q_data["text"],
                            options_json,
                            correct_json,
                            q_data.get("difficulty", "medium"),
                            q_data.get("explanation", ""),
                            q_data.get("bloom_level", "understand"),
                            qid
                        )
                    )
                else:
                    # Создаем новый вопрос
                    db.execute(
                        """
                        INSERT INTO questions 
                        (topic_id, qid, type, text, options_json, correct_json,
                         difficulty, explanation, bloom_level)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            topic_id,
                            qid,
                            q_data["type"],
                            q_data["text"],
                            options_json,
                            correct_json,
                            q_data.get("difficulty", "medium"),
                            q_data.get("explanation", ""),
                            q_data.get("bloom_level", "understand")
                        )
                    )
                    questions_count += 1
                
                # Обновляем таблицу options
                question_row = db.execute(
                    "SELECT id FROM questions WHERE qid = ?", (qid,)
                ).fetchone()
                
                if question_row:
                    question_db_id = question_row["id"]
                    
                    # Удаляем старые опции
                    db.execute("DELETE FROM options WHERE question_id = ?", (question_db_id,))
                    
                    # Добавляем новые опции
                    for opt in q_data.get("options", []):
                        opt_id = opt.get("id", "")
                        opt_text = opt.get("text", "")
                        is_correct = 1 if opt_id in q_data.get("correct", []) else 0
                        
                        db.execute(
                            """
                            INSERT INTO options (question_id, option_id, option_text, is_correct)
                            VALUES (?, ?, ?, ?)
                            """,
                            (question_db_id, opt_id, opt_text, is_correct)
                        )
        
        db.commit()
        
        print(f"✅ Импорт завершен успешно!")
        print(f"📊 Новых тем: {topics_count}")
        print(f"📚 Новых вопросов: {questions_count}")
        
        # Выводим статистику по темам
        topic_stats = db.execute("""
            SELECT t.title, COUNT(q.id) as q_count
            FROM topics t
            LEFT JOIN questions q ON q.topic_id = t.id
            GROUP BY t.id
        """).fetchall()
        
        print("\n📈 Статистика по темам:")
        for row in topic_stats:
            print(f"   • {row['title']}: {row['q_count']} вопросов")
        
        return True
        
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка при импорте: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Импорт расширенного банка вопросов")
    print("=" * 60)
    print()
    
    if not DB_PATH.exists():
        print(f"❌ База данных не найдена: {DB_PATH}")
        print("Сначала запустите приложение для создания базы данных.")
        sys.exit(1)
    
    success = import_topics_and_questions()
    sys.exit(0 if success else 1)
