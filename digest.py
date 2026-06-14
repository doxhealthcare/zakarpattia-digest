#!/usr/bin/env python3
"""Щоденний дайджест Закарпаття -> Telegram @zakarpattianews. БЮДЖЕТНА версія.

Новини беруться з RSS місцевих ЗМІ (безкоштовно), Haiku лише відбирає топ-10
і пише короткі підписи. БЕЗ платного web_search → ~$0.02 за випуск.
Запускається в GitHub Actions (cron 19:00 Київ).
"""
import datetime
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zoneinfo
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import anthropic

KYIV = zoneinfo.ZoneInfo("Europe/Kyiv")
NOW = datetime.datetime.now(KYIV)
if os.environ.get("FORCE_RUN") != "1" and NOW.hour != 19:
    print(f"Київський час {NOW:%H:%M} != 19:xx — пропускаю запуск.")
    sys.exit(0)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@zakarpattianews")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
MIN_ITEMS = 5

FEEDS = [
    "https://zakarpattya.net.ua/rss.xml",
    "https://goloskarpat.info/rss.xml",
    "https://karpatnews.in.ua/feed/",
    "https://transkarpatia.net/rss.xml",
    "https://www.0312.ua/rss",
]

UA_MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня",
             "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
DATE_UA = f"{NOW.day} {UA_MONTHS[NOW.month - 1]} {NOW.year}"
_USAGE = {"in": 0, "out": 0}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def collect():
    """Збирає свіжі новини з RSS-стрічок (за останні ~36 год)."""
    cutoff = NOW - datetime.timedelta(hours=36)
    items, seen = [], set()
    for feed in FEEDS:
        try:
            root = ET.fromstring(fetch(feed))
        except Exception as e:
            print(f"  feed fail: {feed} ({e})")
            continue
        cnt = 0
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            desc = strip_html(it.findtext("description") or "")[:300]
            if not (title and link.startswith("http")):
                continue
            pub = it.findtext("pubDate")
            if pub:
                try:
                    dt = parsedate_to_datetime(pub)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=KYIV)
                    if dt < cutoff:
                        continue
                except Exception:
                    pass
            key = title.lower()[:60]
            if key in seen:
                continue
            seen.add(key)
            items.append({"title": title, "url": link, "desc": desc})
            cnt += 1
            if cnt >= 12:  # ліміт на одне джерело
                break
    return items


PROMPT_TMPL = """Ты — редактор Telegram-канала новостей Закарпатья. Сегодня: {DATE}.
Ниже — свежие новости из RSS местных СМИ (реальные заголовки и ссылки).
Отбери 10 САМЫХ ВАЖНЫХ и РАЗНОПЛАНОВЫХ за последние сутки. Баланс категорий:
политика/ЕС, экономика, энергетика/ЖКХ/тарифы, война/безопасность, происшествия (не более 2-3),
общество, культура/туризм. Убери дубли одного события, рекламу, мелочёвку (гороскопы, магнитные бури), кликбейт.
Для каждой: emoji, заголовок (укр., на основе исходного), 2-3 предложения описания на украинском
(по сути новости, нейтрально, без воды), и url СТРОГО ИЗ СПИСКА (не выдумывай и не меняй ссылку).
Верни СТРОГО JSON-массив между маркерами, без иного текста:
===JSON_START===
[{{"emoji":"🇪🇺","title":"...","desc":"...","url":"https://..."}}]
===JSON_END===

НОВОСТИ:
{ITEMS}"""


def curate(items):
    listing = "\n".join(f"- {i['title']} :: {i['desc']} :: {i['url']}" for i in items)
    prompt = PROMPT_TMPL.replace("{DATE}", DATE_UA).replace("{ITEMS}", listing)
    resp = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        _USAGE["in"] += resp.usage.input_tokens
        _USAGE["out"] += resp.usage.output_tokens
    except Exception:
        pass
    txt = "".join(b.text for b in resp.content if b.type == "text")
    m = re.search(r"\[.*\]", txt, re.DOTALL)
    out = json.loads(m.group(0)) if m else []
    src_urls = {i["url"] for i in items}
    return [o for o in out if isinstance(o, dict) and o.get("url") in src_urls][:10]


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
              f"📰 <i>Джерела: {', '.join(d for d in domains if d)[:120]}</i>",
              "🤖 <i>Автоматичний дайджест • щодня о 19:00</i>"]
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
    print("[1/3] Збираю новини з RSS…")
    cands = collect()
    print(f"      кандидатів з RSS: {len(cands)}")
    if len(cands) < MIN_ITEMS:
        print(f"[skip] Замало новин у RSS ({len(cands)} < {MIN_ITEMS}) — пропускаю.")
        sys.exit(0)

    print(f"[2/3] Відбираю топ-10 ({MODEL})…")
    final = curate(cands)
    cost = _USAGE["in"] / 1e6 * 1 + _USAGE["out"] / 1e6 * 5  # Haiku $1/$5
    print(f"      фінал: {len(final)}")
    print(f"[usage] input={_USAGE['in']} output={_USAGE['out']}  ~${cost:.4f}/run (Haiku, RSS, без web_search)")
    if len(final) < MIN_ITEMS:
        print(f"[skip] Після відбору замало ({len(final)} < {MIN_ITEMS}) — пропускаю.")
        sys.exit(0)

    print("[3/3] Публікую…")
    if not tg_send(render(final)):
        sys.exit("Telegram error — пост не опубліковано.")
    print("OK ✅")
