import asyncio
import random
import string
import re
import os
import logging
from typing import Optional, List, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import httpx

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Фонетика ──────────────────────────────────────────────────────────────────
VOWELS    = list("aeiou")
SOFT_CONS = list("bdfghjklmnrstvw")
PATTERNS  = ["CVCVC", "VCVCV", "CVCCV", "CVCVV", "VCCVC"]

NICE_COMBOS = [
    "li","la","ri","ra","ni","na","mi","ma",
    "vi","va","ti","ta","si","sa","ki","ka",
    "ro","lo","no","mo","de","re","le","ne",
]
UGLY_COMBOS = ["xz","zx","qq","ww","vv","kk","jj","xx","ck","gn","mn"]


def escape_md(text: str) -> str:
    """Экранирует спецсимволы для MarkdownV2."""
    return re.sub(r'([_*\[\]()~`>#+=|{}.!\\-])', r'\\\1', text)


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
        if (n[i] in "aeiou") != (n[i + 1] in "aeiou")
    )
    score += alternations * 0.5

    if n[-1] in "aeiou":
        score += 0.5

    for combo in UGLY_COMBOS:
        if combo in n:
            score -= 1.5

    for combo in NICE_COMBOS:
        if combo in n:
            score += 0.4

    return round(min(max(score, 0.0), 10.0), 1)


def generate_candidates(count: int = 300) -> List[Tuple[str, float]]:
    candidates: set = set()
    while len(candidates) < count // 2:
        candidates.add(generate_from_pattern(random.choice(PATTERNS)))
    while len(candidates) < count:
        candidates.add("".join(random.choices(string.ascii_lowercase, k=5)))
    scored = [(n, score_username(n)) for n in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ── Проверка через Telegram Bot API (getChat) ─────────────────────────────────
async def check_username_available(username: str, token: str) -> Optional[bool]:
    """
    True  = юзернейм свободен
    False = занят
    None  = не удалось определить
    """
    url = f"https://api.telegram.org/bot{token}/getChat"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.post(url, json={"chat_id": f"@{username}"})
            data = r.json()
            logger.info("getChat @%s → %s", username, data)
            if data.get("ok"):
                return False   # getChat успешен → юзернейм занят
            err = data.get("description", "").lower()
            # Telegram возвращает разные варианты для несуществующего юзернейма
            free_phrases = ["not found", "chat not found", "invalid username", "no username"]
            if any(p in err for p in free_phrases):
                return True    # юзернейм свободен
            # 429 = флуд-лимит, ждём
            if r.status_code == 429:
                retry = int(r.headers.get("Retry-After", 3))
                await asyncio.sleep(retry)
                return None
            return None
    except Exception as e:
        logger.warning("check_username_available error: %s", e)
        return None


# ── Построение текста результата ──────────────────────────────────────────────
def build_result_text(found: List[Tuple], title: str) -> str:
    lines = [f"🎯 *{escape_md(title)}*\n"]
    for i, item in enumerate(found, 1):
        name, score = item[0], item[1]
        confirmed = item[2] if len(item) > 2 else False
        filled = round(score)
        bar = "🟩" * filled + "⬜" * (10 - filled)
        status = "✅ свободен" if confirmed else "❓ возможно свободен"
        lines.append(
            f"{i}\\. `@{name}` — {score}/10 {escape_md(status)}\n"
            f"   {bar}\n"
            f"   👉 [Проверить](https://t\\.me/{name})"
        )
    return "\n".join(lines)


# ── Поиск юзернеймов ──────────────────────────────────────────────────────────
async def run_search(
    token: str,
    pattern_filter: Optional[str] = None,
    progress_msg=None,
) -> List[Tuple]:
    found: List[Tuple] = []

    if pattern_filter is None:
        candidates = generate_candidates(300)
        top = candidates[:40]
        for idx, (name, score) in enumerate(top, 1):
            if len(found) >= 5:
                break
            available = await check_username_available(name, token)
            if available is True:
                found.append((name, score, True))   # подтверждённо свободен
            elif available is None and score >= 7.0:
                found.append((name, score, False))  # не проверен, но красивый
            if progress_msg and idx % 5 == 0:
                try:
                    await progress_msg.edit_text(
                        f"⏳ Проверено: {idx}/40 | Найдено: {len(found)}/5"
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.4)
    else:
        attempts = 0
        while len(found) < 5 and attempts < 80:
            attempts += 1
            if pattern_filter == "cvcvc":
                name = generate_from_pattern("CVCVC")
            else:
                pat = random.choice(["CVCVV", "VCVCV", "CVCVC"])
                name = generate_from_pattern(pat)
                if name[-1] not in "aeiou":
                    continue
            score = score_username(name)
            if score < 5.0:
                continue
            available = await check_username_available(name, token)
            if available is True:
                found.append((name, score, True))
            elif available is None and score >= 7.0:
                found.append((name, score, False))
            await asyncio.sleep(0.4)

    return found


# ── Хэндлеры ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 *Username Finder Bot*\n\n"
        "Ищу красивые свободные 5\\-буквенные юзернеймы для Telegram\\.\n\n"
        "📌 *Как работает оценка:*\n"
        "• Чередование гласных/согласных → плавно звучит\n"
        "• Красивые слоги \\(la, ri, ma\\.\\.\\. \\) → легко выговаривать\n"
        "• Штраф за три согласных подряд\n\n"
        "Жми кнопку ниже 👇"
    )
    keyboard = [[InlineKeyboardButton("🔍 Найти юзернеймы", callback_data="search")]]
    await update.message.reply_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def search_usernames(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    msg = await query.message.reply_text("⏳ Генерирую и проверяю доступность…")

    token = context.bot.token
    found = await run_search(token, progress_msg=msg)

    if not found:
        result = "😔 Не нашёл свободных\\. Попробуй ещё раз\\!"
    else:
        result = build_result_text(found, "Топ 5-буквенных юзернеймов:")

    keyboard = [
        [InlineKeyboardButton("🔄 Ещё варианты",      callback_data="search")],
        [InlineKeyboardButton("🎲 Паттерн CVCVC",     callback_data="pattern_cvcvc")],
        [InlineKeyboardButton("✨ С гласной в конце", callback_data="end_vowel")],
    ]
    try:
        await msg.edit_text(
            result,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("Не удалось отредактировать сообщение: %s", e)
        await query.message.reply_text(
            result,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )


async def search_pattern(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pattern_filter: str,
) -> None:
    query = update.callback_query
    await query.answer()
    msg = await query.message.reply_text("⏳ Ищу по паттерну…")

    token = context.bot.token
    found = await run_search(token, pattern_filter=pattern_filter)

    label = "CVCVC" if pattern_filter == "cvcvc" else "гласная в конце"
    if not found:
        result = "😔 Не нашёл свободных\\. Попробуй ещё раз\\!"
    else:
        result = build_result_text(found, f"Результат ({label}):")

    keyboard = [
        [InlineKeyboardButton("🔄 Ещё", callback_data=f"pattern_{pattern_filter}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="search")],
    ]
    try:
        await msg.edit_text(
            result,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("Не удалось отредактировать сообщение: %s", e)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data
    if data == "search":
        await search_usernames(update, context)
    elif data == "pattern_cvcvc":
        await search_pattern(update, context, "cvcvc")
    elif data == "end_vowel":
        await search_pattern(update, context, "end_vowel")
    else:
        await update.callback_query.answer("Неизвестная команда")


# ── Запуск ────────────────────────────────────────────────────────────────────
def main() -> None:
    if not BOT_TOKEN:
        raise ValueError(
            "BOT_TOKEN не задан!\n"
            "Локально: export BOT_TOKEN=твой_токен\n"
            "Railway: добавь в Variables → BOT_TOKEN"
        )
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
