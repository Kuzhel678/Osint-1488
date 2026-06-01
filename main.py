from datetime import datetime
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import feedparser


APP_VERSION = "1.0.0"
KEYWORDS_FILE = "keywords.txt"
SOURCES_FILE = "sources.txt"
SETTINGS_FILE = "settings.txt"
LOGS_FILE = "logs.txt"
REPORT_FILE = "report.txt"
CATEGORY_REPORT_FILE = "category_report.txt"
OPERATIONAL_SUMMARY_FILE = "operational_summary.txt"
DEFAULT_NEWS_LIMIT = 10
DEFAULT_AUTO_MONITORING_INTERVAL = 30
DEFAULT_SHOW_ONLY_IMPORTANT = False
DEFAULT_SETTINGS = {
    "NEWS_LIMIT": str(DEFAULT_NEWS_LIMIT),
    "AUTO_MONITORING_INTERVAL": str(DEFAULT_AUTO_MONITORING_INTERVAL),
    "SHOW_ONLY_IMPORTANT": str(DEFAULT_SHOW_ONLY_IMPORTANT),
}
CATEGORIES = ["Війна", "Політика", "Міжнародні відносини", "Санкції", "Інше"]
IMPORTANCE_ORDER = {
    "ВИСОКА": 1,
    "СЕРЕДНЯ": 2,
    "НИЗЬКА": 3,
}


def read_lines_from_file(file_name, item_name):
    try:
        with open(file_name, "r", encoding="utf-8") as file:
            lines = []

            for line in file:
                text = line.strip()

                if text:
                    lines.append(text)

    except FileNotFoundError:
        print(f"❌ Файл {file_name} не знайдено.")
        print(f"Створіть файл і додайте {item_name}, кожен з нового рядка.")
        return []

    if not lines:
        print(f"❌ Файл {file_name} порожній.")
        print(f"Додайте хоча б один рядок: {item_name}.")
        return []

    return lines


def create_default_settings():
    with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
        for key, value in DEFAULT_SETTINGS.items():
            file.write(f"{key}={value}\n")


def read_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
            settings = DEFAULT_SETTINGS.copy()

            for line in file:
                text = line.strip()

                if not text or "=" not in text:
                    continue

                key, value = text.split("=", 1)
                settings[key.strip()] = value.strip()

    except FileNotFoundError:
        create_default_settings()
        settings = DEFAULT_SETTINGS.copy()
        print(f"ℹ️ Файл {SETTINGS_FILE} не знайдено. Створено базові налаштування.")

    try:
        news_limit = int(settings["NEWS_LIMIT"])
    except ValueError:
        news_limit = DEFAULT_NEWS_LIMIT

    if news_limit < 1:
        news_limit = DEFAULT_NEWS_LIMIT

    try:
        auto_interval = int(settings["AUTO_MONITORING_INTERVAL"])
    except ValueError:
        auto_interval = DEFAULT_AUTO_MONITORING_INTERVAL

    if auto_interval < 1:
        auto_interval = DEFAULT_AUTO_MONITORING_INTERVAL

    show_only_important = settings["SHOW_ONLY_IMPORTANT"].lower() == "true"

    return {
        "news_limit": news_limit,
        "auto_interval": auto_interval,
        "show_only_important": show_only_important,
    }


def find_keyword(title, keywords):
    title_lower = title.lower()

    for keyword in keywords:
        if keyword.lower() in title_lower:
            return keyword

    return None


def print_header(keywords):
    print("📰" + "=" * 58 + "📰")
    print("                    OSINT News Bot")
    print("          🔎 Моніторинг RSS за ключовими словами")
    print("📰" + "=" * 58 + "📰")

    print("\n🔑 Ключові слова:")
    print(", ".join(keywords))


def make_short_analysis(keyword):
    return (
        "Новина може бути важливою для інформаційного моніторингу, "
        f"оскільки містить ключове слово: {keyword}"
    )


def calculate_importance(title, keyword):
    high_importance_words = [
        "терміново",
        "атака",
        "ракета",
        "вибух",
        "загроза",
        "НАТО",
        "санкції",
    ]
    medium_importance_words = ["Україна", "Росія", "Польща", "Білорусь", "ЗСУ"]

    title_lower = title.lower()
    keyword_lower = keyword.lower()

    for word in high_importance_words:
        if word.lower() in title_lower or word.lower() == keyword_lower:
            return "ВИСОКА"

    for word in medium_importance_words:
        if word.lower() in title_lower or word.lower() == keyword_lower:
            return "СЕРЕДНЯ"

    return "НИЗЬКА"


def detect_category(title):
    categories = {
        "Війна": ["війна", "атака", "ракета", "вибух", "фронт", "ЗСУ"],
        "Політика": ["президент", "уряд", "парламент", "вибори", "міністр"],
        "Міжнародні відносини": ["НАТО", "ЄС", "США", "Польща", "Білорусь", "Росія"],
        "Санкції": ["санкції", "обмеження", "заборона"],
    }

    title_lower = title.lower()

    for category, words in categories.items():
        for word in words:
            if word.lower() in title_lower:
                return category

    return "Інше"


def print_news(number, news_item):
    title = news_item["title"]
    published_date = news_item["published_date"]
    source = news_item["source"]
    link = news_item["link"]
    keyword = news_item["keyword"]
    importance = news_item["importance"]
    category = news_item["category"]
    short_analysis = make_short_analysis(keyword)

    print(f"\n{number}. 🧭 Новина")
    print(f"   Назва: {title}")
    print(f"   Дата: {published_date}")
    print(f"   Джерело: {source}")
    print(f"   Категорія: {category}")
    print(f"   Важливість: {importance}")
    print(f"   Ключове слово: {keyword}")
    print(f"   Посилання: {link}")
    print(f"   Короткий висновок: {short_analysis}")


def print_grouped_news(found_news, keywords, news_limit):
    shown_news = found_news[:news_limit]

    for keyword in keywords:
        news_for_keyword = []

        for news_item in shown_news:
            if news_item["keyword"] == keyword:
                news_for_keyword.append(news_item)

        if not news_for_keyword:
            continue

        print(f"\n🔎 Ключове слово: {keyword}")
        print("-" * 60)

        for number, news_item in enumerate(news_for_keyword, start=1):
            print_news(number, news_item)


def count_news_by_category(found_news):
    statistics = {}

    for category in CATEGORIES:
        statistics[category] = 0

    for news_item in found_news:
        category = news_item["category"]
        statistics[category] += 1

    return statistics


def print_category_statistics(found_news):
    statistics = count_news_by_category(found_news)

    print("\n=== Статистика ===")

    for category in CATEGORIES:
        print(f"{category}: {statistics[category]}")


def sort_news_by_importance(found_news):
    return sorted(
        found_news,
        key=lambda news_item: IMPORTANCE_ORDER[news_item["importance"]],
    )


def extract_article_image_url(link):
    if not link:
        return ""

    request = Request(
        link,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    try:
        with urlopen(request, timeout=5) as response:
            html = response.read(300000).decode("utf-8", errors="ignore")
    except (HTTPError, URLError, TimeoutError, ValueError):
        return ""

    image_patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]

    for pattern in image_patterns:
        image_match = re.search(pattern, html, re.IGNORECASE)

        if image_match:
            return image_match.group(1)

    return ""


def extract_image_url(news_item, link=""):
    media_content = news_item.get("media_content", [])

    if media_content:
        image_url = media_content[0].get("url", "")

        if image_url:
            return image_url

    media_thumbnail = news_item.get("media_thumbnail", [])

    if media_thumbnail:
        image_url = media_thumbnail[0].get("url", "")

        if image_url:
            return image_url

    for enclosure in news_item.get("enclosures", []):
        enclosure_type = enclosure.get("type", "")
        image_url = enclosure.get("href", "")

        if image_url and enclosure_type.startswith("image/"):
            return image_url

    summary = news_item.get("summary", "")
    image_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)

    if image_match:
        return image_match.group(1)

    return extract_article_image_url(link)


def collect_news(sources, keywords):
    found_news = []
    failed_sources = []
    shown_news_keys = set()

    for rss_url in sources:
        news_feed = feedparser.parse(rss_url)

        if news_feed.bozo:
            failed_sources.append(rss_url)
            continue

        source = news_feed.feed.get("title", "Невідоме джерело")

        for news_item in news_feed.entries:
            title = news_item.get("title", "Без заголовка")
            published_date = news_item.get("published", news_item.get("updated", "невідомо"))
            link = news_item.get("link", "")
            keyword = find_keyword(title, keywords)

            if link:
                news_key = link
            else:
                news_key = title

            if keyword and news_key not in shown_news_keys:
                importance = calculate_importance(title, keyword)
                category = detect_category(title)
                shown_news_keys.add(news_key)
                found_news.append(
                    {
                        "keyword": keyword,
                        "importance": importance,
                        "category": category,
                        "title": title,
                        "published_date": published_date,
                        "source": source,
                        "link": link or "Посилання відсутнє",
                        "image_url": extract_image_url(news_item, link),
                    }
                )

    return found_news, failed_sources


def write_log(sources, total_found, failed_sources):
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    if failed_sources:
        status = "ERROR"
    else:
        status = "SUCCESS"

    with open(LOGS_FILE, "a", encoding="utf-8") as file:
        file.write(f"[{current_time}]\n")
        file.write(f"Перевірено джерел: {len(sources)}\n")
        file.write(f"Знайдено новин: {total_found}\n")
        file.write("RSS-джерела:\n")

        for source in sources:
            file.write(f"- {source}\n")

        if failed_sources:
            file.write("Помилки RSS: так\n")
            file.write("Недоступні джерела:\n")

            for source in failed_sources:
                file.write(f"- {source}\n")
        else:
            file.write("Помилки RSS: ні\n")

        file.write(f"Статус: {status}\n\n")


def save_report(found_news):
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        file.write("OSINT News Bot - звіт\n")
        file.write(f"Дата і час: {current_time}\n")
        file.write(f"Знайдено новин: {len(found_news)}\n")
        file.write("=" * 60 + "\n\n")

        for number, news_item in enumerate(found_news, start=1):
            short_analysis = make_short_analysis(news_item["keyword"])

            file.write(f"{number}. Новина\n")
            file.write(f"Назва: {news_item['title']}\n")
            file.write(f"Дата: {news_item['published_date']}\n")
            file.write(f"Джерело: {news_item['source']}\n")
            file.write(f"Категорія: {news_item['category']}\n")
            file.write(f"Важливість: {news_item['importance']}\n")
            file.write(f"Ключове слово: {news_item['keyword']}\n")
            file.write(f"Посилання: {news_item['link']}\n")
            file.write(f"Короткий висновок: {short_analysis}\n")
            file.write("-" * 60 + "\n\n")


def ask_to_save_report(found_news):
    answer = input("\nЗберегти результати у report.txt? (y/n): ").strip().lower()

    if answer == "y":
        save_report(found_news)
        print("✅ Результати збережено у report.txt")
    elif answer == "n":
        print("ℹ️ Результати не збережено.")
    else:
        print("❌ Неправильний вибір. Результати не збережено.")


def ask_to_save_operational_summary(summary_text):
    answer = input("\nЗберегти зведену довідку у файл? (y/n): ").strip().lower()

    if answer == "y":
        with open(OPERATIONAL_SUMMARY_FILE, "w", encoding="utf-8") as file:
            file.write(summary_text)

        print("Зведену довідку збережено у operational_summary.txt")
    elif answer == "n":
        return
    else:
        print("❌ Неправильний вибір. Зведену довідку не збережено.")


def show_menu():
    print(f"\n=== OSINT News Bot v{APP_VERSION} ===")
    print()
    print("1. Запустити моніторинг новин")
    print("2. Показати ключові слова")
    print("3. Показати RSS-джерела")
    print("4. Показати тільки важливі новини")
    print("5. Пошук новин за власним ключовим словом")
    print("6. Пошук новин за категорією")
    print("7. Змінити ліміт новин")
    print("8. Перевірити RSS-джерела")
    print("9. Створити короткий звіт по категоріях")
    print("10. Пошук новин за RSS-джерелом")
    print("11. Топ-5 найважливіших новин")
    print("12. Оперативна зведена довідка")
    print("13. Автоматичний моніторинг")
    print("14. Очистити старі звіти та логи")
    print("15. Статус системи")
    print("16. Перевірка проєкту")
    print("17. Вийти")


def show_file_items(file_name, item_name):
    items = read_lines_from_file(file_name, item_name)

    if not items:
        return

    print(f"\n📄 {item_name}:")

    for number, item in enumerate(items, start=1):
        print(f"{number}. {item}")


def run_monitoring(
    news_limit,
    only_high_importance=False,
    custom_keywords=None,
    custom_sources=None,
    selected_category=None,
    empty_message="За заданими ключовими словами новин не знайдено",
    ask_report=True,
):
    if custom_keywords:
        keywords = custom_keywords
    else:
        keywords = read_lines_from_file(KEYWORDS_FILE, "ключові слова")

    if custom_sources:
        sources = custom_sources
    else:
        sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")

    if not keywords or not sources:
        return

    print_header(keywords)

    found_news, failed_sources = collect_news(sources, keywords)

    if only_high_importance:
        important_news = []

        for news_item in found_news:
            if news_item["importance"] == "ВИСОКА":
                important_news.append(news_item)

        found_news = important_news

    if selected_category:
        category_news = []

        for news_item in found_news:
            if news_item["category"] == selected_category:
                category_news.append(news_item)

        found_news = category_news

    found_news = sort_news_by_importance(found_news)

    total_found = len(found_news)

    write_log(sources, total_found, failed_sources)

    print(f"\nЗнайдено новин: {total_found}")

    for rss_url in failed_sources:
        print(f"❌ Не вдалося прочитати RSS-джерело: {rss_url}")

    if total_found == 0:
        if only_high_importance:
            print("Важливих новин зараз не знайдено")
        elif selected_category:
            print("Новин у цій категорії не знайдено")
        else:
            print(empty_message)
        return

    print(f"Показано перші {min(total_found, news_limit)} новин:")
    print("-" * 60)

    print_grouped_news(found_news, keywords, news_limit)

    print_category_statistics(found_news)

    if ask_report:
        ask_to_save_report(found_news)

    print("\n" + "✅" + "=" * 58 + "✅")
    print("Готово!")


def search_by_custom_keyword(news_limit):
    keyword = input("\nВведіть ключове слово для пошуку: ").strip()

    if not keyword:
        print("❌ Ключове слово не може бути порожнім.")
        return

    run_monitoring(
        news_limit,
        custom_keywords=[keyword],
        empty_message="Новин за цим словом не знайдено",
    )


def search_by_category(news_limit):
    print("\nОберіть категорію:")

    for number, category in enumerate(CATEGORIES, start=1):
        print(f"{number}. {category}")

    choice = input("\nВаш вибір: ").strip()

    if not choice.isdigit():
        print("❌ Неправильний вибір категорії.")
        return

    category_number = int(choice)

    if category_number < 1 or category_number > len(CATEGORIES):
        print("❌ Неправильний вибір категорії.")
        return

    selected_category = CATEGORIES[category_number - 1]
    print(f"\n📂 Обрана категорія: {selected_category}")

    run_monitoring(news_limit, selected_category=selected_category)


def change_news_limit(current_limit):
    new_limit = input("\nВведіть новий ліміт новин: ").strip()

    try:
        new_limit = int(new_limit)
    except ValueError:
        print("Введіть коректне число")
        return current_limit

    if new_limit < 1:
        print("Ліміт має бути більше 0")
        return current_limit

    print(f"✅ Новий ліміт новин: {new_limit}")
    return new_limit


def check_rss_sources():
    sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")

    if not sources:
        return

    working_count = 0
    error_count = 0

    print("\n=== Перевірка RSS-джерел ===")

    for source in sources:
        news_feed = feedparser.parse(source)

        if news_feed.bozo or not news_feed.entries:
            print(f"❌ Помилка: {source}")
            error_count += 1
        else:
            print(f"✅ Працює: {source}")
            working_count += 1

    print("\n=== Підсумок ===")
    print(f"Перевірено джерел: {len(sources)}")
    print(f"Працює: {working_count}")
    print(f"Помилок: {error_count}")


def create_category_report():
    keywords = read_lines_from_file(KEYWORDS_FILE, "ключові слова")
    sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")

    if not keywords or not sources:
        return

    print("\n📊 Створюю короткий звіт по категоріях...")

    found_news, failed_sources = collect_news(sources, keywords)
    total_found = len(found_news)
    statistics = count_news_by_category(found_news)
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    max_category = CATEGORIES[0]

    for category in CATEGORIES:
        if statistics[category] > statistics[max_category]:
            max_category = category

    write_log(sources, total_found, failed_sources)

    with open(CATEGORY_REPORT_FILE, "w", encoding="utf-8") as file:
        file.write("=== Звіт OSINT News Bot ===\n")
        file.write(f"Дата: {current_time}\n")
        file.write(f"Усього знайдено новин: {total_found}\n\n")
        file.write("Категорії:\n")

        for category in CATEGORIES:
            file.write(f"- {category}: {statistics[category]}\n")

        file.write("\nКороткий висновок:\n")
        file.write(f"Найбільше новин зараз у категорії: {max_category}\n")

    print(f"✅ Звіт створено: {CATEGORY_REPORT_FILE}")


def search_by_source(news_limit):
    sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")

    if not sources:
        return

    print("\nОберіть RSS-джерело:")

    for number, source in enumerate(sources, start=1):
        print(f"{number}. {source}")

    choice = input("\nВаш вибір: ").strip()

    if not choice.isdigit():
        print("Невірний номер джерела")
        return

    source_number = int(choice)

    if source_number < 1 or source_number > len(sources):
        print("Невірний номер джерела")
        return

    selected_source = sources[source_number - 1]
    print(f"\n📡 Обране джерело: {selected_source}")

    run_monitoring(
        news_limit,
        custom_sources=[selected_source],
    )


def show_top_important_news():
    keywords = read_lines_from_file(KEYWORDS_FILE, "ключові слова")
    sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")

    if not keywords or not sources:
        return

    found_news, failed_sources = collect_news(sources, keywords)
    found_news = sort_news_by_importance(found_news)
    top_news = found_news[:5]

    write_log(sources, len(found_news), failed_sources)

    print("\n=== Топ-5 найважливіших новин ===")

    for rss_url in failed_sources:
        print(f"❌ Не вдалося прочитати RSS-джерело: {rss_url}")

    if not top_news:
        print("За заданими ключовими словами новин не знайдено")
        return

    for number, news_item in enumerate(top_news, start=1):
        print(f"\n{number}. {news_item['title']}")
        print(f"Дата: {news_item['published_date']}")
        print(f"Джерело: {news_item['source']}")
        print(f"Категорія: {news_item['category']}")
        print(f"Важливість: {news_item['importance']}")
        print(f"Посилання: {news_item['link']}")


def show_operational_summary():
    keywords = read_lines_from_file(KEYWORDS_FILE, "ключові слова")
    sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")

    if not keywords or not sources:
        return

    found_news, failed_sources = collect_news(sources, keywords)
    important_news = []

    for news_item in found_news:
        if news_item["importance"] == "ВИСОКА" or news_item["importance"] == "СЕРЕДНЯ":
            important_news.append(news_item)

    important_news = sort_news_by_importance(important_news)
    statistics = count_news_by_category(important_news)
    max_category = CATEGORIES[0]

    for category in CATEGORIES:
        if statistics[category] > statistics[max_category]:
            max_category = category

    write_log(sources, len(important_news), failed_sources)

    summary_lines = ["=== Оперативна зведена довідка ==="]

    for rss_url in failed_sources:
        summary_lines.append(f"❌ Не вдалося прочитати RSS-джерело: {rss_url}")

    if not important_news:
        summary_lines.append("")
        summary_lines.append("Новин з високою або середньою важливістю не знайдено")
        summary_text = "\n".join(summary_lines)
        print("\n" + summary_text)
        ask_to_save_operational_summary(summary_text)
        return

    for category in CATEGORIES:
        summary_lines.append("")
        summary_lines.append(f"{category}:")

        category_has_news = False

        for news_item in important_news:
            if news_item["category"] == category:
                summary_lines.append(f"- {news_item['title']}")
                category_has_news = True

        if not category_has_news:
            summary_lines.append("- немає новин")

    summary_lines.append("")
    summary_lines.append("Короткий висновок:")
    summary_lines.append(f"Найбільше уваги зараз потребує категорія: {max_category}")

    summary_text = "\n".join(summary_lines)
    print("\n" + summary_text)
    ask_to_save_operational_summary(summary_text)


def run_automatic_monitoring(news_limit, default_interval, show_only_important):
    interval = input(
        f"\nВведіть інтервал у хвилинах [{default_interval}]: "
    ).strip()

    if not interval:
        interval = default_interval

    try:
        interval = int(interval)
    except ValueError:
        print("Введіть коректне число")
        return

    if interval < 1:
        print("Інтервал має бути більше 0")
        return

    print("\nАвтоматичний моніторинг запущено.")
    print("Щоб зупинити, натисніть Ctrl + C.")

    try:
        while True:
            run_monitoring(
                news_limit,
                only_high_importance=show_only_important,
                ask_report=False,
            )
            print(f"\nНаступний запуск через {interval} хв.")
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        print("\nАвтоматичний моніторинг зупинено користувачем")


def clear_reports_and_logs():
    answer = input("\nВи точно хочете очистити звіти та логи? (y/n): ").strip().lower()

    if answer == "n":
        return

    if answer != "y":
        print("❌ Неправильний вибір. Очищення скасовано.")
        return

    files_to_clear = [
        REPORT_FILE,
        CATEGORY_REPORT_FILE,
        OPERATIONAL_SUMMARY_FILE,
        LOGS_FILE,
    ]

    for file_name in files_to_clear:
        if os.path.exists(file_name):
            with open(file_name, "w", encoding="utf-8"):
                pass

    print("Звіти та логи очищено")


def show_system_status(news_limit, auto_interval, show_only_important):
    sources = read_lines_from_file(SOURCES_FILE, "RSS-джерела")
    keywords = read_lines_from_file(KEYWORDS_FILE, "ключові слова")
    report_files = [
        REPORT_FILE,
        CATEGORY_REPORT_FILE,
        OPERATIONAL_SUMMARY_FILE,
        LOGS_FILE,
    ]

    print("\n=== Статус системи ===")
    print(f"RSS-джерел: {len(sources)}")
    print(f"Ключових слів: {len(keywords)}")
    print(f"Ліміт новин: {news_limit}")
    print(f"Автомоніторинг: {auto_interval} хв")
    print(f"Тільки важливі: {show_only_important}")

    print("\nФайли:")

    for file_name in report_files:
        if os.path.exists(file_name):
            print(f"✅ {file_name}")
        else:
            print(f"❌ {file_name}")


def check_project():
    required_files = [
        "main.py",
        KEYWORDS_FILE,
        SOURCES_FILE,
        SETTINGS_FILE,
        "README.md",
    ]
    files_that_must_not_be_empty = [KEYWORDS_FILE, SOURCES_FILE, SETTINGS_FILE]

    print("\n=== Перевірка проєкту ===")

    for file_name in required_files:
        if os.path.exists(file_name):
            print(f"✅ {file_name} знайдено")
        else:
            print(f"⚠️ Проблема: {file_name} не знайдено")

    for file_name in files_that_must_not_be_empty:
        if os.path.exists(file_name) and os.path.getsize(file_name) == 0:
            print(f"⚠️ Проблема: {file_name} порожній")

    if not os.path.exists(SETTINGS_FILE):
        return

    settings = {}

    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()

            if not text or "=" not in text:
                continue

            key, value = text.split("=", 1)
            settings[key.strip()] = value.strip()

    news_limit = settings.get("NEWS_LIMIT")
    auto_interval = settings.get("AUTO_MONITORING_INTERVAL")
    show_only_important = settings.get("SHOW_ONLY_IMPORTANT")

    try:
        if int(news_limit) < 1:
            print("⚠️ Проблема: NEWS_LIMIT має бути числом більше 0")
    except (TypeError, ValueError):
        print("⚠️ Проблема: NEWS_LIMIT має бути числом більше 0")

    try:
        if int(auto_interval) < 1:
            print("⚠️ Проблема: AUTO_MONITORING_INTERVAL має бути числом більше 0")
    except (TypeError, ValueError):
        print("⚠️ Проблема: AUTO_MONITORING_INTERVAL має бути числом більше 0")

    if show_only_important not in ["True", "False"]:
        print("⚠️ Проблема: SHOW_ONLY_IMPORTANT має бути True або False")


def main():
    settings = read_settings()
    news_limit = settings["news_limit"]
    auto_interval = settings["auto_interval"]
    show_only_important = settings["show_only_important"]

    while True:
        show_menu()
        print(f"Поточний ліміт новин: {news_limit}")
        print(f"Стандартний інтервал автомоніторингу: {auto_interval} хв.")
        print(f"Показувати тільки важливі: {show_only_important}")
        choice = input("\nОберіть пункт меню: ")

        if choice == "1":
            run_monitoring(news_limit, only_high_importance=show_only_important)
        elif choice == "2":
            show_file_items(KEYWORDS_FILE, "ключові слова")
        elif choice == "3":
            show_file_items(SOURCES_FILE, "RSS-джерела")
        elif choice == "4":
            run_monitoring(news_limit, only_high_importance=True)
        elif choice == "5":
            search_by_custom_keyword(news_limit)
        elif choice == "6":
            search_by_category(news_limit)
        elif choice == "7":
            news_limit = change_news_limit(news_limit)
        elif choice == "8":
            check_rss_sources()
        elif choice == "9":
            create_category_report()
        elif choice == "10":
            search_by_source(news_limit)
        elif choice == "11":
            show_top_important_news()
        elif choice == "12":
            show_operational_summary()
        elif choice == "13":
            run_automatic_monitoring(news_limit, auto_interval, show_only_important)
        elif choice == "14":
            clear_reports_and_logs()
        elif choice == "15":
            show_system_status(news_limit, auto_interval, show_only_important)
        elif choice == "16":
            check_project()
        elif choice == "17":
            print("\n👋 Роботу завершено.")
            break
        else:
            print("\nНевірний вибір. Спробуйте ще раз.")


if __name__ == "__main__":
    main()
