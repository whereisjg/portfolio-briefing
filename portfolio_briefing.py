#!/usr/bin/env python3
"""
포트폴리오 일일 브리핑 — GitHub Actions 자동화용
QLD, SSO, USD, 426030 실시간 가격 + 뉴스 → 마크다운 + 텔레그램 전송
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
TICKERS = ["QLD", "SSO", "USD", "426030"]

def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def call_claude_with_search(prompt):
    if not CLAUDE_API_KEY:
        raise ValueError("❌ CLAUDE_API_KEY 환경변수 미설정!")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4000,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "system": "You are a financial assistant. Search the web for real-time ETF prices and news. After searching, respond ONLY with valid JSON, no markdown fences, no preamble.",
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
    prompt = f"""Today is {today} KST.

Search the web RIGHT NOW for the current prices of these ETFs:
1. QLD (ProShares Ultra QQQ) - search "QLD stock price today"
2. SSO (ProShares Ultra S&P500) - search "SSO stock price today"
3. USD (ProShares Ultra Semiconductors) - search "USD ETF price today"
4. 426030 (TIMEFOLIO 미국나스닥100액티브, KRX Korea ETF) - search "426030 주가 오늘" (price in KRW ₩)

Also search for 3 recent news headlines for each and translate to Korean.

After searching, respond ONLY with this JSON (no markdown, no extra text):
{{
  "prices": {{
    "QLD":    {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "currency": "USD"}},
    "SSO":    {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "currency": "USD"}},
    "USD":    {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "currency": "USD"}},
    "426030": {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "currency": "KRW"}}
  }},
  "news": {{
    "QLD":    [{{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}, {{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}, {{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}],
    "SSO":    [{{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}, {{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}, {{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}],
    "USD":    [{{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}, {{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}, {{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}],
    "426030": [{{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}, {{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}, {{"title_ko": "뉴스제목", "source": "출처", "time": "시간"}}]
  }},
  "insight": "오늘 시장 핵심 한 줄 (20자 이내)",
  "actions": ["오늘 가격 흐름과 뉴스를 바탕으로 각 종목별 구체적인 대응 전략 1줄씩. 예: 추가매수 / 관망 / 부분익절 / 비중축소 등 행동 중심으로"],
  "summary": "오늘 하루 각 종목에 일어난 주요 뉴스/이벤트를 종목별로 한 줄씩 요약. 과거 수익률이나 분석보다는 오늘 실제로 있었던 일 위주로"
}}"""

    print("🔍 웹 검색으로 실시간 데이터 수집 중...")
    response = call_claude_with_search(prompt)

    clean = response.replace("```json", "").replace("```", "").strip()
    start = clean.find("{")
    end = clean.rfind("}") + 1
    if start >= 0 and end > start:
        clean = clean[start:end]

    data = json.loads(clean)
    print("✅ 데이터 수집 완료")
    return data

def build_markdown(data):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    md = f"# 📈 포트폴리오 일일 브리핑\n\n> {today} KST\n\n"
    md += "## 💰 가격 요약\n\n| 종목 | 현재가 | 전일비 |\n|------|--------|--------|\n"
    for t in TICKERS:
        p = data.get("prices", {}).get(t)
        if p:
            chg = p.get("chg_pct", 0)
            currency = p.get("currency", "USD")
            symbol = "₩" if currency == "KRW" else "$"
            price = p.get("price", 0)
            price_str = f"{symbol}{price:,.0f}" if currency == "KRW" else f"{symbol}{price:.2f}"
            name = "TIMEFOLIO 나스닥100액티브" if t == "426030" else t
            md += f"| {name} | {price_str} | {chg:+.2f}% |\n"
    md += "\n## 📰 종목별 뉴스\n\n"
    for t in TICKERS:
        name = "TIMEFOLIO 나스닥100액티브 (426030)" if t == "426030" else t
        md += f"### {name}\n"
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
        print(f"❌ 전송 실패: {response.status_code} — {response.text[:200]}")
        return False

def build_telegram_message(data):
    today = datetime.now(KST).strftime("%m/%d")
    prices = data.get("prices", {})

    lines = [f"📈 *포트폴리오 브리핑 {today}*\n"]

    for t in TICKERS:
        p = prices.get(t, {})
        chg = p.get("chg_pct", 0)
        price = p.get("price", 0)
        currency = p.get("currency", "USD")
        symbol = "₩" if currency == "KRW" else "$"
        price_str = f"{symbol}{price:,.0f}" if currency == "KRW" else f"{symbol}{price:.2f}"
        emoji = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
        name = "TIMEFOLIO나스닥100" if t == "426030" else t
        lines.append(f"{emoji} *{name}* {price_str} ({chg:+.2f}%)")

    lines.append("\n━━━━━━━━━━━━━━━")
    lines.append(f"💡 *{data.get('insight', '—')}*")
    lines.append("━━━━━━━━━━━━━━━\n")

    lines.append("🎯 *오늘의 대응*")
    for a in data.get("actions", []):
        lines.append(f"  ▸ {a}")
    lines.append("")

    summary = data.get("summary", "")
    if summary:
        lines.append("📰 *오늘의 뉴스*")
        sentences = summary.replace("。", ".").split(". ")
        for s in sentences:
            s = s.strip()
            if s:
                if not s.endswith("."):
                    s += "."
                lines.append(f"• {s}\n")

    return "\n".join(lines)

def main():
    print(f"\n{'='*50}")
    print(f"🚀 포트폴리오 브리핑 — {now_kst()}")
    print(f"{'='*50}")

    try:
        print("\n[1/4] 실시간 가격 및 뉴스 수집...")
        data = fetch_prices_and_news()

        print("\n[2/4] 마크다운 생성...")
        md_content = build_markdown(data)

        print("\n[3/4] 파일 저장...")
        save_markdown(md_content)

        print("\n[4/4] 텔레그램 전송...")
        telegram_msg = build_telegram_message(data)
        print(f"📝 메시지 미리보기:\n{telegram_msg}\n")
        send_telegram(telegram_msg)

        print(f"\n{'='*50}")
        print("✨ 모든 작업 완료!")
        print(f"{'='*50}\n")
        return 0

    except Exception as e:
        print(f"\n❌ 오류: {e}")
        # 오류 발생 시 텔레그램으로 알림
        error_msg = f"⚠️ *포트폴리오 브리핑 오류*\n\n`{str(e)[:200]}`\n\nGitHub Actions 로그를 확인하세요."
        send_telegram(error_msg)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())
