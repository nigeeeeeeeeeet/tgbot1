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

VOWELS    = list("aeiou")
SOFT_CONS = list("bdfghjklmnrstvw")

# Паттерны: добавлены 4-буквенные (Fragment ценит краткость)
PATTERNS_5 = ["CVCVC", "VCVCV", "CVCCV", "CVCVV", "VCCVC"]
# ── Комбо-оценки ──────────────────────────────────────────────────────────────

# Красивые комбо (Fragment-стиль: мелодичность, короткие слоги)
NICE_COMBOS = [
    "li","la","ri","ra","ni","na","mi","ma",
    "vi","va","ti","ta","si","sa","ki","ka",
    "ro","lo","no","mo","de","re","le","ne",
    # Fragment-популярные звуки
    "ora","elo","ava","eli","aria","iro",
    "ano","ino","ola","ela","ara","iri",
    "ova","eve","ike","oka","ala","ile",
]

# Уродливые комбо (штрафуем)
UGLY_COMBOS = [
    "xz","zx","qq","ww","vv","kk","jj","xx",
    "ck","gn","mn","ng","mb","nk","nt","nd",
    "rr","ss","tt","ll","ff","pp","bb","dd",
    "tch","sch","str","spl","spr",
]


# ── Генерация ─────────────────────────────────────────────────────────────────

def generate_from_pattern(pattern: str) -> str:
    result = []
    for ch in pattern:
        if ch == "C":
            result.append(random.choice(SOFT_CONS))
        else:
            result.append(random.choice(VOWELS))
    return "".join(result)


# ── Скоринг с учётом Fragment ─────────────────────────────────────────────────

def score_username(name: str) -> float:
    """
    Скоринг от 0 до 10.
    Учитывает:
    - Длину (4 буквы = Fragment-премиум бонус)
    - Чередование гласных/согласных
    - Наличие красивых/уродливых комбо
    - Окончание на гласную
    - Отсутствие стечений согласных
    """
    score = 5.0
    n = name.lower()
    length = len(n)

    # ── Бонус за длину (Fragment-логика) ──────────────────────────────────────
    if length == 4:
        score += 1.5   # 4-буквенные на Fragment = премиум
    elif length == 5:
        score += 0.5   # стандартный
    elif length <= 3:
        score += 2.0   # если вдруг попадётся — золото
    # 6+ букв = без бонуса

    # ── Стечения согласных (жёсткий штраф) ───────────────────────────────────
    cons_runs = re.findall(r"[bcdfghjklmnprstvwxyz]{2,}", n)
    for run in cons_runs:
        if len(run) >= 3:
            score -= 3.0   # 3+ согласных = нечитаемо
        elif len(run) == 2:
            score -= 0.8   # 2 согласных рядом — терпимо, но минус

    # ── Стечения гласных ──────────────────────────────────────────────────────
    vowel_runs = re.findall(r"[aeiou]{3,}", n)
    score -= len(vowel_runs) * 1.5

    # ── Чередование (ритмичность) ─────────────────────────────────────────────
    alternations = sum(
        1 for i in range(len(n) - 1)
        if (n[i] in "aeiou") != (n[i + 1] in "aeiou")
    )
    # Идеальное чередование: length-1 переходов
    ideal = length - 1
    if ideal > 0:
        rhythm_ratio = alternations / ideal
        score += rhythm_ratio * 1.5   # до +1.5 за идеальный ритм

    # ── Окончание на гласную (мелодичность) ───────────────────────────────────
    if n[-1] in "aeiou":
        score += 0.8
    # Окончание на -o, -a, -i особенно красиво
    if n[-1] in "oai":
        score += 0.3

    # ── Начало с гласной (Fragment часто ценит) ───────────────────────────────
    if n[0] in "aeiou":
        score += 0.3

    # ── Красивые комбо (бонус) ────────────────────────────────────────────────
    for combo in NICE_COMBOS:
        if combo in n:
            score += 0.4

    # ── Уродливые комбо (штраф) ───────────────────────────────────────────────
    for combo in UGLY_COMBOS:
        if combo in n:
            score -= 1.2

    # ── Повторяющиеся буквы ───────────────────────────────────────────────────
    if re.search(r"(.)\1", n):
        score -= 0.5

    return round(min(max(score, 0.0), 10.0), 1)


# ── Генерация кандидатов ──────────────────────────────────────────────────────

def generate_candidates(count: int = 300) -> List[Tuple[str, float]]:
    candidates: set = set()

    # 5-буквенные паттернные
    while len(candidates) < count * 2 // 3:
        candidates.add(generate_from_pattern(random.choice(PATTERNS_5)))

    # Случайные 5-буквенные (для охвата)
    while len(candidates) < count:
        candidates.add("".join(random.choices(string.ascii_lowercase, k=5)))

    scored = [(n, score_username(n)) for n in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ── Проверка Telegram ──────────────────────────────────────────────────────────

async def check_telegram(username: str, token: str) -> Optional[bool]:
    """True = свободен, False = занят, None = неизвестно"""
    url = f"https://api.telegram.org/bot{token}/getChat"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.post(url, json={"chat_id": f"@{username}"})
            data = r.json()
            logger.info("TG @%s -> ok=%s desc=%s", username, data.get("ok"), data.get("description", ""))
            if data.get("ok"):
                return False
            err = data.get("description", "").lower()
            if any(p in err for p in ["not found", "chat not found", "invalid username"]):
                return True
            if r.status_code == 429:
                await asyncio.sleep(int(r.headers.get("Retry-After", 3)))
            return None
    except Exception as e:
        logger.warning("TG check error: %s", e)
        return None


# ── Проверка Fragment ──────────────────────────────────────────────────────────

async def check_fragment(username: str) -> dict:
    """
    Возвращает {'status': 'free' | 'sold' | 'unknown', 'url': str}.
    free  — 404, ника нет на Fragment.
    sold  — любой другой ответ (продан или продаётся — неважно, занят).
    unknown — ошибка сети.
    """
    url = f"https://fragment.com/username/{username.lower()}"
    result = {"status": "unknown", "url": url}

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            r = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml"
            })

        # 404 — точно нет на Fragment, свободен
        if r.status_code == 404:
            result["status"] = "free"
            return result

        # Любой другой статус — ник существует на Fragment (продан или продаётся).
        # В обоих случаях не показываем — занят.
        result["status"] = "sold"
        return result


    except Exception as e:
        logger.warning("Fragment check error for @%s: %s", username, e)
        result["status"] = "unknown"
        return result


# ── Полная проверка ────────────────────────────────────────────────────────────

async def is_username_free(username: str, token: str) -> Tuple[bool, str]:
    """
    Возвращает (свободен: bool, статус: str).
    Свободен только если: TG не нашёл И Fragment вернул 404.
    """
    tg = await check_telegram(username, token)

    if tg is False:
        return False, "taken_tg"

    frag = await check_fragment(username)

    if frag["status"] == "free":
        return True, "free"

    if frag["status"] == "unknown":
        # Fragment недоступен — доверяем TG
        if tg is True:
            return True, "free"
        return False, "unknown"

    # sold или любой другой — занят
    return False, "taken"


# ── Форматирование результата ──────────────────────────────────────────────────

def build_result_text(found: List[tuple]) -> str:
    """found: list of (name, score)"""
    if not found:
        return "😔 Свободных не нашлось. Попробуй ещё раз!"

    lines = ["🎯 <b>Свободные юзернеймы:</b>\n"]
    for i, (name, score) in enumerate(found, 1):
        filled = round(score)
        bar    = "🟩" * filled + "⬜" * (10 - filled)
        lines.append(
            f"{i}. <code>@{name}</code> — {score}/10\n"
            f"   {bar}\n"
            f"   ✅ свободен\n"
            f'   <a href="https://t.me/{name}">👉 Открыть в Telegram</a>'
        )
    return "\n\n".join(lines)


async def run_search(
    token: str,
    pattern_filter: Optional[str] = None,
    progress_msg=None,
) -> List[tuple]:
    """
    Ищет только реально свободные 5-буквенные юзернеймы (status == 'free').
    Возвращает list of (name, score, fragment_url).
    """
    found: List[tuple] = []

    async def check_and_add(name: str, score: float):
        free, status = await is_username_free(name, token)
        if free:
            found.append((name, score))

    async def update_progress(idx: int, total: int):
        if progress_msg and idx % 5 == 0:
            try:
                await progress_msg.edit_text(
                    f"⏳ Проверено: {idx}/{total} | Найдено: {len(found)}/5"
                )
            except Exception:
                pass

    if pattern_filter is None:
        candidates = generate_candidates(300)
        top = candidates[:70]
        for idx, (name, score) in enumerate(top, 1):
            if len(found) >= 5:
                break
            await check_and_add(name, score)
            await update_progress(idx, 70)
            await asyncio.sleep(0.5)

    elif pattern_filter == "cvcvc":
        attempts = 0
        while len(found) < 5 and attempts < 120:
            attempts += 1
            name  = generate_from_pattern("CVCVC")
            score = score_username(name)
            if score < 5.0:
                continue
            await check_and_add(name, score)
            await asyncio.sleep(0.5)

    elif pattern_filter == "end_vowel":
        attempts = 0
        while len(found) < 5 and attempts < 120:
            attempts += 1
            pat  = random.choice(["CVCVV", "VCVCV", "CVCVC"])
            name = generate_from_pattern(pat)
            if name[-1] not in "aeiou":
                continue
            score = score_username(name)
            if score < 5.0:
                continue
            await check_and_add(name, score)
            await asyncio.sleep(0.5)

    return found


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 <b>Username Finder Bot</b>\n\n"
        "Ищу красивые свободные 5-буквенные юзернеймы.\n"
        "Проверяю в Telegram и на Fragment — показываю только <b>реально свободные</b>.\n\n"
        "Скоринг учитывает:\n"
        "• Ритмичность (чередование C/V)\n"
        "• Мелодичность слогов\n\n"
        "Жми кнопку 👇"
    )
    keyboard = [[InlineKeyboardButton("🔍 Найти юзернеймы", callback_data="search")]]
    await update.message.reply_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def search_usernames(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    msg = await query.message.reply_text("⏳ Проверяю Telegram + Fragment…")

    found = await run_search(context.bot.token, progress_msg=msg)
    result = build_result_text(found) if found else "😔 Не нашёл свободных. Попробуй ещё раз!"

    keyboard = [
        [InlineKeyboardButton("🔄 Ещё варианты",        callback_data="search")],
        [InlineKeyboardButton("🎲 Паттерн CVCVC",        callback_data="pattern_cvcvc")],
        [InlineKeyboardButton("✨ С гласной в конце",    callback_data="pattern_end_vowel")],
    ]
    try:
        await msg.edit_text(
            result, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error("edit_text error: %s", e)
        await query.message.reply_text(
            result, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )


async def search_pattern(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pattern_filter: str
) -> None:
    query = update.callback_query
    await query.answer()

    labels = {
        "cvcvc":     "CVCVC",
        "end_vowel": "гласная в конце",
    }
    msg = await query.message.reply_text(
        f"⏳ Ищу: {labels.get(pattern_filter, pattern_filter)}…"
    )

    found = await run_search(context.bot.token, pattern_filter=pattern_filter)
    result = build_result_text(found) if found else "😔 Не нашёл свободных. Попробуй ещё раз!"

    keyboard = [
        [InlineKeyboardButton("🔄 Ещё", callback_data=f"pattern_{pattern_filter}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="search")],
    ]
    try:
        await msg.edit_text(
            result, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error("edit_text error: %s", e)
        await query.message.reply_text(
            result, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = update.callback_query.data
    if data == "search":
        await search_usernames(update, context)
    elif data.startswith("pattern_"):
        pf = data[len("pattern_"):]
        await search_pattern(update, context, pf)
    else:
        await update.callback_query.answer("Неизвестная команда")


def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан! Railway: добавь в Variables -> BOT_TOKEN")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
