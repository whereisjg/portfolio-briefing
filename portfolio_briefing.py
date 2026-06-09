#!/usr/bin/env python3
"""
Daily portfolio briefing for GitHub Actions.

This version does not call an AI API. It fetches prices directly from Yahoo
Finance, applies simple rule-based guidance, sends Telegram, and saves markdown.
"""

import os
from datetime import datetime

import pytz
import requests


KST = pytz.timezone("Asia/Seoul")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ASSETS = [
    {"ticker": "QLD", "symbol": "QLD", "name": "QLD", "display": "QLD", "currency": "USD"},
    {"ticker": "SSO", "symbol": "SSO", "name": "SSO", "display": "SSO", "currency": "USD"},
    {"ticker": "USD", "symbol": "USD", "name": "USD", "display": "USD", "currency": "USD"},
    {
        "ticker": "426030",
        "symbol": "426030.KS",
        "name": "TIMEFOLIO 나스닥100액티브",
        "display": "TIMEFOLIO나스닥100",
        "currency": "KRW",
    },
]


def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def fetch_quote(asset):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{asset['symbol']}"
    params = {"range": "5d", "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, params=params, headers=headers, timeout=20)
    response.raise_for_status()

    result = response.json()["chart"]["result"][0]
    meta = result["meta"]
    closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    closes = [close for close in closes if close is not None]

    price = closes[-1] if closes else meta.get("regularMarketPrice")
    previous_close = closes[-2] if len(closes) > 1 else None

    if price is None or previous_close in (None, 0):
        raise ValueError(f"가격 데이터를 찾지 못했습니다: {asset['ticker']}")

    chg_pct = ((price - previous_close) / previous_close) * 100
    return {
        **asset,
        "price": float(price),
        "prev_close": float(previous_close),
        "chg_pct": float(chg_pct),
    }


def fetch_prices():
    quotes = []
    errors = []

    for asset in ASSETS:
        try:
            quote = fetch_quote(asset)
            quotes.append(quote)
            print(f"OK {asset['ticker']}: {quote['price']} ({quote['chg_pct']:+.2f}%)")
        except Exception as exc:
            errors.append(f"{asset['ticker']}: {exc}")
            print(f"ERROR {asset['ticker']}: {exc}")

    if not quotes:
        raise ValueError("모든 가격 조회에 실패했습니다.")

    return quotes, errors


def format_price(item):
    if item["currency"] == "KRW":
        return f"₩{item['price']:,.0f}"
    return f"${item['price']:.2f}"


def movement_emoji(chg_pct):
    if chg_pct > 0:
        return "🟢"
    if chg_pct < 0:
        return "🔴"
    return "⚪"


def action_for(item):
    ticker = item["ticker"]
    chg = item["chg_pct"]

    if chg >= 5:
        return f"{ticker}: 하루 +5% 이상 급등. 변동성 확대 구간, 일부 차익실현 또는 손절선 점검."
    if chg >= 3:
        return f"{ticker}: 하루 +3% 이상 상승. 단기 과열 가능성, 추격 매수보다 관망."
    if chg >= 0.5:
        return f"{ticker}: 완만한 상승 흐름. 기존 비중 유지, 큰 조정 시 분할 매수 검토."
    if chg > -0.5:
        return f"{ticker}: 보합권 움직임. 방향성 확인 전까지 기존 전략 유지."
    if chg <= -5:
        return f"{ticker}: 하루 -5% 이상 급락. 손절선과 추가 매수 기준을 먼저 점검."
    if chg <= -3:
        return f"{ticker}: 하루 -3% 이상 하락. 성급한 물타기보다 지지선 확인."
    return f"{ticker}: 약세 흐름. 무리한 신규 매수보다 관망."


def market_summary(quotes):
    positives = [item for item in quotes if item["chg_pct"] > 0]
    negatives = [item for item in quotes if item["chg_pct"] < 0]
    surges = [item for item in quotes if item["chg_pct"] >= 3]
    drops = [item for item in quotes if item["chg_pct"] <= -3]

    if len(positives) == len(quotes) and surges:
        return "레버리지 ETF 전반 강세", "위험자산 선호", surges, drops
    if len(negatives) == len(quotes) and drops:
        return "레버리지 ETF 전반 약세", "위험 회피", surges, drops
    if len(positives) > len(negatives):
        return "상승 우위의 혼조세", "부분적 위험자산 선호", surges, drops
    if len(negatives) > len(positives):
        return "하락 우위의 혼조세", "방어적 대응 우위", surges, drops
    return "방향성 확인 구간", "중립", surges, drops


def build_content(quotes, errors):
    today_full = datetime.now(KST).strftime("%Y-%m-%d")
    today_short = datetime.now(KST).strftime("%m/%d")
    today_file = datetime.now(KST).strftime("%Y%m%d")
    headline, mood, surges, drops = market_summary(quotes)

    price_lines = [
        f"{movement_emoji(item['chg_pct'])} {item['display']} {format_price(item)} ({item['chg_pct']:+.2f}%)"
        for item in quotes
    ]
    action_lines = [f"  ▸ {action_for(item)}" for item in quotes]
    surge_text = ", ".join(item["ticker"] for item in surges) if surges else "없음"
    drop_text = ", ".join(item["ticker"] for item in drops) if drops else "없음"

    telegram_lines = [
        f"📈 포트폴리오 브리핑 {today_short}",
        "",
        *price_lines,
        "",
        "━━━━━━━━━━━━━━━",
        f"💡 오늘의 핵심: {headline}",
        "━━━━━━━━━━━━━━━",
        "",
        "🎯 오늘의 대응",
        *action_lines,
        "",
        "📊 변동성 체크",
        f"  ▸ 급등 종목: {surge_text}",
        f"  ▸ 급락 종목: {drop_text}",
        f"  ▸ 전체 분위기: {mood}",
        "",
        "📁 저장",
        f"  ▸ briefings/briefing_{today_file}.md",
    ]

    if errors:
        telegram_lines.extend(["", "⚠️ 데이터 확인 필요", *[f"  ▸ {error}" for error in errors]])

    md_lines = [
        "# 📈 포트폴리오 일일 브리핑",
        "",
        f"> {today_full} KST",
        "",
        "## 💰 가격 요약",
        "",
        "| 종목 | 현재가 | 전일비 |",
        "| --- | ---: | ---: |",
    ]

    for item in quotes:
        md_lines.append(f"| {item['name']} | {format_price(item)} | {item['chg_pct']:+.2f}% |")

    md_lines.extend(
        [
            "",
            "## 💡 오늘의 핵심",
            "",
            headline,
            "",
            "## 🎯 오늘의 대응",
            "",
            *[f"- {action_for(item)}" for item in quotes],
            "",
            "## 📊 변동성 체크",
            "",
            f"- 급등 종목: {surge_text}",
            f"- 급락 종목: {drop_text}",
            f"- 전체 분위기: {mood}",
        ]
    )

    if errors:
        md_lines.extend(["", "## ⚠️ 데이터 확인 필요", "", *[f"- {error}" for error in errors]])

    return "\n".join(telegram_lines), "\n".join(md_lines) + "\n"


def save_markdown(content):
    today = datetime.now(KST).strftime("%Y%m%d")
    output_dir = "briefings"
    os.makedirs(output_dir, exist_ok=True)

    filename = os.path.join(output_dir, f"briefing_{today}.md")
    with open(filename, "w", encoding="utf-8") as file:
        file.write(content)

    print(f"Saved: {filename}")
    return filename


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing. Skipping send.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    response = requests.post(url, json=payload, timeout=20)

    if response.status_code == 200:
        print("Telegram sent.")
        return True

    print(f"Telegram failed: {response.status_code} - {response.text[:200]}")
    return False


def main():
    print("=" * 50)
    print(f"Portfolio briefing - {now_kst()}")
    print("=" * 50)

    try:
        print("[1/4] Fetching prices...")
        quotes, errors = fetch_prices()

        print("[2/4] Building rule-based briefing...")
        telegram_msg, md_content = build_content(quotes, errors)

        print("[3/4] Saving markdown...")
        save_markdown(md_content)

        print("[4/4] Sending Telegram...")
        print(telegram_msg)
        send_telegram(telegram_msg)

        print("Done.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        send_telegram(f"⚠️ 포트폴리오 브리핑 오류\n\n`{str(exc)[:200]}`")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
