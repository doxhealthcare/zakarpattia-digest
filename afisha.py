#!/usr/bin/env python3
"""Афіша на вихідні — пост по пʼятницях ~17:00 Київ -> @zakarpattianews.

Claude + web_search збирає події на найближчі субота-неділя в Ужгороді/Закарпатті
(концерти, театр, фестивалі, виставки, ярмарки, дитяче). Тільки реальні посилання.
Запускається в GitHub Actions (cron пʼятниця 17:00 Київ).
"""
import datetime
import json
import os
import re
import sys
import urllib.request
import zoneinfo
from urllib.parse import urlparse

import anthropic

KYIV = zoneinfo.ZoneInfo("Europe/Kyiv")
NOW = datetime.datetime.now(KYIV)
# Тільки пʼятниця (weekday==4) о 17:xx; FORCE_RUN=1 — обійти для тесту
if os.environ.get("FORCE_RUN") != "1" and not (NOW.weekday() == 4 and NOW.hour == 17):
    print(f"Не пʼятниця 17:xx ({NOW:%a %H:%M}) — пропускаю запуск.")
    sys.exit(0)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@zakarpattianews")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
MIN_ITEMS = 2

UA_MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня",
             "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
DATE_UA = f"{NOW.day} {UA_MONTHS[NOW.month - 1]} {NOW.year}"

PROMPT = f"""Ты — редактор афиши. Сегодня пятница, {DATE_UA}.
С помощью web_search найди события на ближайшие ВЫХОДНЫЕ (суббота и воскресенье) в Ужгороде и Закарпатье:
концерты, театр, фестивали, выставки, ярмарки, спорт, детское.
Источники: 0312.ua/afisha, uzhgorod.internet-bilet.ua, uzhgorod.kontramarka.ua, concert.ua/uk/uzhhorod,
uzhhorod.karabas.com, uzhgorod-day.com. Бери ТОЛЬКО реальные URL из результатов поиска (не выдумывай).
Отбери 5-8 интересных событий именно на эти выходные. Для каждого: emoji, название (укр.),
когда (день+время), место (укр.), url.
Верни СТРОГО JSON-массив между маркерами, без иного текста:
===JSON_START===
[{{"emoji":"🎵","title":"...","when":"Сб 19:00","place":"...","url":"https://..."}}]
===JSON_END==="""


def extract_json(text):
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except Exception:
        return []


def gen():
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": PROMPT}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
    chunks = []
    for _ in range(8):  # дочитуємо pause_turn (серверний цикл web_search)
        kwargs = dict(model=MODEL, max_tokens=3000, messages=messages, tools=tools)
        if "haiku" not in MODEL:
            kwargs["thinking"] = {"type": "adaptive"}
        resp = client.messages.create(**kwargs)
        chunks.append("".join(b.text for b in resp.content if b.type == "text"))
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    txt = "".join(chunks)
    print(f"відповідь моделі: {len(txt)} символів")
    items = extract_json(txt)
    return [i for i in items if isinstance(i, dict) and str(i.get("url", "")).startswith("http")]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(items):
    lines = ["🎭 <b>Афіша на вихідні | Закарпаття</b>", f"🗓 {DATE_UA}", ""]
    for it in items[:8]:
        emoji = it.get("emoji", "🎟")
        title = esc(str(it.get("title", "")).strip())
        url = it.get("url", "")
        when = esc(str(it.get("when", "")).strip())
        place = esc(str(it.get("place", "")).strip())
        meta = " · ".join(p for p in (when, place) if p)
        lines.append(f'{emoji} <b><a href="{url}">{title}</a></b>')
        if meta:
            lines.append(meta)
        lines.append("")
    lines += ["———", "🤖 <i>Гарних вихідних! Деталі та квитки — за посиланнями.</i>"]
    return "\n".join(lines)


def tg_send(text):
    payload = json.dumps({"chat_id": CHANNEL, "parse_mode": "HTML",
                          "disable_web_page_preview": True, "text": text}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                 data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8")
    print("Telegram:", body[:300])
    return '"ok":true' in body


if __name__ == "__main__":
    print(f"Шукаю події на вихідні ({MODEL})…")
    items = gen()
    print(f"подій: {len(items)}")
    if len(items) < MIN_ITEMS:
        print(f"[skip] Замало подій ({len(items)} < {MIN_ITEMS}) — пропускаю.")
        sys.exit(0)
    if not tg_send(render(items)):
        sys.exit("Telegram error — афіша не опублікована.")
    print("OK ✅")
