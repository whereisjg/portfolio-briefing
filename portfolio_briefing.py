#!/usr/bin/env python3
"""
포트폴리오 일일 브리핑 — GitHub Actions 자동화용
QLD, SSO, USD 실시간 가격 + 뉴스 → 마크다운 + 텔레그램 전송
"""

import json
import os
from datetime import datetime
import pytz
import requests

KST = pytz.timezone("Asia/Seoul")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TICKERS = ["QLD", "SSO", "USD"]

def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def call_claude_api(prompt, system_prompt=None):
    if not CLAUDE_API_KEY:
        raise ValueError("❌ CLAUDE_API_KEY 환경변수 미설정!")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    payload = {
        "model": "claude-opus-4-8",
        "max_tokens": 2000,
        "system": system_prompt or "You are a financial assistant. Respond ONLY in valid JSON with no markdown, no preamble, no backticks.",
        "messages": [{"role": "user", "content": prompt}]
    }

    response = requests.post(url, json=payload, headers=headers)

    if response.status_code != 200:
        print(f"❌ API 에러: {response.status_code}")
        print(f"응답: {response.text[:300]}")

    response.raise_for_status()
    data = response.json()
    texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(texts)

def fetch_prices_and_news():
    today = datetime.now(KST).strftime("%Y-%m-%d")
    prompt = f"""Today is {today} KST. Search and provide current market data for:
- QLD (ProShares Ultra QQQ, 2x Nasdaq)
- SSO (ProShares Ultra S&P500, 2x S&P500)
- USD (ProShares Ultra Semiconductors, 2x Semiconductors)

Get current price, previous close, % change, and 3 recent Korean-translated news headlines each.

Respond ONLY as valid JSON:
{{
  "prices": {{
    "QLD": {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0}},
    "SSO": {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0}},
    "USD": {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0}}
  }},
  "news": {{
    "QLD": [{{"title_ko": "...", "source": "...", "time": "..."}}],
    "SSO": [{{"title_ko": "...", "source": "...", "time": "..."}}],
    "USD": [{{"title_ko": "...", "source": "...", "time": "..."}}]
  }},
  "insight": "오늘 시장 핵심 한 줄 (20자 이내)",
  "actions": ["액션1", "액션2"],
  "summary": "텔레그램용 200자 이내 요약"
}}"""

    response = call_claude_api(prompt)
    clean = response.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

def build_markdown(data):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    md = f"# 📈 포트폴리오 일일 브리핑\n\n> {today} KST\n\n"
    md += "## 💰 가격 요약\n\n| 종목 | 현재가 | 전일비 |\n|------|--------|--------|\n"
    for t in TICKERS:
        p = data.get("prices", {}).get(t)
        if p:
            chg = p.get("chg_pct", 0)
            md += f"| {t} | ${p.get('price', 0):.2f} | {chg:+.2f}% |\n"
    md += "\n## 📰 종목별 뉴스\n\n"
    for t in TICKERS:
        md += f"### {t}\n"
        for i, n in enumerate(data.get("news", {}).get(t, [])[:3], 1):
            md += f"{i}. {n.get('title_ko','—')} *({n.get('source','')})*\n"
        md += "\n"
    md += f"## 💡 오늘의 핵심\n{data.get('insight','—')}\n\n"
    md += "## 🎯 오늘의 액션\n"
    for a in data.get("actions", []):
        md += f"- {a}\n"
    return md

def save_markdown(content):
    today = datetime.now(KST).strftime("%Y%m%d")
    filename = f"briefing_{today}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ 저장: {filename}")
    return filename

def send_telegram(message):
    """텔레그램으로 메시지 전송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  텔레그램 설정 없음 — 전송 생략")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    response = requests.post(url, json=payload)

    if response.status_code == 200:
        print("✅ 텔레그램 전송 완료!")
        return True
    else:
        print(f"❌ 텔레그램 전송 실패: {response.status_code}")
        print(response.text[:200])
        return False

def build_telegram_message(data):
    today = datetime.now(KST).strftime("%m/%d")
    prices = data.get("prices", {})

    lines = [f"📈 *포트폴리오 브리핑 {today}*\n"]

    for t in TICKERS:
        p = prices.get(t, {})
        chg = p.get("chg_pct", 0)
        price = p.get("price", 0)
        emoji = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
        lines.append(f"{emoji} *{t}* ${price:.2f} ({chg:+.2f}%)")

    lines.append(f"\n💡 {data.get('insight', '—')}")

    summary = data.get("summary", "")
    if summary:
        lines.append(f"\n{summary}")

    return "\n".join(lines)

def main():
    print(f"\n{'='*50}")
    print(f"🚀 포트폴리오 브리핑 — {now_kst()}")
    print(f"{'='*50}")

    try:
        print("\n[1/4] 가격 및 뉴스 수집...")
        data = fetch_prices_and_news()
        print("✅ 데이터 수집 완료")

        print("\n[2/4] 마크다운 생성...")
        md_content = build_markdown(data)

        print("\n[3/4] 파일 저장...")
        save_markdown(md_content)

        print("\n[4/4] 텔레그램 전송...")
        telegram_msg = build_telegram_message(data)
        print(f"📝 메시지:\n{telegram_msg}")
        send_telegram(telegram_msg)

        print(f"\n{'='*50}")
        print("✨ 모든 작업 완료!")
        print(f"{'='*50}\n")
        return 0

    except Exception as e:
        print(f"\n❌ 오류: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())
