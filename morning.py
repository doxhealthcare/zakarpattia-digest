#!/usr/bin/env python3
"""Ранковий пост 09:00 Київ: курс ПриватБанку + погода Ужгород + бензин БРСМ -> @zakarpattianews.

Курс і погода — відкриті безкоштовні API (надійно). Бензин БРСМ — маленький
Haiku+web_search запит (~$0.01/день) з graceful-fallback: якщо не вдалося,
пост все одно виходить з курсом і погодою.
Запускається в GitHub Actions (cron 09:00 Київ).
"""
import datetime
import json
import os
import re
import sys
import urllib.request
import zoneinfo

KYIV = zoneinfo.ZoneInfo("Europe/Kyiv")
NOW = datetime.datetime.now(KYIV)
if os.environ.get("FORCE_RUN") != "1" and NOW.hour != 9:
    print(f"Київський час {NOW:%H:%M} != 9:xx — пропускаю запуск.")
    sys.exit(0)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@zakarpattianews")
FUEL_MODEL = os.environ.get("FUEL_MODEL", "claude-haiku-4-5")

UA_MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня",
             "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
DATE_UA = f"{NOW.day} {UA_MONTHS[NOW.month - 1]} {NOW.year}"

WMO = {0: "☀️ ясно", 1: "🌤 переважно ясно", 2: "⛅ мінлива хмарність", 3: "☁️ хмарно",
       45: "🌫 туман", 48: "🌫 паморозь", 51: "🌦 мряка", 53: "🌦 мряка", 55: "🌦 мряка",
       61: "🌧 дощ", 63: "🌧 дощ", 65: "🌧 сильний дощ", 66: "🌧 крижаний дощ", 67: "🌧 крижаний дощ",
       71: "🌨 сніг", 73: "🌨 сніг", 75: "❄️ сильний сніг", 77: "🌨 сніжна крупа",
       80: "🌦 зливи", 81: "🌦 зливи", 82: "⛈ сильні зливи", 85: "🌨 снігопад", 86: "❄️ снігопад",
       95: "⛈ гроза", 96: "⛈ гроза з градом", 99: "⛈ гроза з градом"}


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def privat_rates():
    data = get_json("https://api.privatbank.ua/p24api/pubinfo?json&exchange&coursid=5")
    out = {}
    for it in data:
        if it.get("ccy") in ("USD", "EUR"):
            out[it["ccy"]] = (float(it["buy"]), float(it["sale"]))
    return out


def weather():
    d = get_json("https://api.open-meteo.com/v1/forecast?latitude=48.62&longitude=22.29"
                 "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m"
                 "&hourly=temperature_2m,precipitation_probability"
                 "&daily=precipitation_probability_max,sunrise,sunset,weather_code"
                 "&wind_speed_unit=ms&timezone=Europe%2FKyiv")
    c = d["current"]
    day = d["daily"]
    hours = d["hourly"]["time"]
    today = day["time"][0]

    def temp_at(hh):
        key = f"{today}T{hh}"
        return round(d["hourly"]["temperature_2m"][hours.index(key)]) if key in hours else None

    def hm(iso):  # "2026-06-14T05:27" -> "05:27"
        return iso.split("T")[1] if "T" in iso else iso

    return {
        "now": round(c["temperature_2m"]),
        "feels": round(c["apparent_temperature"]),
        "hum": round(c["relative_humidity_2m"]),
        "wind": round(c["wind_speed_10m"]),
        "cond": WMO.get(c["weather_code"], "🌡"),
        "morn": temp_at("09:00"),
        "day": temp_at("15:00"),
        "eve": temp_at("21:00"),
        "precip": day["precipitation_probability_max"][0],
        "sunrise": hm(day["sunrise"][0]),
        "sunset": hm(day["sunset"][0]),
    }


def fuel_brsm():
    """Сьогоднішні ціни БРСМ через Haiku+web_search. Повертає {} при будь-якій помилці."""
    import anthropic
    client = anthropic.Anthropic()
    prompt = ("С помощью web_search найди СЕГОДНЯШНИЕ цены на топливо сети АЗС «БРСМ-Нафта» в Украине. "
              "Источники: index.minfin.com.ua/ua/markets/fuel/tm/brsmnafta/, auto.ria.com/uk/toplivo/brsm-nafta/, "
              "agrarii-razom.com.ua/fuel/azs/brsm-nafta. Бери средние/актуальные цены сети. "
              "Верни СТРОГО JSON между маркерами, без иного текста (числа грн/л как строки; если нет — пустая строка):\n"
              '===J===\n{"a95":"00.00","dp":"00.00","gas":"00.00"}\n===J===')
    resp = client.messages.create(
        model=FUEL_MODEL, max_tokens=600,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": prompt}],
    )
    txt = "".join(b.text for b in resp.content if b.type == "text")
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    return json.loads(m.group(0)) if m else {}


def build():
    lines = ["🌅 <b>Доброго ранку, Закарпаття!</b>", f"🗓 {DATE_UA}", ""]
    try:
        r = privat_rates()
        lines.append("💵 <b>Курс ПриватБанку</b> (готівка, грн — купівля / продаж):")
        if "USD" in r:
            lines.append(f"🇺🇸 USD: {r['USD'][0]:.2f} / {r['USD'][1]:.2f}")
        if "EUR" in r:
            lines.append(f"🇪🇺 EUR: {r['EUR'][0]:.2f} / {r['EUR'][1]:.2f}")
        lines.append("")
    except Exception as e:
        print("rates failed:", e)
    try:
        w = weather()
        lines.append("🌤 <b>Погода, Ужгород</b>")
        lines.append(f"🌡 Зараз {w['now']}°, відчувається {w['feels']}° · {w['cond']}")
        parts = []
        if w["morn"] is not None:
            parts.append(f"вранці {w['morn']}°")
        if w["day"] is not None:
            parts.append(f"вдень {w['day']}°")
        if w["eve"] is not None:
            parts.append(f"ввечері {w['eve']}°")
        if parts:
            lines.append("📊 " + " · ".join(parts))
        lines.append(f"☔ Дощ до {w['precip']}% · 💧 вологість {w['hum']}% · 💨 вітер {w['wind']} м/с")
        lines.append(f"🌅 Схід {w['sunrise']} · 🌇 Захід {w['sunset']}")
        lines.append("")
    except Exception as e:
        print("weather failed:", e)
    try:
        f = fuel_brsm()
        parts = []
        if f.get("a95"):
            parts.append(f"А-95 {f['a95']}")
        if f.get("dp"):
            parts.append(f"ДП {f['dp']}")
        if f.get("gas"):
            parts.append(f"Газ {f['gas']}")
        if parts:
            lines.append("⛽ <b>Пальне БРСМ</b> (грн/л): " + " · ".join(parts))
            lines.append("")
    except Exception as e:
        print("fuel failed:", e)
    lines.append("🤖 <i>Курс — ПриватБанк, погода — Open-Meteo, пальне — БРСМ</i>")
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
    text = build()
    if not tg_send(text):
        sys.exit("Telegram error — ранковий пост не опубліковано.")
    print("OK ✅")
