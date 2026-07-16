"""
Крипто-дайджест бот
--------------------
Що робить:
1. Бере свіжі новини з кількох публічних RSS-джерел (CoinDesk, Cointelegraph, The Block)
2. Надсилає їх у Groq API з проханням написати ОРИГІНАЛЬНИЙ авторський пост українською
3. Публікує готовий пост одразу в твій Telegram-канал
4. Надсилає копію тобі в особисті повідомлення — щоб бачив, що опубліковано,
   і міг видалити пост із каналу, якщо щось не так

Запуск за розкладом: через cron / Планувальник завдань (дивись README.md)
"""

import os
import re
import json
import feedparser
import requests
import time
from datetime import datetime, timedelta, timezone

# ==================== НАЛАШТУВАННЯ ====================

# Отримай токен у @BotFather (див. README.md)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8979873235:AAGT9U24erroXtfViIVl71GZzcVlqSSkqnc")

# Твій особистий Telegram chat_id — куди надсилати КОПІЮ опублікованого поста
# Дізнатись: напиши боту @userinfobot, він покаже твій ID
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "6077733789")

# Твій канал, куди бот публікує пости НАПРЯМУ.
# Якщо канал публічний — просто вкажи @username каналу, наприклад "@my_crypto_channel"
# Якщо канал приватний — знадобиться числовий ID (див. README.md, розділ "Як дізнатись CHANNEL_ID")
# ВАЖЛИВО: бот має бути доданий в канал як АДМІНІСТРАТОР з правом публікації постів
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@CkyptoChe")

# Безкоштовний ключ Groq API (https://console.groq.com/keys)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
GROQ_MODEL = "llama-3.3-70b-versatile"  # потужна безкоштовна модель на Groq

# Публічні RSS-джерела новин про крипту (можна додавати свої)
RSS_SOURCES = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
}

# Файл, де зберігаємо, які новини вже обробили (щоб не дублювати)
SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen_articles.json")

# Скільки годин вважати новину "свіжою"
FRESH_HOURS = 6

# ==================== ФУНКЦІЇ ====================

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen_ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids)[-500:], f)  # тримаємо тільки останні 500, щоб файл не ріс без кінця


def extract_image_url(entry):
    """Пробує знайти URL картинки в RSS-записі (media:content, media:thumbnail, enclosure)."""
    if entry.get("media_content"):
        url = entry.media_content[0].get("url")
        if url:
            return url
    if entry.get("media_thumbnail"):
        url = entry.media_thumbnail[0].get("url")
        if url:
            return url
    for link in entry.get("links", []):
        if link.get("type", "").startswith("image"):
            return link.get("href")
    return None


def fetch_fresh_articles():
    """Збирає свіжі статті з усіх RSS-джерел, яких ще не бачили."""
    seen = load_seen()
    fresh = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FRESH_HOURS)

    for source_name, url in RSS_SOURCES.items():
        feed = feedparser.parse(url)
        for entry in feed.entries[:10]:  # дивимось тільки останні 10 записів з кожного джерела
            uid = entry.get("id", entry.get("link"))
            if uid in seen:
                continue

            # Парсимо дату публікації, якщо є
            published = None
            if entry.get("published_parsed"):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

            if published and published < cutoff:
                continue  # застаріла новина, пропускаємо

            fresh.append({
                "id": uid,
                "source": source_name,
                "title": entry.get("title", ""),
                "summary": entry.get("summary", "")[:500],
                "link": entry.get("link", ""),
                "image": extract_image_url(entry),
            })

    return fresh, seen


GIBBERISH_WHITELIST = {
    "btc", "eth", "sol", "nft", "api", "ai", "usd", "eur", "uae",
    "etf", "llc", "otc", "kyc", "aml", "p2p", "defi", "dao", "vc",
}


def clean_post_text(text):
    """Прибирає випадкові сміттєві токени, які іноді додає безкоштовна модель
    в самому кінці тексту (наприклад, окреме беззмістовне слово без голосних)."""
    text = text.strip()
    match = re.search(r"\s([a-zA-Z]{2,6})\s*$", text)
    if match:
        token = match.group(1).lower()
        if token not in GIBBERISH_WHITELIST and not any(v in token for v in "aeiouy"):
            text = text[: match.start()].rstrip()
    return text


def generate_post(articles):
    """Відправляє статті в Claude API і отримує готовий авторський пост українською."""
    if not articles:
        return None

    sources_text = "\n\n".join(
        f"Джерело: {a['source']}\nЗаголовок: {a['title']}\nОпис: {a['summary']}"
        for a in articles
    )

    prompt = f"""Ти — редактор українськомовного Telegram-каналу про криптовалюти. Твоя аудиторія — люди, які цікавляться крипторинком, але не хочуть читати сухі новинні зведення.

Ось кілька свіжих новин з різних джерел:

{sources_text}

Крок 1: обери З ЦИХ НОВИН ОДНУ найцікавішу/найважливішу для аудиторії (не намагайся впхнути всі одразу).

Крок 2: напиши на основі неї ОДИН пост українською мовою для Telegram:
- Почни з емодзі + короткий "гачок" у першому реченні, який чіпляє увагу (не просто констатація факту)
- Перекажи суть своїми словами, не копіюй структуру чи речення оригіналу
- Додай 1-2 речення контексту або "чому це важливо" — не просто факт, а сенс для читача
- Якщо доречно — постав 1 коротке риторичне питання в кінці, щоб зчепити коментарі
- Обсяг: 400-600 символів (коротко й ємно, не розтягуй)
- У кінці окремим рядком вкажи джерело: "За даними: [назва джерела]" (тільки те одне джерело, яке реально використав)
- Додай 2-3 доречні хештеги
- НЕ давай інвестиційних порад ("купуй/продавай"), тільки факти та контекст
- Пиши живо, як людина, а не як прес-реліз — без канцеляризмів і штампів

Виведи ТІЛЬКИ готовий текст поста, без пояснень від себе і без назви кроків."""

    url = "https://api.groq.com/openai/v1/chat/completions"
    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "content-type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
        },
        timeout=60,
    )
    if not response.ok:
        print("=== ПОМИЛКА ВІД GROQ API ===")
        print(f"Код: {response.status_code}")
        print(f"Відповідь: {response.text}")
        print("=============================")
    response.raise_for_status()
    data = response.json()
    raw_text = data["choices"][0]["message"]["content"].strip()
    return clean_post_text(raw_text)


def send_post(chat_id, text, articles, note="", include_sources=True):
    """Надсилає пост (фото+підпис або просто текст) у вказаний chat_id.
    Використовується і для публікації в канал (include_sources=False —
    чистий пост без посилань), і для копії адміну (include_sources=True —
    з посиланнями на джерела для перевірки)."""
    sources_list = "\n".join(f"• {a['source']}: {a['link']}" for a in articles)
    header = f"{note}\n\n" if note else ""

    # Шукаємо першу картинку серед свіжих новин цього запуску
    image_url = next((a["image"] for a in articles if a.get("image")), None)

    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    if image_url:
        # Підпис до фото в Telegram обмежений 1024 символами
        caption = f"{header}{text}"
        if len(caption) > 1024:
            caption = caption[:1000] + "…"

        resp = requests.post(f"{base_url}/sendPhoto", json={
            "chat_id": chat_id,
            "photo": image_url,
            "caption": caption,
        })
        if not resp.ok:
            print(f"Не вдалось надіслати фото в {chat_id} ({resp.text}), надсилаю текстом.")
            image_url = None
        elif include_sources:
            requests.post(f"{base_url}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"🔗 Оригінальні джерела:\n{sources_list}",
                "disable_web_page_preview": True,
            })

    if not image_url:
        full_message = f"{header}{text}"
        if include_sources:
            full_message += f"\n\n—\n🔗 Оригінальні джерела:\n{sources_list}"
        resp = requests.post(f"{base_url}/sendMessage", json={
            "chat_id": chat_id,
            "text": full_message,
            "disable_web_page_preview": True,
        })
        resp.raise_for_status()


def main():
    articles, seen = fetch_fresh_articles()

    if not articles:
        print("Немає нових статей — пропускаємо цей запуск.")
        return

    post_text = generate_post(articles)
    if not post_text:
        print("Groq не повернув текст поста.")
        return

    # 1. Публікуємо напряму в канал — чистий пост, без посилань і зайвого тексту
    send_post(CHANNEL_ID, post_text, articles, include_sources=False)

    # 2. Надсилаємо копію собі в особисті з посиланнями на джерела — для контролю
    send_post(ADMIN_CHAT_ID, post_text, articles, note="✅ Щойно опубліковано в канал:", include_sources=True)

    # Позначаємо статті як оброблені
    seen.update(a["id"] for a in articles)
    save_seen(seen)

    print(f"Готово: опубліковано в канал пост на основі {len(articles)} новин.")


CHECK_INTERVAL = 300  # 5 минут

if __name__ == "__main__":
    print(f"Бот запущен. Проверка RSS каждые {CHECK_INTERVAL} секунд.")
    while True:
        try:
            print("=" * 60)
            print("Новая проверка RSS...")
            main()
        except Exception as e:
            print(f"Ошибка: {e}")

        print(f"Ожидание {CHECK_INTERVAL} секунд...")
        time.sleep(CHECK_INTERVAL)

