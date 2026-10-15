#!/usr/bin/env python3
"""
Скрипт для создания реалистичных демо-данных.
Создает группы, учеников с казахскими/русскими именами, и реалистичные попытки тестов.
"""

import json
import os
import sys
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Добавляем родительскую директорию в путь
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from werkzeug.security import generate_password_hash

DB_PATH = BASE_DIR / "data" / "infotrainer.db"

# Список реалистичных имен (казахские и русские)
STUDENT_NAMES = [
    ("Айдар", "Аidar"), ("Алмас", "Almas"), ("Арман", "Arman"), ("Бауыржан", "Bauyrzhan"),
    ("Ерлан", "Erlan"), ("Ербол", "Erbol"), ("Жандос", "Zhandos"), ("Қайрат", "Kairat"),
    ("Мади", "Madi"), ("Нұрлан", "Nurlan"), ("Руслан", "Ruslan"), ("Серік", "Serik"),
    ("Талғат", "Talgat"), ("Улан", "Ulan"), ("Шынғыс", "Shyngys"),
    ("Айгуль", "Aigul"), ("Айжан", "Aizhan"), ("Алия", "Aliya"), ("Асель", "Asel"),
    ("Ботагоз", "Botagoz"), ("Гульназ", "Gulnaz"), ("Дана", "Dana"), ("Елена", "Elena"),
    ("Жанар", "Zhanar"), ("Инна", "Inna"), ("Карлыгаш", "Karlygash"), ("Ляззат", "Lyazzat"),
    ("Мария", "Maria"), ("Наталья", "Natalya"), ("Ольга", "Olga"), ("Светлана", "Svetlana"),
    ("Татьяна", "Tatyana"), ("Умит", "Umit")
]

# Фамилии
SURNAMES = [
    "Иванов", "Петров", "Сидоров", "Смирнов", "Кузнецов", "Попов", "Васильев", "Соколов",
    "Михайлов", "Новиков", "Федоров", "Морозов", "Волков", "Алексеев", "Лебедев", "Семенов",
    "Егоров", "Павлов", "Козлов", "Степанов", "Николаев", "Орлов", "Андреев", "Макаров",
    "Нурланов", "Ахметов", "Оспанов", "Ермеков", "Куанышев", "Сериков", "Байтенов", "Тлеубаев"
]


def create_demo_users(db, group_id, teacher_id):
    """Создание демо-учеников для группы."""
    created_users = []
    
    # Создаем 15-25 учеников
    num_students = random.randint(15, 25)
    selected_names = random.sample(STUDENT_NAMES, min(num_students, len(STUDENT_NAMES)))
    
    for i, (name_kk, name_en) in enumerate(selected_names):
        surname = random.choice(SURNAMES)
        username = f"{name_en.lower()}.{surname.lower()}"
        
        # Проверяем, существует ли пользователь
        existing = db.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        
        if existing:
            username = f"{username}{random.randint(1, 99)}"
        
        password_hash = generate_password_hash("Student123")
        
        cursor = db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, password_hash, "student")
        )
        user_id = cursor.lastrowid
        
        # Добавляем в группу
        db.execute(
            "INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?, ?)",
            (group_id, user_id)
        )
        
        created_users.append({
            "id": user_id,
            "username": username,
            "name": f"{name_kk} {surname}"
        })
    
    return created_users


def create_test_attempts(db, users, topic_ids):
    """Создание реалистичных попыток тестов для учеников."""
    attempts_created = 0
    
    now = datetime.now(timezone.utc)
    
    for user in users:
        # Каждый ученик проходит 3-8 тестов
        num_attempts = random.randint(3, 8)
        
        for _ in range(num_attempts):
            topic_id = random.choice(topic_ids)
            
            # Создаем дату в пределах последних 6 недель
            days_ago = random.randint(0, 42)
            started_at = now - timedelta(days=days_ago, hours=random.randint(0, 23))
            
            # Реалистичная длительность: от 40% до 95% лимита времени
            topic = db.execute(
                "SELECT time_limit_sec FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            
            if topic:
                time_limit = topic["time_limit_sec"]
                duration_factor = random.uniform(0.4, 0.95)
                duration_sec = int(time_limit * duration_factor)
                
                finished_at = started_at + timedelta(seconds=duration_sec)
                
                # Случайный процент правильных ответов (20% - 95%)
                percent = round(random.uniform(20, 95), 1)
                
                # Примерное количество вопросов
                num_questions = random.randint(8, 20)
                score = int(num_questions * percent / 100)
                
                # Создаем attempt
                cursor = db.execute(
                    """
                    INSERT INTO attempts 
                    (user_id, topic_id, started_at, deadline_at, finished_at, status, 
                     question_order_json, answers_json, duration_sec, suspicious_flag)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        topic_id,
                        started_at.isoformat(),
                        (started_at + timedelta(seconds=time_limit)).isoformat(),
                        finished_at.isoformat(),
                        "submitted",
                        "[]",
                        "{}",
                        duration_sec,
                        1 if duration_sec < num_questions * 2 else 0
                    )
                )
                attempt_id = cursor.lastrowid
                
                # Получаем название темы
                topic_title = db.execute(
                    "SELECT title FROM topics WHERE id = ?", (topic_id,)
                ).fetchone()["title"]
                
                # Создаем result
                review_data = {
                    "questions": [],
                    "started_at": started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                    "time_expired": False,
                    "suspicious": duration_sec < num_questions * 2
                }
                
                db.execute(
                    """
                    INSERT INTO results 
                    (user_id, topic_id, topic_title, score, max_score, percent, 
                     duration_sec, finished_at, review_json, attempt_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"],
                        topic_id,
                        topic_title,
                        score,
                        num_questions,
                        percent,
                        duration_sec,
                        finished_at.isoformat(),
                        json.dumps(review_data, ensure_ascii=False),
                        attempt_id
                    )
                )
                
                attempts_created += 1
    
    return attempts_created


def main():
    """Главная функция создания демо-данных."""
    print("=" * 60)
    print("🚀 Создание демо-данных для ИнфоТренер")
    print("=" * 60)
    print()
    
    if not DB_PATH.exists():
        print(f"❌ База данных не найдена: {DB_PATH}")
        print("Сначала запустите приложение для создания базы данных.")
        return False
    
    db = get_db()
    
    try:
        # Проверяем, есть ли преподаватели
        teachers = db.execute(
            "SELECT id FROM users WHERE role = 'teacher' LIMIT 1"
        ).fetchall()
        
        if not teachers:
            print("❌ Не найдено ни одного преподавателя.")
            print("Сначала создайте преподавателя через регистрацию.")
            return False
        
        teacher_id = teachers[0]["id"]
        
        # Получаем ID тем
        topics = db.execute("SELECT id FROM topics").fetchall()
        if not topics:
            print("❌ Не найдено ни одной темы.")
            print("Сначала импортируйте вопросы через scripts/seed_questions.py")
            return False
        
        topic_ids = [t["id"] for t in topics]
        
        print(f"✅ Найдено {len(topic_ids)} тем")
        print()
        
        # Создаем группы
        print("📚 Создание учебных групп...")
        groups = [
            ("ИС-21", "2", "Информационные системы"),
            ("ПО-22", "2", "Программное обеспечение"),
            ("САУ-21", "2", "Системы автоматизации")
        ]
        
        created_groups = []
        for group_name, course, specialty in groups:
            invite_code = f"GROUP-{group_name}-{random.randint(1000, 9999)}"
            
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO groups (name, course, specialty, invite_code, created_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (group_name, course, specialty, invite_code, teacher_id)
            )
            
            if cursor.lastrowid:
                created_groups.append((cursor.lastrowid, group_name))
            else:
                # Группа уже существует, получаем ID
                existing = db.execute(
                    "SELECT id FROM groups WHERE name = ?", (group_name,)
                ).fetchone()
                if existing:
                    created_groups.append((existing["id"], group_name))
        
        print(f"✅ Создано {len(created_groups)} групп")
        print()
        
        # Создаем учеников для каждой группы
        all_students = []
        for group_id, group_name in created_groups:
            print(f"👨‍🎓 Создание учеников для группы {group_name}...")
            students = create_demo_users(db, group_id, teacher_id)
            all_students.extend(students)
            print(f"   Создано {len(students)} учеников")
        
        print()
        print(f"✅ Всего создано {len(all_students)} учеников")
        print()
        
        # Создаем попытки тестов
        print("📝 Создание попыток тестов...")
        attempts_created = create_test_attempts(db, all_students, topic_ids)
        
        print(f"✅ Создано {attempts_created} попыток")
        print()
        
        db.commit()
        
        print("=" * 60)
        print("🎉 Демо-данные успешно созданы!")
        print("=" * 60)
        print()
        print("📊 Статистика:")
        print(f"   • Групп: {len(created_groups)}")
        print(f"   • Учеников: {len(all_students)}")
        print(f"   • Попыток тестов: {attempts_created}")
        print()
        print("🔑 Демо-аккаунты:")
        print("   • teacher / Teacher123")
        print("   • student / Student123")
        
        return True
        
    except Exception as e:
        db.rollback()
        print(f"❌ Ошибка при создании демо-данных: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
