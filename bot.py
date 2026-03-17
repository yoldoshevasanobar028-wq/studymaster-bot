import os
import asyncio
import json
import logging
import sqlite3
from urllib.parse import quote_plus

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from google import genai


BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN topilmadi. Railway Variables ga qo'ying.")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY topilmadi. Railway Variables ga qo'ying.")


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = genai.Client(api_key=GEMINI_API_KEY)

DB_NAME = "studymaster.db"
user_modes = {}
current_quizzes = {}

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📚 Mavzu izlash"), KeyboardButton(text="🎥 Video darslar")],
        [KeyboardButton(text="❓ Savol-javob"), KeyboardButton(text="📝 Quiz/Test")],
        [KeyboardButton(text="📊 Natijalarim"), KeyboardButton(text="🧠 O'zlashtirish tahlili")],
        [KeyboardButton(text="🌟 Kun savoli va faktlari")]
    ],
    resize_keyboard=True
)


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            full_name TEXT,
            username TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            topic TEXT,
            total_questions INTEGER,
            correct_answers INTEGER,
            score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def add_user(telegram_id: int, full_name: str, username: str | None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO users (telegram_id, full_name, username)
        VALUES (?, ?, ?)
    """, (telegram_id, full_name, username))
    conn.commit()
    conn.close()


def save_result(telegram_id: int, topic: str, total_questions: int, correct_answers: int, score: float):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO results (telegram_id, topic, total_questions, correct_answers, score)
        VALUES (?, ?, ?, ?, ?)
    """, (telegram_id, topic, total_questions, correct_answers, score))
    conn.commit()
    conn.close()


def get_results(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT topic, total_questions, correct_answers, score, created_at
        FROM results
        WHERE telegram_id = ?
        ORDER BY created_at DESC
    """, (telegram_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_analysis(telegram_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT topic, score
        FROM results
        WHERE telegram_id = ?
    """, (telegram_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def ask_gemini(prompt: str) -> str:
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = getattr(response, "text", None)
        return text.strip() if text and text.strip() else "Javob topilmadi."
    except Exception as e:
        error_text = str(e)
        if "API key was reported as leaked" in error_text:
            raise ValueError("GEMINI_API_KEY bloklangan. Yangi API key oling va Railway Variables ga qo'ying.")
        raise ValueError(f"Gemini bilan ulanishda xatolik: {error_text}")


def generate_topic_explanation(topic: str) -> str:
    prompt = f"""
Sen talabalar uchun aqlli o‘quv yordamchisisan.
Javobni o‘zbek tilida, sodda, tushunarli va tartibli yoz.

Mavzu: {topic}

Javob tuzilmasi:
1. Ta'rif
2. Asosiy tushuncha
3. Muhim nuqtalar
4. Oddiy misol
5. Xulosa
"""
    return ask_gemini(prompt)


def generate_qa_answer(question: str) -> str:
    prompt = f"""
Sen talabalar uchun savol-javob yordamchisisan.
Javobni o'zbek tilida, sodda va tushunarli yoz.

Savol: {question}
"""
    return ask_gemini(prompt)


def generate_daily_fact_and_question() -> str:
    prompt = """
Talabalar uchun:
- 1 ta qiziqarli fakt
- 1 ta kreativ savol
- 1 ta qisqa motivatsion gap

Javobni faqat o'zbek tilida yoz.
"""
    return ask_gemini(prompt)


def generate_quiz(topic: str):
    prompt = f"""
Sen quiz tuzuvchi yordamchisan.
Mavzu: {topic}

Faqat JSON qaytar.

Format:
{{
  "topic": "{topic}",
  "questions": [
    {{
      "question": "Savol matni",
      "options": {{
        "A": "variant A",
        "B": "variant B",
        "C": "variant C",
        "D": "variant D"
      }},
      "correct_answer": "A"
    }}
  ]
}}

Talablar:
- 15 ta savol
- har savolda A, B, C, D variant
- correct_answer faqat A, B, C yoki D
"""

    raw = ask_gemini(prompt).replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Quiz JSON formatida xatolik bor.")

    if "topic" not in data or "questions" not in data:
        raise ValueError("Quiz ma'lumotlari to'liq emas.")

    questions = []
    for q in data["questions"]:
        if (
            isinstance(q, dict)
            and "question" in q
            and "options" in q
            and isinstance(q["options"], dict)
            and all(k in q["options"] for k in ["A", "B", "C", "D"])
            and str(q.get("correct_answer", "")).upper() in ["A", "B", "C", "D"]
        ):
            questions.append({
                "question": q["question"],
                "options": q["options"],
                "correct_answer": str(q["correct_answer"]).upper()
            })

    if not questions:
        raise ValueError("Yaroqli test savollari topilmadi.")

    return {
        "topic": data["topic"],
        "questions": questions[:15]
    }


async def send_quiz_question(message: Message, user_id: int):
    quiz = current_quizzes[user_id]
    idx = quiz["current_index"]
    q = quiz["questions"][idx]

    text = (
        f"📝 Test {idx + 1}/{len(quiz['questions'])}\n"
        f"📘 Mavzu: {quiz['topic']}\n\n"
        f"❓ {q['question']}\n\n"
        f"A) {q['options']['A']}\n"
        f"B) {q['options']['B']}\n"
        f"C) {q['options']['C']}\n"
        f"D) {q['options']['D']}\n\n"
        "Javobni faqat A, B, C yoki D ko'rinishida yuboring."
    )
    await message.answer(text)


@dp.message(CommandStart())
async def start_handler(message: Message):
    add_user(
        telegram_id=message.from_user.id,
        full_name=message.from_user.full_name,
        username=message.from_user.username
    )

    user_modes[message.from_user.id] = None

    text = (
        f"Salom, {message.from_user.full_name}!\n\n"
        "StudyMaster Bot ga xush kelibsiz 🎓\n\n"
        "Bu bot orqali siz:\n"
        "• mavzu bo'yicha ma'lumot olasiz\n"
        "• video dars linkini topasiz\n"
        "• savol-javob qilasiz\n"
        "• quiz/test ishlaysiz\n"
        "• natijalaringizni ko'rasiz\n"
        "• o'zlashtirishingizni tahlil qilasiz\n"
        "• kun savoli va faktlarini olasiz"
    )

    await message.answer(text, reply_markup=main_keyboard)


@dp.message(F.text == "📚 Mavzu izlash")
async def topic_button_handler(message: Message):
    user_modes[message.from_user.id] = "topic"
    await message.answer("Qaysi mavzu bo'yicha ma'lumot kerak? Mavzuni yozing.")


@dp.message(F.text == "🎥 Video darslar")
async def video_button_handler(message: Message):
    user_modes[message.from_user.id] = "video"
    await message.answer("Qaysi mavzu bo'yicha video dars kerak? Mavzuni yozing.")


@dp.message(F.text == "❓ Savol-javob")
async def qa_button_handler(message: Message):
    user_modes[message.from_user.id] = "qa"
    await message.answer("Savolingizni yozing.")


@dp.message(F.text == "📝 Quiz/Test")
async def quiz_button_handler(message: Message):
    user_modes[message.from_user.id] = "quiz"
    await message.answer("Qaysi mavzu bo'yicha test ishlamoqchisiz? Mavzuni yozing.")


@dp.message(F.text == "📊 Natijalarim")
async def results_button_handler(message: Message):
    rows = get_results(message.from_user.id)

    if not rows:
        await message.answer("Sizda hali test natijalari mavjud emas.")
        return

    text = "📊 Sizning oxirgi natijalaringiz:\n\n"
    for topic, total, correct, score, created_at in rows[:5]:
        text += (
            f"📘 Mavzu: {topic}\n"
            f"✅ To'g'ri javob: {correct}/{total}\n"
            f"📈 Natija: {score:.1f}%\n"
            f"🕒 Sana: {created_at}\n\n"
        )

    await message.answer(text)


@dp.message(F.text == "🧠 O'zlashtirish tahlili")
async def analysis_button_handler(message: Message):
    rows = get_analysis(message.from_user.id)

    if not rows:
        await message.answer("Tahlil uchun hali natijalar yetarli emas.")
        return

    avg = sum(score for _, score in rows) / len(rows)

    topic_scores = {}
    for topic, score in rows:
        topic_scores.setdefault(topic, []).append(score)

    avg_topic_scores = {topic: sum(scores) / len(scores) for topic, scores in topic_scores.items()}
    strongest_topic = max(avg_topic_scores, key=avg_topic_scores.get)
    weakest_topic = min(avg_topic_scores, key=avg_topic_scores.get)

    if avg >= 85:
        level = "A'lo"
    elif avg >= 70:
        level = "Yaxshi"
    elif avg >= 50:
        level = "O'rtacha"
    else:
        level = "Qo'shimcha ishlash kerak"

    text = (
        f"🧠 O'zlashtirish tahlili:\n\n"
        f"📊 Umumiy o'rtacha: {avg:.1f}%\n"
        f"🏷 Daraja: {level}\n"
        f"💪 Eng yaxshi mavzu: {strongest_topic}\n"
        f"📉 Kuchsizroq mavzu: {weakest_topic}\n\n"
        f"Tavsiya: {weakest_topic} bo'yicha ko'proq mashq qiling."
    )

    await message.answer(text)


@dp.message(F.text == "🌟 Kun savoli va faktlari")
async def daily_button_handler(message: Message):
    await message.answer("⏳ Kun savoli va faktlari tayyorlanmoqda...")
    try:
        await message.answer(generate_daily_fact_and_question())
    except Exception as e:
        await message.answer(f"❌ Xatolik yuz berdi:\n{e}")


@dp.message()
async def main_text_handler(message: Message):
    user_id = message.from_user.id
    text = (message.text or "").strip()
    mode = user_modes.get(user_id)

    if not text:
        await message.answer("Iltimos, matn yuboring.")
        return

    if user_id in current_quizzes:
        answer = text.upper()

        if answer not in ["A", "B", "C", "D"]:
            await message.answer("Javobni faqat A, B, C yoki D ko'rinishida yuboring.")
            return

        quiz = current_quizzes[user_id]
        q = quiz["questions"][quiz["current_index"]]

        if answer == q["correct_answer"]:
            quiz["correct_count"] += 1

        quiz["current_index"] += 1

        if quiz["current_index"] < len(quiz["questions"]):
            await send_quiz_question(message, user_id)
        else:
            total = len(quiz["questions"])
            correct_answers = quiz["correct_count"]
            score = (correct_answers / total) * 100

            save_result(user_id, quiz["topic"], total, correct_answers, score)

            await message.answer(
                f"✅ Test yakunlandi!\n\n"
                f"📘 Mavzu: {quiz['topic']}\n"
                f"✅ To'g'ri javoblar: {correct_answers}/{total}\n"
                f"📈 Natija: {score:.1f}%"
            )

            del current_quizzes[user_id]
            user_modes[user_id] = None
        return

    if mode == "topic":
        await message.answer("⏳ Mavzu bo'yicha ma'lumot tayyorlanmoqda...")
        try:
            await message.answer(generate_topic_explanation(text))
        except Exception as e:
            await message.answer(f"❌ Xatolik yuz berdi:\n{e}")
        user_modes[user_id] = None
        return

    if mode == "video":
        search_query = quote_plus(f"{text} dars")
        youtube_url = f"https://www.youtube.com/results?search_query={search_query}"
        await message.answer(f"🎥 {text} bo'yicha video darslar:\n\n{youtube_url}")
        user_modes[user_id] = None
        return

    if mode == "qa":
        await message.answer("⏳ Javob tayyorlanmoqda...")
        try:
            await message.answer(generate_qa_answer(text))
        except Exception as e:
            await message.answer(f"❌ Xatolik yuz berdi:\n{e}")
        user_modes[user_id] = None
        return

    if mode == "quiz":
        await message.answer("⏳ Test savollari tayyorlanmoqda...")
        try:
            quiz_data = generate_quiz(text)
            current_quizzes[user_id] = {
                "topic": quiz_data["topic"],
                "questions": quiz_data["questions"],
                "current_index": 0,
                "correct_count": 0
            }
            await send_quiz_question(message, user_id)
        except Exception as e:
            await message.answer(f"❌ Test yaratishda xatolik yuz berdi:\n{e}")
        user_modes[user_id] = None
        return

    await message.answer(
        "Kerakli bo'limni menyudan tanlang.\n\n"
        "📚 Mavzu izlash\n"
        "🎥 Video darslar\n"
        "❓ Savol-javob\n"
        "📝 Quiz/Test"
    )


async def main():
    init_db()
    logging.info("Bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())