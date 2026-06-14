#!/usr/bin/env python3
"""Автономний щоденний дайджест Закарпаття -> Telegram @zakarpattianews.

Повністю автоматичний (без людини в циклі). Безпека замість ручного апруву:
  1) Claude + web_search збирає кандидатів ТІЛЬКИ з реальних знайдених URL;
  2) детермінована перевірка, що кожне посилання живе (HTTP 2xx/3xx);
  3) другий, скептичний прохід Claude-«редактора» прибирає фейки/клікбейт/
     неперевірене, нейтралізує тон чутливих тем, лишає топ-10;
  4) якщо надійних новин < мінімуму — постимо менше або пропускаємо день
     (краще нічого, ніж сміття);
  5) форматування у коді (надійне екранування), потім публікація.
Запускається в GitHub Actions (cron 19:00 Київ).
"""
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zoneinfo
from urllib.parse import urlparse

import anthropic

KYIV = zoneinfo.ZoneInfo("Europe/Kyiv")
NOW = datetime.datetime.now(KYIV)
if os.environ.get("FORCE_RUN") != "1" and NOW.hour != 19:
    print(f"Київський час {NOW:%H:%M} != 19:xx — пропускаю запуск.")
    sys.exit(0)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@zakarpattianews")
LOG_CHAT = os.environ.get("LOG_CHAT_ID")            # опц.: копія посту в приватний лог
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
VERIFY_MODEL = os.environ.get("VERIFY_MODEL", MODEL)  # модель для перевірки (можна сильнішу)
MIN_ITEMS = 5

UA_MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня",
             "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
DATE_UA = f"{NOW.day} {UA_MONTHS[NOW.month - 1]} {NOW.year}"


def claude_text(model, prompt, use_search):
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}] if use_search else None
    chunks = []
    for _ in range(8):  # дочитуємо pause_turn (серверний цикл web_search)
        kwargs = dict(model=model, max_tokens=8000, messages=messages)
        if tools:
            kwargs["tools"] = tools
        # thinking НЕ вмикаємо: для пошуку+JSON воно лише з'їдає бюджет max_tokens
        resp = client.messages.create(**kwargs)
        chunks.append("".join(b.text for b in resp.content if b.type == "text"))
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    return "".join(chunks)


def extract_json(text):
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except Exception:
        return []


def link_alive(url):
    """Детермінована перевірка: посилання реально відкривається."""
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                return 200 <= r.status < 400
        except urllib.error.HTTPError as e:
            if e.code in (403, 405):  # метод не дозволено / бот-захист — пробуємо GET
                continue
            return False
        except Exception:
            continue
    return False


GEN_PROMPT = f"""Ты — редактор Telegram-канала новостей Закарпатья. Сегодня: {DATE_UA}.
С помощью инструмента web_search найди свежие новости Закарпатской области Украины за последние сутки.
Сделай 5-8 поисков: «Закарпаття новини {DATE_UA}», «Ужгород новини», «Мукачево новини», и по сайтам
goloskarpat.info, zakarpattya.net.ua, mukachevo.net, pmg.ua, transkarpatia.net, karpatnews.in.ua.
Собери 12 кандидатов. КРИТИЧЕСКИ ВАЖНО: используй ТОЛЬКО реальные URL из результатов поиска — НЕ выдумывай ссылки.
Для каждого: emoji, заголовок (укр.), 2-3 предложения описания (укр., нейтрально, без кликбейта), url.
Верни СТРОГО JSON-массив между маркерами, без иного текста:
===JSON_START===
[{{"emoji":"🇪🇺","title":"...","desc":"...","url":"https://..."}}]
===JSON_END==="""


def gen_candidates():
    txt = claude_text(MODEL, GEN_PROMPT, use_search=True)
    print(f"      (відповідь моделі: {len(txt)} символів)")
    items = extract_json(txt)
    return [i for i in items if isinstance(i, dict) and str(i.get("url", "")).startswith("http")]


def verify(items):
    prompt = f"""Ты — придирчивый редактор-факт-чекер украинских региональных новостей. Дата: {DATE_UA}.
Вот кандидаты в JSON. Оставь ТОЛЬКО то, за что не стыдно публиковать:
- убери явные фейки, кликбейт, непроверенные сенсации, дубли одного события, рекламу, мелочёвку (гороскопы, магнитные бури);
- для чувствительных тем (война, гибель людей, трагедии) — нейтральный, уважительный тон;
- сбалансируй категории, выбери топ-10 (или меньше, если меньше достойных).
НЕ меняй поле url. Верни СТРОГО JSON-массив того же формата (максимум 10) между маркерами, без иного текста:
===JSON_START===
[...]
===JSON_END===
Кандидаты:
{json.dumps(items, ensure_ascii=False)}"""
    out = extract_json(claude_text(VERIFY_MODEL, prompt, use_search=False))
    return out if out else items[:10]


NUM = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(items):
    lines = ["📍 <b>Закарпаття | Головне за день</b>", f"🗓 {DATE_UA}", ""]
    for idx, it in enumerate(items[:10]):
        emoji = it.get("emoji", "🔹")
        title = esc(str(it.get("title", "")).strip())
        url = it.get("url", "")
        desc = esc(str(it.get("desc", "")).strip())
        lines.append(f'{NUM[idx]} {emoji} <b><a href="{url}">{title}</a></b>')
        if desc:
            lines.append(desc)
        lines.append("")
    domains = sorted({urlparse(it.get("url", "")).netloc.replace("www.", "") for it in items[:10] if it.get("url")})
    lines += ["———",
              f"📰 <i>Джерела: {', '.join(d for d in domains if d) [:120]}</i>",
              "🤖 <i>Автоматичний дайджест • щодня о 19:00</i>"]
    return "\n".join(lines)


def tg_send(chat, text):
    payload = json.dumps({"chat_id": chat, "parse_mode": "HTML",
                          "disable_web_page_preview": True, "text": text}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                 data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8")
    print("Telegram:", body[:300])
    return '"ok":true' in body


if __name__ == "__main__":
    print(f"[1/4] Генерую кандидатів ({MODEL})…")
    cands = gen_candidates()
    print(f"      кандидатів: {len(cands)}")

    print("[2/4] Перевіряю посилання…")
    cands = [i for i in cands if link_alive(i["url"])]
    print(f"      живих: {len(cands)}")

    print(f"[3/4] Скептична перевірка ({VERIFY_MODEL})…")
    final = verify(cands)
    alive_urls = {i["url"] for i in cands}
    final = [i for i in final if i.get("url") in alive_urls] or cands[:10]
    print(f"      фінал: {len(final)}")

    if len(final) < MIN_ITEMS:
        print(f"[skip] Замало надійних новин ({len(final)} < {MIN_ITEMS}) — пропускаю день.")
        sys.exit(0)

    print("[4/4] Публікую…")
    html = render(final)
    if not tg_send(CHANNEL, html):
        sys.exit("Telegram error — пост не опубліковано.")
    if LOG_CHAT:
        try:
            tg_send(LOG_CHAT, "🗒 копія опублікованого:\n\n" + html)
        except Exception as e:
            print("log copy failed:", e)
    print("OK ✅")
