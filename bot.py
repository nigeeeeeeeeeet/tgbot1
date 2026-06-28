import asyncio
import random
import string
import re
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import httpx

# Токен берётся из переменной окружения (безопасно!)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Фонетика ──────────────────────────────────
VOWELS     = list("aeiou")
SOFT_CONS  = list("bdfghjklmnrstvw")

PATTERNS = [
    "CVCVC",
    "VCVCV",
    "CVCCV",
    "CVCVV",
    "VCCVC",
]

def generate_from_pattern(pattern: str) -> str:
    result = []
    for ch in pattern:
        if ch == "C":
            result.append(random.choice(SOFT_CONS))
        else:
            result.append(random.choice(VOWELS))
    return "".join(result)

def score_username(name: str) -> float:
    score = 5.0
    n = name.lower()

    if re.search(r"[bcdfghjklmnprstvwxyz]{3}", n):
        score -= 2.5
    if re.search(r"[aeiou]{3}", n):
        score -= 1.5

    alternations = sum(
        1 for i in range(len(n) - 1)
        if (n[i] in "aeiou") != (n[i+1] in "aeiou")
    )
    score += alternations * 0.5

    if n[-1] in "aeiou":
        score += 0.5

    ugly = ["xz", "zx", "qq", "ww", "vv", "kk", "jj", "xx"]
    for combo in ugly:
        if combo in n:
            score -= 1.5

    nice = ["li", "la", "ri", "ra", "ni", "na", "mi", "ma", "vi", "va",
            "ti", "ta", "si", "sa", "ki", "ka", "ro", "lo", "no", "mo"]
    for combo in nice:
        if combo in n:
            score += 0.4

    return round(min(max(score, 0), 10), 1)

def generate_candidates(count: int = 300) -> list[tuple[str, float]]:
    candidates = set()
    while len(candidates) < count // 2:
        pat = random.choice(PATTERNS)
        name = generate_from_pattern(pat)
        candidates.add(name)
    while len(candidates) < count:
        name = "".join(random.choices(string.ascii_lowercase, k=5))
        candidates.add(name)
    scored = [(name, score_username(name)) for name in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored

async def check_username_available(username: str) -> bool | None:
    url = f"https://t.me/{username}"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                if "tgme_page_title" in r.text or ('"@' + username.lower() + '"') in r.text.lower():
                    return False
                return True
            return None
    except Exception:
        return None

# ── Хэндлеры ──────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Username Finder Bot*\n\n"
        "Ищу красивые свободные 5-буквенные юзернеймы для Telegram.\n\n"
        "📌 *Как работает оценка:*\n"
        "• Чередование гласных/согласных → плавно звучит\n"
        "• Красивые слоги (la, ri, ma...) → приятно выговаривать\n"
        "• Штраф за три согласных подряд\n\n"
        "Жми кнопку ниже 👇"
    )
    keyboard = [[InlineKeyboardButton("🔍 Найти юзернеймы", callback_data="search")]]
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def search_usernames(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    msg = await query.message.reply_text("⏳ Генерирую и проверяю доступность...")

    candidates = generate_candidates(300)
    top = candidates[:30]
    found = []
    checked = 0

    for name, score in top:
        if len(found) >= 5:
            break
        checked += 1
        available = await check_username_available(name)
        if available or available is None:
            found.append((name, score))
        if checked % 5 == 0:
            try:
                await msg.edit_text(f"⏳ Проверено: {checked}/30 | Найдено: {len(found)}/5")
            except Exception:
                pass
        await asyncio.sleep(0.3)

    if not found:
        result = "😔 Не нашёл свободных. Попробуй ещё раз!"
    else:
        lines = ["🎯 *Топ 5-буквенных юзернеймов:*\n"]
        for i, (name, score) in enumerate(found, 1):
            bar = "🟩" * round(score) + "⬜" * (10 - round(score))
            lines.append(
                f"{i}. `@{name}` — {score}/10\n"
                f"   {bar}\n"
                f"   👉 [Проверить](https://t.me/{name})"
            )
        result = "\n".join(lines)

    keyboard = [
        [InlineKeyboardButton("🔄 Ещё варианты", callback_data="search")],
        [InlineKeyboardButton("🎲 Паттерн CVCVC", callback_data="pattern_cvcvc")],
        [InlineKeyboardButton("✨ С гласной в конце", callback_data="end_vowel")],
    ]
    try:
        await msg.edit_text(
            result, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
    except Exception:
        await query.message.reply_text(
            result, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )

async def search_pattern(update: Update, context: ContextTypes.DEFAULT_TYPE, pattern_filter: str):
    query = update.callback_query
    await query.answer()
    msg = await query.message.reply_text("⏳ Ищу по паттерну...")

    found = []
    attempts = 0
    while len(found) < 5 and attempts < 60:
        attempts += 1
        if pattern_filter == "cvcvc":
            name = generate_from_pattern("CVCVC")
        else:
            pat = random.choice(["CVCVV", "VCVCV", "CVCVC"])
            name = generate_from_pattern(pat)
            if name[-1] not in "aeiou":
                continue

        score = score_username(name)
        if score < 5:
            continue
        available = await check_username_available(name)
        if available or available is None:
            found.append((name, score))
        await asyncio.sleep(0.3)

    lines = [f"🎯 *Результат (фильтр: {pattern_filter}):*\n"]
    for i, (name, score) in enumerate(found, 1):
        bar = "🟩" * round(score) + "⬜" * (10 - round(score))
        lines.append(
            f"{i}. `@{name}` — {score}/10\n"
            f"   {bar}\n"
            f"   👉 [Проверить](https://t.me/{name})"
        )

    keyboard = [
        [InlineKeyboardButton("🔄 Ещё", callback_data=f"pattern_{pattern_filter}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="search")],
    ]
    await msg.edit_text(
        "\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "search":
        await search_usernames(update, context)
    elif data == "pattern_cvcvc":
        await search_pattern(update, context, "cvcvc")
    elif data == "end_vowel":
        await search_pattern(update, context, "end_vowel")

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан! Добавь его в переменные окружения.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("✅ Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
