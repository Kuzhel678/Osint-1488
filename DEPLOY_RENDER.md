# Deploy OSINT News Bot на Render

Telegram-бот на Render потрібно запускати як **Background Worker**, а не як Web Service.

## Підготовка

1. Завантажте проєкт на GitHub.
2. Переконайтесь, що файл `.env` не потрапив у GitHub.
3. На Render створіть новий **Background Worker**.
4. Підключіть GitHub-репозиторій.

## Налаштування Render

Build Command:

```bash
pip install -r requirements.txt
```

Start Command:

```bash
python telegram_bot.py
```

Environment Variable:

```text
TELEGRAM_BOT_TOKEN=your_bot_token_here
ADMIN_IDS=730977304
```

Не вставляйте реальний токен у код, README або `.env.example`.

## Канал Telegram

1. Створіть Telegram-канал.
2. Додайте бота в канал як адміністратора.
3. У Render додайте змінну:

```text
CHANNEL_ID=@your_channel_here
```

Команди для каналу:

```text
/channelstatus
/postimportant
/postsummary
/autopost_on
/autopost_off
```

## LLM-аналіз

Щоб увімкнути AI-аналіз, додайте в Render:

```text
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o-mini
```

Команда:

```text
/aianalyze
```

Не додавайте `OPENAI_API_KEY` у GitHub.

## Важливо про 24/7

Для постійної роботи Telegram-бота потрібен сервіс типу **Background Worker**.

Файли `admins.txt`, `subscribers.txt`, `sent_news.txt` і логи можуть втрачатися після перезапуску Render, якщо не використовується постійне сховище або база даних.
