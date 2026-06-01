import os
import json
import threading
import time
import socket
import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import feedparser
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import NetworkError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from main import (
    CATEGORIES,
    KEYWORDS_FILE,
    SOURCES_FILE,
    collect_news,
    count_news_by_category,
    read_lines_from_file,
    read_settings,
    sort_news_by_importance,
)


NEWS_LIMIT = 5
BOT_VERSION = "1.1.0"
ALERT_INTERVAL_MINUTES = 30
ANTI_SPAM_SECONDS = 10
MAX_TELEGRAM_MESSAGE_LENGTH = 4000
ADMINS_FILE = "admins.txt"
SUBSCRIBERS_FILE = "subscribers.txt"
SENT_NEWS_FILE = "sent_news.txt"
TELEGRAM_LOGS_FILE = "telegram_logs.txt"
BOT_STATS_FILE = "bot_stats.txt"
DEFAULT_ADMIN_IDS = [730977304]
AUTOPOST_FILE = "autopost_channel.txt"
NEWS_CACHE_TTL_SECONDS = int(os.getenv("NEWS_CACHE_TTL_SECONDS", "300"))
NEWS_CACHE_REFRESH_SECONDS = int(os.getenv("NEWS_CACHE_REFRESH_SECONDS", "300"))
RSS_TIMEOUT_SECONDS = int(os.getenv("RSS_TIMEOUT_SECONDS", "10"))
NEWS_CACHE = {
    "news": None,
    "failed_sources": [],
    "updated_at": 0,
}
socket.setdefaulttimeout(RSS_TIMEOUT_SECONDS)
user_last_request_time = {}
pending_clearlogs_users = set()
pending_clearstats_users = set()
pending_broadcasts = {}
PENDING_CHANNEL_POSTS = {}
DEFAULT_BOT_STATS = {
    "/news": 0,
    "/important": 0,
    "/summary": 0,
    "/ping": 0,
    "Підписок": 0,
    "Відписок": 0,
}
NEWS_BUTTON = "📰 Новини"
IMPORTANT_BUTTON = "🚨 Важливі"
SUMMARY_BUTTON = "📊 Зведення"
HELP_BUTTON = "ℹ️ Допомога"
AI_ANALYZE_BUTTON = "🧠 AI-аналіз"


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OSINT News Bot is running")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = os.getenv("PORT")

    if not port:
        return

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), HealthCheckHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"Health server started on port {port}")


def get_main_keyboard():
    keyboard = [
        [NEWS_BUTTON, IMPORTANT_BUTTON],
        [SUMMARY_BUTTON, AI_ANALYZE_BUTTON],
        [HELP_BUTTON],
    ]

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_bot_token():
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if telegram_token:
        return telegram_token

    if not os.path.exists(".env"):
        print("Помилка: файл .env не знайдено")
        return None

    load_dotenv()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not telegram_token:
        print("Помилка: TELEGRAM_BOT_TOKEN не вказано")
        return None

    return telegram_token


def ensure_admins_file():
    if not os.path.exists(ADMINS_FILE):
        with open(ADMINS_FILE, "w", encoding="utf-8"):
            pass


def get_admin_ids():
    ensure_admins_file()

    admin_ids = set(DEFAULT_ADMIN_IDS)
    env_admin_ids = os.getenv("ADMIN_IDS", "")

    for admin_id in env_admin_ids.split(","):
        admin_id = admin_id.strip()

        if admin_id.isdigit():
            admin_ids.add(int(admin_id))

    with open(ADMINS_FILE, "r", encoding="utf-8") as file:
        for line in file:
            admin_id = line.strip()

            if admin_id.isdigit():
                admin_ids.add(int(admin_id))

    return sorted(admin_ids)


def is_admin(user_id):
    return user_id in get_admin_ids()


def make_first_admin_if_needed(user_id):
    admin_ids = get_admin_ids()

    if admin_ids:
        return False

    with open(ADMINS_FILE, "w", encoding="utf-8") as file:
        file.write(f"{user_id}\n")

    return True


async def deny_if_not_admin(update: Update):
    if is_admin(update.effective_user.id):
        return False

    await update.message.reply_text("У вас немає доступу до цієї команди.")
    return True


async def is_spam_request(update: Update):
    user_id = update.effective_user.id
    current_time = time.time()
    last_request_time = user_last_request_time.get(user_id, 0)

    if current_time - last_request_time < ANTI_SPAM_SECONDS:
        await update.message.reply_text("Зачекайте кілька секунд перед наступним запитом.")
        return True

    user_last_request_time[user_id] = current_time
    return False


def read_set_from_file(file_name):
    if not os.path.exists(file_name):
        return set()

    with open(file_name, "r", encoding="utf-8") as file:
        items = set()

        for line in file:
            text = line.strip()

            if text:
                items.add(text)

    return items


def write_set_to_file(file_name, items):
    with open(file_name, "w", encoding="utf-8") as file:
        for item in sorted(items):
            file.write(f"{item}\n")


def log_user_command(update: Update, command):
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    user = update.effective_user
    username = user.username or "unknown_user"
    user_id = user.id

    with open(TELEGRAM_LOGS_FILE, "a", encoding="utf-8") as file:
        file.write(f"[{current_time}]\n")
        file.write(f"User: {username}\n")
        file.write(f"ID: {user_id}\n")
        file.write(f"Command: {command}\n\n")


def create_default_bot_stats():
    write_bot_stats(DEFAULT_BOT_STATS.copy())


def read_bot_stats():
    if not os.path.exists(BOT_STATS_FILE):
        create_default_bot_stats()

    stats = DEFAULT_BOT_STATS.copy()

    with open(BOT_STATS_FILE, "r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()

            if not text or "=" not in text:
                continue

            key, value = text.split("=", 1)

            try:
                stats[key.strip()] = int(value.strip())
            except ValueError:
                stats[key.strip()] = 0

    return stats


def write_bot_stats(stats):
    with open(BOT_STATS_FILE, "w", encoding="utf-8") as file:
        for key in DEFAULT_BOT_STATS:
            file.write(f"{key}={stats.get(key, 0)}\n")


def increment_bot_stat(key):
    stats = read_bot_stats()
    stats[key] = stats.get(key, 0) + 1
    write_bot_stats(stats)


def count_lines_in_file(file_name):
    if not os.path.exists(file_name):
        return 0

    with open(file_name, "r", encoding="utf-8") as file:
        count = 0

        for line in file:
            if line.strip():
                count += 1

    return count


def get_news_limit():
    try:
        settings = read_settings()
        news_limit = int(settings.get("news_limit", NEWS_LIMIT))

        if news_limit > 0:
            return news_limit
    except Exception:
        pass

    return NEWS_LIMIT


def get_channel_id():
    return os.getenv("CHANNEL_ID", "").strip()


def is_channel_autopost_enabled():
    env_value = os.getenv("AUTOPOST_CHANNEL", "").strip().lower()

    if env_value in ["true", "1", "yes", "on"]:
        return True

    if not os.path.exists(AUTOPOST_FILE):
        return False

    with open(AUTOPOST_FILE, "r", encoding="utf-8") as file:
        return file.read().strip().lower() == "true"


def set_channel_autopost(enabled):
    with open(AUTOPOST_FILE, "w", encoding="utf-8") as file:
        file.write("true" if enabled else "false")


def add_subscriber(chat_id):
    subscribers = read_set_from_file(SUBSCRIBERS_FILE)
    chat_id = str(chat_id)

    if chat_id in subscribers:
        return False

    subscribers.add(chat_id)
    write_set_to_file(SUBSCRIBERS_FILE, subscribers)
    return True


def remove_subscriber(chat_id):
    subscribers = read_set_from_file(SUBSCRIBERS_FILE)
    chat_id = str(chat_id)

    if chat_id not in subscribers:
        return False

    subscribers.remove(chat_id)
    write_set_to_file(SUBSCRIBERS_FILE, subscribers)
    return True


def fetch_monitored_news():
    keywords = read_lines_from_file(KEYWORDS_FILE, "ключові слова")
    sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")

    if not keywords or not sources:
        return [], ["Не вдалося прочитати keywords.txt або sources.txt"]

    found_news, failed_sources = collect_news(sources, keywords)
    found_news = sort_news_by_importance(found_news)

    return found_news, failed_sources


def refresh_news_cache():
    found_news, failed_sources = fetch_monitored_news()
    NEWS_CACHE["news"] = found_news
    NEWS_CACHE["failed_sources"] = failed_sources
    NEWS_CACHE["updated_at"] = time.time()
    return found_news, failed_sources


def get_monitored_news(force_refresh=False):
    cached_news = NEWS_CACHE["news"]

    if not force_refresh and cached_news is not None:
        return cached_news, NEWS_CACHE["failed_sources"]

    return refresh_news_cache()


async def refresh_news_cache_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await asyncio.to_thread(refresh_news_cache)
        print("RSS cache updated")
    except Exception as error:
        print(f"RSS cache update error: {error}")


def format_news_item(number, news_item):
    return (
        f"{number}. 📰 Назва:\n"
        f"{news_item['title']}\n\n"
        f"📅 Дата:\n"
        f"{news_item['published_date']}\n\n"
        f"🏷 Категорія:\n"
        f"{news_item['category']}\n\n"
        f"🚨 Важливість:\n"
        f"{news_item['importance']}\n\n"
        f"🔗 Посилання:\n"
        f"{news_item['link']}"
    )


def format_news_message(title, news_items, empty_message="Новин не знайдено"):
    if not news_items:
        return f"{title}\n\n{empty_message}"

    news_blocks = []

    for number, news_item in enumerate(news_items, start=1):
        news_blocks.append(format_news_item(number, news_item))

    return title + "\n\n" + "\n\n────────────\n\n".join(news_blocks)


def format_channel_news_item(news_item):
    return (
        f"📰 {news_item['title']}\n\n"
        f"Дата: {news_item['published_date']}\n"
        f"Категорія: {news_item['category']}\n"
        f"Важливість: {news_item['importance']}\n"
        f"Посилання: {news_item['link']}"
    )


def build_channel_post_text(news_item):
    conclusion = (
        "Новина може бути важливою для інформаційного моніторингу, "
        f"оскільки містить ключове слово: {news_item.get('keyword', 'невідомо')}."
    )
    return (
        f"🟠 {news_item['title']}\n\n"
        f"↪️ {conclusion}\n\n"
        f"🏷 Категорія: {news_item['category']}\n"
        f"🚨 Важливість: {news_item['importance']}\n"
        f"📡 Джерело: {news_item['source']}\n\n"
        f"🏆 OSINT News Bot\n"
        f"🔗 {news_item['link']}"
    )


def remember_news_for_channel_post(user_id, news_items):
    PENDING_CHANNEL_POSTS[user_id] = {}
    keyboard = []

    for index, news_item in enumerate(news_items[:5], start=1):
        post_id = str(index)
        PENDING_CHANNEL_POSTS[user_id][post_id] = news_item
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"Опублікувати новину {index}",
                    callback_data=f"post_news:{post_id}",
                )
            ]
        )

    return InlineKeyboardMarkup(keyboard)


async def send_news_to_channel(bot, channel_id, news_item):
    image_url = news_item.get("image_url", "")
    caption = build_channel_post_text(news_item)

    if not image_url:
        raise ValueError("У новини немає картинки в RSS.")

    await bot.send_photo(chat_id=channel_id, photo=image_url, caption=caption[:1024])


def analyze_news_with_llm(news_item):
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()

    if not api_key:
        return "LLM-аналіз вимкнено: не вказано OPENROUTER_API_KEY."

    model = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip()
    prompt = (
        "Проаналізуй OSINT-новину українською мовою. "
        "Дай короткий висновок у 3 пунктах: суть, ризик, чому це важливо. "
        "Не вигадуй фактів поза заголовком.\n\n"
        f"Назва: {news_item['title']}\n"
        f"Категорія: {news_item['category']}\n"
        f"Важливість: {news_item['importance']}\n"
        f"Посилання: {news_item['link']}"
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Ти OSINT-аналітик. Пиши стисло, обережно і без вигаданих фактів.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "max_tokens": 250,
    }
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Kuzhel678/Osint-1488",
            "X-Title": "OSINT News Bot",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        return f"LLM-аналіз недоступний: {error}"

    choices = data.get("choices", [])

    if choices:
        message = choices[0].get("message", {})
        content = message.get("content", "")

        if content:
            return content.strip()

    return "LLM-аналіз не повернув текст."


def split_long_message(message):
    parts = []
    current_part = ""
    separator = "\n\n────────────\n\n"

    for block in message.split(separator):
        if len(block) > MAX_TELEGRAM_MESSAGE_LENGTH:
            if current_part:
                parts.append(current_part)
                current_part = ""

            for start in range(0, len(block), MAX_TELEGRAM_MESSAGE_LENGTH):
                parts.append(block[start : start + MAX_TELEGRAM_MESSAGE_LENGTH])

            continue

        if not current_part:
            current_part = block
        elif len(current_part) + len(separator) + len(block) <= MAX_TELEGRAM_MESSAGE_LENGTH:
            current_part += separator + block
        else:
            parts.append(current_part)
            current_part = block

    if current_part:
        parts.append(current_part)

    return parts


async def reply_long_message(update, message, reply_markup=None):
    message_parts = split_long_message(message)

    for index, message_part in enumerate(message_parts):
        if index == 0:
            await update.message.reply_text(message_part, reply_markup=reply_markup)
        else:
            await update.message.reply_text(message_part)


async def send_long_message(bot, chat_id, message):
    message_parts = split_long_message(message)

    for message_part in message_parts:
        await bot.send_message(chat_id=chat_id, text=message_part)


def get_news_key(news_item):
    if news_item["link"] != "Посилання відсутнє":
        return news_item["link"]

    return news_item["title"]


def format_operational_summary(news_items):
    important_news = []

    for news_item in news_items:
        if news_item["importance"] in ["ВИСОКА", "СЕРЕДНЯ"]:
            important_news.append(news_item)

    if not important_news:
        return "Недостатньо даних для формування зведення."

    statistics = count_news_by_category(important_news)
    max_category = CATEGORIES[0]

    for category in CATEGORIES:
        if statistics[category] > statistics[max_category]:
            max_category = category

    lines = ["=== Оперативна зведена довідка ==="]

    for category in CATEGORIES:
        lines.append(f"\n{category}:")
        category_news = []

        for news_item in important_news:
            if news_item["category"] == category:
                category_news.append(news_item)

        if category_news:
            for news_item in category_news[:get_news_limit()]:
                lines.append(f"- {news_item['title']}")
        else:
            lines.append("- немає новин")

    lines.append("\nКороткий висновок:")
    lines.append(f"Найбільше уваги зараз потребує категорія: {max_category}")

    return "\n".join(lines)


def get_help_text():
    return (
        "=== OSINT News Bot — Допомога ===\n\n"
        "/start — запустити бота\n"
        "/news — показати останні новини\n"
        "/important — показати важливі новини\n"
        "/summary — оперативна зведена довідка\n"
        "/subscribe — підписатися на сповіщення\n"
        "/unsubscribe — відписатися від сповіщень\n"
        "/status — статус системи\n"
        "/ping — перевірити роботу бота\n"
        "/botstats — статистика використання бота\n"
        "/settings — показати налаштування бота\n"
        "/sources — показати RSS-джерела\n"
        "/checkrss — перевірка RSS-джерел\n"
        "/keywords — показати ключові слова\n"
        "/restartinfo — інструкція з перезапуску бота\n"
        "/version — версія Telegram-бота\n"
        "/about — інформація про OSINT News Bot\n"
        "/myid — показати ваш Telegram ID\n"
        "/broadcast текст повідомлення — розсилка підписникам\n"
        "/health — перевірка системи\n"
        "/logs — показати останні логи\n"
        "/clearlogs — очистити логи Telegram-бота\n"
        "/clearstats — очистити статистику Telegram-бота\n"
        "/admins — список адміністраторів\n"
        "/adminhelp — службові команди\n"
        "/lastalerts — останні сповіщення\n"
        "/aianalyze — AI-аналіз останньої важливої новини\n"
        "/help — допомога"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/start")
    add_subscriber(update.effective_chat.id)
    is_first_admin = make_first_admin_if_needed(update.effective_user.id)
    message = (
        "OSINT News Bot активовано\n\n"
        "Основні команди:\n"
        "/news — останні новини\n"
        "/important — важливі новини\n"
        "/summary — оперативна зведена довідка\n"
        "/help — допомога"
    )

    if is_first_admin:
        message += "\n\nВас призначено першим адміністратором системи."

    await reply_long_message(update, message, reply_markup=get_main_keyboard())


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/news")
    increment_bot_stat("/news")

    try:
        if await is_spam_request(update):
            return

        found_news, failed_sources = get_monitored_news()
        message = format_news_message(
            "📰 Перші 5 новин",
            found_news[:get_news_limit()],
            "Новин за заданими ключовими словами не знайдено.",
        )

        if failed_sources:
            message += f"\n\n⚠️ Помилки RSS-джерел: {len(failed_sources)}"

        await reply_long_message(update, message)

        if is_admin(update.effective_user.id) and found_news:
            publish_news = found_news[:5]
            keyboard = remember_news_for_channel_post(update.effective_user.id, publish_news)
            await update.message.reply_text(
                "Оберіть новину для публікації в канал:",
                reply_markup=keyboard,
            )
    except Exception as error:
        print(f"Помилка /news: {error}")
        await update.message.reply_text("Сталася помилка. Перевірте налаштування бота.")


async def important_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/important")
    increment_bot_stat("/important")

    try:
        if await is_spam_request(update):
            return

        found_news, failed_sources = get_monitored_news()
        important_news = []

        for news_item in found_news:
            if news_item["importance"] == "ВИСОКА":
                important_news.append(news_item)

        message = format_news_message(
            "🔥 Важливі новини",
            important_news[:get_news_limit()],
            "Важливих новин зараз не знайдено.",
        )

        if failed_sources:
            message += f"\n\n⚠️ Помилки RSS-джерел: {len(failed_sources)}"

        await reply_long_message(update, message)
    except Exception as error:
        print(f"Помилка /important: {error}")
        await update.message.reply_text("Сталася помилка. Перевірте налаштування бота.")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/summary")
    increment_bot_stat("/summary")

    try:
        if await is_spam_request(update):
            return

        found_news, failed_sources = get_monitored_news()
        message = format_operational_summary(found_news)

        if failed_sources:
            message += f"\n\n⚠️ Помилки RSS-джерел: {len(failed_sources)}"

        await reply_long_message(update, message)
    except Exception as error:
        print(f"Помилка /summary: {error}")
        await update.message.reply_text("Сталася помилка. Перевірте налаштування бота.")


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/subscribe")
    was_added = add_subscriber(update.effective_chat.id)

    if was_added:
        increment_bot_stat("Підписок")
        await update.message.reply_text("Ви підписані на OSINT-сповіщення")
    else:
        await update.message.reply_text("Ви вже підписані на OSINT-сповіщення.")


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/unsubscribe")
    was_removed = remove_subscriber(update.effective_chat.id)

    if was_removed:
        increment_bot_stat("Відписок")
        await update.message.reply_text("Ви відписані від OSINT-сповіщень")
    else:
        await update.message.reply_text("Ви не були підписані.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/status")
    subscribers_count = count_lines_in_file(SUBSCRIBERS_FILE)
    sent_news_count = count_lines_in_file(SENT_NEWS_FILE)
    sources_count = count_lines_in_file(SOURCES_FILE)
    keywords_count = count_lines_in_file(KEYWORDS_FILE)

    message = (
        "=== Статус OSINT Bot ===\n"
        f"Підписників: {subscribers_count}\n"
        f"Інтервал перевірки: {ALERT_INTERVAL_MINUTES} хв\n"
        f"Надісланих новин: {sent_news_count}\n"
        f"RSS-джерел: {sources_count}\n"
        f"Ключових слів: {keywords_count}"
    )

    await reply_long_message(update, message)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/ping")
    increment_bot_stat("/ping")
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    message = (
        "✅ Бот працює\n"
        f"Час перевірки: {current_time}"
    )

    await update.message.reply_text(message)


async def botstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/botstats")
    stats = read_bot_stats()
    message = (
        "=== Статистика бота ===\n"
        f"/news: {stats['/news']}\n"
        f"/important: {stats['/important']}\n"
        f"/summary: {stats['/summary']}\n"
        f"/ping: {stats['/ping']}\n"
        f"Підписок: {stats['Підписок']}\n"
        f"Відписок: {stats['Відписок']}"
    )

    await update.message.reply_text(message)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/settings")

    if os.path.exists("settings.txt"):
        settings = read_settings()
        news_limit = settings["news_limit"]
        auto_interval = settings["auto_interval"]
        show_only_important = settings["show_only_important"]
    else:
        news_limit = "невідомо"
        auto_interval = "невідомо"
        show_only_important = "невідомо"

    sources_count = count_lines_in_file(SOURCES_FILE)
    keywords_count = count_lines_in_file(KEYWORDS_FILE)

    message = (
        "=== Налаштування OSINT Bot ===\n"
        f"Ліміт новин: {news_limit}\n"
        f"Інтервал перевірки: {auto_interval} хв\n"
        f"Тільки важливі: {show_only_important}\n"
        f"RSS-джерел: {sources_count}\n"
        f"Ключових слів: {keywords_count}"
    )

    await update.message.reply_text(message)


async def sources_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/sources")
    sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")

    if not sources:
        await update.message.reply_text("RSS-джерела не знайдено.")
        return

    lines = ["=== RSS-джерела ==="]

    for number, source in enumerate(sources, start=1):
        lines.append(f"{number}. {source}")

    await reply_long_message(update, "\n".join(lines))


async def checkrss_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/checkrss")
    sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")

    if not sources:
        await update.message.reply_text("RSS-джерела не знайдено.")
        return

    working_count = 0
    error_count = 0
    lines = ["=== Перевірка RSS ==="]

    for source in sources:
        try:
            news_feed = feedparser.parse(source)

            if news_feed.entries:
                working_count += 1
                lines.append(f"✅ Працює: {source}")
            else:
                error_count += 1
                lines.append(f"❌ Помилка: {source}")
        except Exception:
            error_count += 1
            lines.append(f"❌ Помилка: {source}")

    lines.append("")
    lines.append(f"Перевірено джерел: {len(sources)}")
    lines.append(f"Працює: {working_count}")
    lines.append(f"Помилок: {error_count}")

    await reply_long_message(update, "\n".join(lines))


async def keywords_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/keywords")
    keywords = read_lines_from_file(KEYWORDS_FILE, "ключові слова")

    if not keywords:
        await update.message.reply_text("Ключові слова не знайдено.")
        return

    lines = ["=== Ключові слова ==="]

    for number, keyword in enumerate(keywords, start=1):
        lines.append(f"{number}. {keyword}")

    await reply_long_message(update, "\n".join(lines))


async def restartinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/restartinfo")
    message = (
        "=== Перезапуск бота ===\n\n"
        "1. Відкрити PowerShell у папці проєкту\n"
        "2. Зупинити бота: CTRL + C\n"
        "3. Запустити знову:\n"
        "python telegram_bot.py\n\n"
        "Якщо бот не запускається:\n"
        "pip install python-dotenv\n"
        "pip install feedparser\n"
        "pip install \"python-telegram-bot[job-queue]\""
    )

    await update.message.reply_text(message)


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/version")
    message = (
        "=== OSINT News Bot ===\n"
        f"Версія: {BOT_VERSION}\n"
        "Статус: стабільна Telegram-версія\n"
        "Функції:\n"
        "- RSS-моніторинг\n"
        "- Telegram-команди\n"
        "- Автосповіщення\n"
        "- Статистика\n"
        "- Логи\n"
        "- Антиспам\n"
        "- Налаштування"
    )

    await update.message.reply_text(message)


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/about")
    message = (
        "=== Про OSINT News Bot ===\n\n"
        "OSINT News Bot — це Telegram-бот для моніторингу новин через RSS-джерела.\n\n"
        "Основні можливості:\n"
        "- збір новин із RSS\n"
        "- фільтрація за ключовими словами\n"
        "- визначення категорії новини\n"
        "- оцінка важливості\n"
        "- оперативна зведена довідка\n"
        "- автоматичні сповіщення\n"
        "- логування команд\n"
        "- статистика використання\n\n"
        "Призначення:\n"
        "допомога в інформаційному моніторингу та швидкому перегляді важливих новин."
    )

    await update.message.reply_text(message)


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/myid")
    user = update.effective_user
    username = user.username or "unknown_user"
    message = (
        f"Ваш Telegram ID: {user.id}\n"
        f"Username: {username}"
    )

    await update.message.reply_text(message)


async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_ids = get_admin_ids()

    if not admin_ids:
        await update.message.reply_text("Список адміністраторів порожній.")
        return

    if await deny_if_not_admin(update):
        return

    log_user_command(update, "/admins")
    lines = ["=== Адміністратори ==="]

    for index, admin_id in enumerate(admin_ids, start=1):
        lines.append(f"{index}. {admin_id}")

    await update.message.reply_text("\n".join(lines))


async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Використання: /addadmin 123456789")
        return

    new_admin_id = int(context.args[0])
    admin_ids = get_admin_ids()

    if new_admin_id in admin_ids:
        await update.message.reply_text("Цей користувач вже є адміністратором.")
        return

    with open(ADMINS_FILE, "a", encoding="utf-8") as file:
        file.write(f"{new_admin_id}\n")

    await update.message.reply_text("Адміністратора додано.")


async def removeadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Використання: /removeadmin 123456789")
        return

    admin_id_to_remove = int(context.args[0])
    admin_ids = get_admin_ids()

    if admin_id_to_remove == update.effective_user.id:
        await update.message.reply_text("Ви не можете видалити самого себе з адміністраторів.")
        return

    if admin_id_to_remove not in admin_ids:
        await update.message.reply_text("Цього адміністратора не знайдено.")
        return

    if len(admin_ids) == 1:
        await update.message.reply_text("Неможливо видалити останнього адміністратора.")
        return

    admin_ids.remove(admin_id_to_remove)

    with open(ADMINS_FILE, "w", encoding="utf-8") as file:
        for admin_id in admin_ids:
            file.write(f"{admin_id}\n")

    await update.message.reply_text("Адміністратора видалено.")


async def adminpanel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    message = (
        "=== Admin Panel ===\n"
        "/adminhelp — службові команди\n"
        "/admins — список адміністраторів\n"
        "/addadmin <id> — додати адміністратора\n"
        "/removeadmin <id> — видалити адміністратора\n"
        "/logs — останні логи\n"
        "/clearlogs — очистити логи\n"
        "/botstats — статистика\n"
        "/clearstats — очистити статистику\n"
        "/health — перевірка системи\n"
        "/restartinfo — інструкція перезапуску"
    )

    message += "\n/testalert — тестове сповіщення"
    message += "\n/checkrss — перевірка RSS-джерел"
    message += "\n/channelstatus — перевірка каналу"
    message += "\n/postimportant — опублікувати важливі новини в канал"
    message += "\n/postsummary — опублікувати зведення в канал"
    message += "\n/autopost_on — увімкнути автопостинг"
    message += "\n/autopost_off — вимкнути автопостинг"
    message += "\n/aianalyze — AI-аналіз новини"
    message += "\n/lastalerts — останні сповіщення"

    await update.message.reply_text(message)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Використання: /broadcast текст повідомлення")
        return

    subscribers = read_set_from_file(SUBSCRIBERS_FILE)

    if not subscribers:
        await update.message.reply_text("Підписників немає.")
        return

    broadcast_text = " ".join(context.args)
    pending_broadcasts[update.effective_user.id] = broadcast_text
    await update.message.reply_text(
        "Підтвердити розсилку? Напишіть YES для підтвердження або NO для скасування."
    )


async def handle_broadcast_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in pending_broadcasts:
        return False

    if await deny_if_not_admin(update):
        pending_broadcasts.pop(user_id, None)
        return True

    broadcast_text = pending_broadcasts.pop(user_id)

    if update.message.text.strip().upper() != "YES":
        await update.message.reply_text("Розсилку скасовано.")
        return True

    subscribers = read_set_from_file(SUBSCRIBERS_FILE)

    if not subscribers:
        await update.message.reply_text("Підписників немає.")
        return True

    sent_count = 0

    for chat_id in subscribers:
        try:
            await context.bot.send_message(chat_id=chat_id, text=broadcast_text)
            sent_count += 1
        except Exception:
            continue

    await update.message.reply_text(f"Розсилку завершено. Надіслано: {sent_count}")
    return True


async def testalert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    subscribers = read_set_from_file(SUBSCRIBERS_FILE)

    if not subscribers:
        await update.message.reply_text("Підписників немає.")
        return

    message = (
        "🚨 Тестове OSINT-сповіщення\n\n"
        "Система автоматичних сповіщень працює коректно."
    )
    sent_count = 0

    for chat_id in subscribers:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
            sent_count += 1
        except Exception:
            continue

    await update.message.reply_text(f"Тестове сповіщення надіслано. Отримувачів: {sent_count}")


async def channelstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    channel_id = get_channel_id()

    if not channel_id:
        await update.message.reply_text("CHANNEL_ID не вказано. Додайте його в Render Environment Variables.")
        return

    message = (
        "=== Channel Status ===\n"
        f"Канал: {channel_id}\n"
        f"Автопостинг: {is_channel_autopost_enabled()}"
    )

    try:
        await context.bot.send_message(chat_id=channel_id, text="✅ OSINT News Bot підключено до каналу.")
        message += "\nСтатус: бот може писати в канал."
    except Exception as error:
        message += f"\nСтатус: помилка відправки в канал: {error}"

    await update.message.reply_text(message)


async def postimportant_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    channel_id = get_channel_id()

    if not channel_id:
        await update.message.reply_text("CHANNEL_ID не вказано.")
        return

    found_news, failed_sources = get_monitored_news()
    important_news = []

    for news_item in found_news:
        if news_item["importance"] == "ВИСОКА":
            important_news.append(news_item)

    if not important_news:
        await update.message.reply_text("Важливих новин зараз не знайдено.")
        return

    message = format_news_message("🚨 Важливі OSINT-новини", important_news[:get_news_limit()])

    if failed_sources:
        message += f"\n\nRSS-помилки: {len(failed_sources)}"

    await send_long_message(context.bot, channel_id, message)
    await update.message.reply_text("Важливі новини опубліковано в канал.")


async def postsummary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    channel_id = get_channel_id()

    if not channel_id:
        await update.message.reply_text("CHANNEL_ID не вказано.")
        return

    found_news, failed_sources = get_monitored_news()
    summary = format_operational_summary(found_news)

    if failed_sources:
        summary += f"\n\nRSS-помилки: {len(failed_sources)}"

    await send_long_message(context.bot, channel_id, summary)
    await update.message.reply_text("Оперативну зведену довідку опубліковано в канал.")


async def autopost_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    if not get_channel_id():
        await update.message.reply_text("CHANNEL_ID не вказано.")
        return

    set_channel_autopost(True)
    await update.message.reply_text("Автопостинг у канал увімкнено.")


async def autopost_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    set_channel_autopost(False)
    await update.message.reply_text("Автопостинг у канал вимкнено.")


async def aianalyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    found_news, failed_sources = get_monitored_news()

    if not found_news:
        await update.message.reply_text("Новин для AI-аналізу не знайдено.")
        return

    news_item = found_news[0]
    await update.message.reply_text("AI-аналіз запущено. Зачекайте кілька секунд.")
    analysis = await asyncio.to_thread(analyze_news_with_llm, news_item)
    message = (
        "=== AI OSINT-аналіз ===\n\n"
        f"{format_channel_news_item(news_item)}\n\n"
        f"Висновок:\n{analysis}"
    )

    if failed_sources:
        message += f"\n\nRSS-помилки: {len(failed_sources)}"

    await reply_long_message(update, message)


async def lastalerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not os.path.exists(SENT_NEWS_FILE) or os.path.getsize(SENT_NEWS_FILE) == 0:
        await update.message.reply_text("Надісланих сповіщень ще немає.")
        return

    with open(SENT_NEWS_FILE, "r", encoding="utf-8") as file:
        alerts = []

        for line in file:
            alert = line.strip()

            if alert:
                alerts.append(alert)

    if not alerts:
        await update.message.reply_text("Надісланих сповіщень ще немає.")
        return

    lines = ["=== Останні сповіщення ==="]

    for index, alert in enumerate(alerts[-10:], start=1):
        lines.append(f"{index}. {alert}")

    await reply_long_message(update, "\n".join(lines))


async def adminhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    log_user_command(update, "/adminhelp")
    message = (
        "=== Admin Help ===\n\n"
        "/status — статус бота\n"
        "/health — перевірка системи\n"
        "/logs — останні логи\n"
        "/clearlogs — очистити логи\n"
        "/botstats — статистика бота\n"
        "/clearstats — очистити статистику\n"
        "/settings — налаштування\n"
        "/sources — RSS-джерела\n"
        "/keywords — ключові слова\n"
        "/restartinfo — інструкція перезапуску\n"
        "/version — версія бота\n"
        "/admins — список адміністраторів\n"
        "/addadmin 123456789 — додати адміністратора\n"
        "/removeadmin 123456789 — видалити адміністратора\n"
        "/adminpanel — панель адміністратора\n"
        "/broadcast текст повідомлення — розсилка підписникам"
    )

    message += "\n/testalert — тестове сповіщення"
    message += "\n/checkrss — перевірка RSS-джерел"
    message += "\n/channelstatus — перевірка каналу"
    message += "\n/postimportant — опублікувати важливі новини в канал"
    message += "\n/postsummary — опублікувати зведення в канал"
    message += "\n/autopost_on — увімкнути автопостинг"
    message += "\n/autopost_off — вимкнути автопостинг"
    message += "\n/aianalyze — AI-аналіз новини"
    message += "\n/lastalerts — останні сповіщення"

    await update.message.reply_text(message)


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/health")
    required_files = [
        ".env",
        KEYWORDS_FILE,
        SOURCES_FILE,
        "settings.txt",
        ADMINS_FILE,
        SUBSCRIBERS_FILE,
        SENT_NEWS_FILE,
    ]
    all_files_exist = True
    lines = ["=== Перевірка системи ==="]

    for file_name in required_files:
        if os.path.exists(file_name):
            lines.append(f"✅ {file_name}")
        else:
            lines.append(f"❌ {file_name}")
            all_files_exist = False

    lines.append("")

    if all_files_exist:
        lines.append("Система працює стабільно.")
    else:
        lines.append("Виявлено проблеми у конфігурації.")

    await update.message.reply_text("\n".join(lines))


async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    log_user_command(update, "/logs")

    if not os.path.exists(TELEGRAM_LOGS_FILE) or os.path.getsize(TELEGRAM_LOGS_FILE) == 0:
        await update.message.reply_text("Логи не знайдено.")
        return

    with open(TELEGRAM_LOGS_FILE, "r", encoding="utf-8") as file:
        log_text = file.read().strip()

    if not log_text:
        await update.message.reply_text("Логи не знайдено.")
        return

    log_entries = []

    for entry in log_text.split("\n\n"):
        entry = entry.strip()

        if entry:
            log_entries.append(entry)

    last_entries = log_entries[-10:]
    message = "=== Останні логи ===\n" + "\n\n".join(last_entries)

    await reply_long_message(update, message)


async def clearlogs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    log_user_command(update, "/clearlogs")
    pending_clearlogs_users.add(update.effective_user.id)
    await update.message.reply_text("Ви впевнені? Напишіть YES для підтвердження.")


async def handle_clearlogs_confirmation(update: Update):
    user_id = update.effective_user.id

    if user_id not in pending_clearlogs_users:
        return False

    if await deny_if_not_admin(update):
        pending_clearlogs_users.discard(user_id)
        return True

    pending_clearlogs_users.remove(user_id)

    if update.message.text.strip() == "YES":
        with open(TELEGRAM_LOGS_FILE, "w", encoding="utf-8"):
            pass

        await update.message.reply_text("Логи очищено.")
    else:
        await update.message.reply_text("Очищення логів скасовано.")

    return True


async def clearstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_not_admin(update):
        return

    log_user_command(update, "/clearstats")
    pending_clearstats_users.add(update.effective_user.id)
    await update.message.reply_text("Ви впевнені? Напишіть YES для підтвердження.")


async def handle_clearstats_confirmation(update: Update):
    user_id = update.effective_user.id

    if user_id not in pending_clearstats_users:
        return False

    if await deny_if_not_admin(update):
        pending_clearstats_users.discard(user_id)
        return True

    pending_clearstats_users.remove(user_id)

    if update.message.text.strip() == "YES":
        create_default_bot_stats()
        await update.message.reply_text("Статистику очищено.")
    else:
        await update.message.reply_text("Очищення статистики скасовано.")

    return True


async def send_alerts(context: ContextTypes.DEFAULT_TYPE):
    subscribers = read_set_from_file(SUBSCRIBERS_FILE)
    channel_id = get_channel_id()
    should_post_to_channel = bool(channel_id and is_channel_autopost_enabled())

    if not subscribers and not should_post_to_channel:
        return

    found_news, failed_sources = get_monitored_news()
    sent_links = read_set_from_file(SENT_NEWS_FILE)
    new_sent_links = set(sent_links)
    alert_news = []

    for news_item in found_news:
        news_key = get_news_key(news_item)

        if news_item["importance"] == "ВИСОКА" and news_key not in sent_links:
            alert_news.append(news_item)
            new_sent_links.add(news_key)

    if not alert_news:
        return

    message = format_news_message("🚨 OSINT-сповіщення", alert_news[:get_news_limit()])

    if failed_sources:
        message += f"\n\n⚠️ Помилки RSS-джерел: {len(failed_sources)}"

    for chat_id in subscribers:
        try:
            await send_long_message(context.bot, int(chat_id), message)
        except Exception as error:
            print(f"Не вдалося надіслати повідомлення {chat_id}: {error}")

    if should_post_to_channel:
        try:
            await send_long_message(context.bot, channel_id, message)
        except Exception as error:
            print(f"Не вдалося опублікувати новини в канал {channel_id}: {error}")

    write_set_to_file(SENT_NEWS_FILE, new_sent_links)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_user_command(update, "/help")
    await reply_long_message(update, get_help_text(), reply_markup=get_main_keyboard())


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Telegram error: {context.error}")


async def channel_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("У вас немає доступу до цієї дії.")
        return

    channel_id = get_channel_id()

    if not channel_id:
        await query.edit_message_text("CHANNEL_ID не вказано. Додайте канал у Render Environment Variables.")
        return

    post_id = query.data.split(":", 1)[1]
    user_posts = PENDING_CHANNEL_POSTS.get(query.from_user.id, {})
    news_item = user_posts.get(post_id)

    if not news_item:
        await query.edit_message_text("Новину не знайдено. Оновіть список через /news.")
        return

    try:
        await send_news_to_channel(context.bot, channel_id, news_item)
    except ValueError as error:
        await query.edit_message_text(f"Не вдалося опублікувати: {error}")
        return
    except Exception as error:
        await query.edit_message_text(f"Помилка публікації в канал: {error}")
        return

    await query.edit_message_text(f"Опубліковано в канал: {news_item['title']}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await handle_clearlogs_confirmation(update):
        return

    if await handle_clearstats_confirmation(update):
        return

    if await handle_broadcast_confirmation(update, context):
        return

    text = update.message.text

    if text == NEWS_BUTTON:
        await news_command(update, context)
    elif text == IMPORTANT_BUTTON:
        await important_command(update, context)
    elif text == SUMMARY_BUTTON:
        await summary_command(update, context)
    elif text == AI_ANALYZE_BUTTON:
        await aianalyze_command(update, context)
    elif text == HELP_BUTTON:
        await help_command(update, context)


def main():
    ensure_admins_file()
    start_health_server()
    telegram_token = get_bot_token()

    if not telegram_token:
        return

    app = Application.builder().token(telegram_token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("news", news_command))
    app.add_handler(CommandHandler("important", important_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("subscribe", subscribe_command))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("botstats", botstats_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("sources", sources_command))
    app.add_handler(CommandHandler("checkrss", checkrss_command))
    app.add_handler(CommandHandler("keywords", keywords_command))
    app.add_handler(CommandHandler("restartinfo", restartinfo_command))
    app.add_handler(CommandHandler("version", version_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("admins", admins_command))
    app.add_handler(CommandHandler("addadmin", addadmin_command))
    app.add_handler(CommandHandler("removeadmin", removeadmin_command))
    app.add_handler(CommandHandler("adminpanel", adminpanel_command))
    app.add_handler(CommandHandler("channelstatus", channelstatus_command))
    app.add_handler(CommandHandler("postimportant", postimportant_command))
    app.add_handler(CommandHandler("postsummary", postsummary_command))
    app.add_handler(CommandHandler("autopost_on", autopost_on_command))
    app.add_handler(CommandHandler("autopost_off", autopost_off_command))
    app.add_handler(CommandHandler("aianalyze", aianalyze_command))
    app.add_handler(CommandHandler("lastalerts", lastalerts_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("testalert", testalert_command))
    app.add_handler(CommandHandler("adminhelp", adminhelp_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("clearlogs", clearlogs_command))
    app.add_handler(CommandHandler("clearstats", clearstats_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(channel_post_callback, pattern="^post_news:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))
    app.add_error_handler(error_handler)

    app.job_queue.run_repeating(
        refresh_news_cache_job,
        interval=NEWS_CACHE_REFRESH_SECONDS,
        first=1,
    )

    app.job_queue.run_repeating(
        send_alerts,
        interval=ALERT_INTERVAL_MINUTES * 60,
        first=10,
    )

    print("Автоматична перевірка новин запущена")
    print("Telegram-бот запущено")

    try:
        app.run_polling()
    except NetworkError:
        print("Помилка: не вдалося підключитися до Telegram API")


if __name__ == "__main__":
    main()
