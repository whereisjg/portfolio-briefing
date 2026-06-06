#!/usr/bin/env python3
"""
포트폴리오 일일 브리핑 — GitHub Actions 자동화용
Alpha Vantage로 정확한 주가 + Claude 웹검색으로 뉴스/대응전략
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
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "").strip()
TICKERS = ["QLD", "SSO", "USD", "426030"]

def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def fetch_price_alpha_vantage(ticker):
    """Alpha Vantage로 미국 ETF 실시간 주가 조회"""
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHA_VANTAGE_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        quote = data.get("Global Quote", {})
        if not quote:
            print(f"⚠️ {ticker} 데이터 없음")
            return None
        price = float(quote.get("05. price", 0))
        prev_close = float(quote.get("08. previous close", 0))
        chg_pct = float(quote.get("10. change percent", "0%").replace("%", ""))
        print(f"✅ {ticker}: ${price:.2f} ({chg_pct:+.2f}%)")
        return {"price": price, "prev_close": prev_close, "chg_pct": chg_pct, "currency": "USD"}
    except Exception as e:
        print(f"❌ {ticker} 조회 실패: {e}")
        return None

def call_claude_with_search(prompt):
    """Claude API 웹검색으로 뉴스 + 한국 ETF 주가 조회"""
    if not CLAUDE_API_KEY:
        raise ValueError("❌ CLAUDE_API_KEY 환경변수 미설정!")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 3000,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
        "system": """You are a financial data assistant. Your ONLY job is to return a JSON object.
CRITICAL RULES:
1. You MUST always respond with ONLY a valid JSON object. No other text.
2. NEVER apologize or explain. NEVER write prose or markdown.
3. If you cannot find exact data, use the most recent data available.
4. The response must start with { and end with }. Nothing else.""",
        "messages": [{"role": "user", "content": prompt}]
    }

    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()
    texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(texts)

def fetch_news_and_krw(us_prices):
    """Claude로 뉴스 + 426030 한국 ETF 주가 조회"""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    
    # 미국 ETF 가격 정보를 프롬프트에 포함
    price_info = ""
    for t in ["QLD", "SSO", "USD"]:
        p = us_prices.get(t)
        if p:
            price_info += f"- {t}: ${p['price']:.2f} ({p['chg_pct']:+.2f}%)\n"

    prompt = f"""Today is {today} KST.

US ETF prices already retrieved:
{price_info}

Please search for:
1. 426030 주가 오늘 (TIMEFOLIO 나스닥100액티브 KRX, price in KRW)
2. 2 recent news headlines for QLD, SSO, USD, 426030 (translate to Korean)
3. Market insight and action strategy for each ticker

Return ONLY this JSON:
{{
  "426030": {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "currency": "KRW"}},
  "news": {{
    "QLD":    [{{"title_ko": "뉴스제목", "source": "출처"}}, {{"title_ko": "뉴스제목", "source": "출처"}}],
    "SSO":    [{{"title_ko": "뉴스제목", "source": "출처"}}, {{"title_ko": "뉴스제목", "source": "출처"}}],
    "USD":    [{{"title_ko": "뉴스제목", "source": "출처"}}, {{"title_ko": "뉴스제목", "source": "출처"}}],
    "426030": [{{"title_ko": "뉴스제목", "source": "출처"}}, {{"title_ko": "뉴스제목", "source": "출처"}}]
  }},
  "insight": "오늘 시장 핵심 한 줄 (20자 이내)",
  "actions": ["QLD: 대응전략", "SSO: 대응전략", "USD: 대응전략", "426030: 대응전략"]
}}"""

    response = call_claude_with_search(prompt)
    clean = response.replace("```json", "").replace("```", "").strip()
    start = clean.find("{")
    end = clean.rfind("}") + 1
    if start >= 0 and end > start:
        clean = clean[start:end]
    return json.loads(clean)

def fetch_all_data():
    """미국 ETF는 Alpha Vantage, 한국 ETF+뉴스는 Claude"""
    print("📊 Alpha Vantage로 미국 ETF 주가 조회 중...")
    us_prices = {}
    for ticker in ["QLD", "SSO", "USD"]:
        price_data = fetch_price_alpha_vantage(ticker)
        if price_data:
            us_prices[ticker] = price_data

    print("🔍 Claude로 뉴스 + 한국 ETF 조회 중...")
    claude_data = fetch_news_and_krw(us_prices)

    # 데이터 합치기
    prices = {**us_prices, "426030": claude_data.get("426030", {"price": 0, "prev_close": 0, "chg_pct": 0, "currency": "KRW"})}
    
    return {
        "prices": prices,
        "news": claude_data.get("news", {}),
        "insight": claude_data.get("insight", "—"),
        "actions": claude_data.get("actions", [])
    }

def build_content(data):
    today_full = datetime.now(KST).strftime("%Y-%m-%d")
    today_short = datetime.now(KST).strftime("%m/%d")
    prices = data.get("prices", {})

    price_lines = []
    for t in TICKERS:
        p = prices.get(t, {})
        chg = p.get("chg_pct", 0)
        price = p.get("price", 0)
        currency = p.get("currency", "USD")
        symbol = "₩" if currency == "KRW" else "$"
        price_str = f"{symbol}{price:,.0f}" if currency == "KRW" else f"{symbol}{price:.2f}"
        emoji = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
        name = "TIMEFOLIO나스닥100" if t == "426030" else t
        price_lines.append(f"{emoji} *{name}* {price_str} ({chg:+.2f}%)")

    news_lines = []
    for t in TICKERS:
        name = "TIMEFOLIO나스닥100 (426030)" if t == "426030" else t
        news_lines.append(f"\n*{name}*")
        for n in data.get("news", {}).get(t, [])[:2]:
            news_lines.append(f"• {n.get('title_ko','—')} _({n.get('source','')})_")

    action_lines = []
    for a in data.get("actions", []):
        action_lines.append(f"  ▸ {a}")

    # 텔레그램 메시지
    telegram = f"📈 *포트폴리오 브리핑 {today_short}*\n\n"
    telegram += "\n".join(price_lines)
    telegram += f"\n\n━━━━━━━━━━━━━━━\n"
    telegram += f"💡 *{data.get('insight', '—')}*\n"
    telegram += f"━━━━━━━━━━━━━━━\n\n"
    telegram += "🎯 *오늘의 대응*\n"
    telegram += "\n".join(action_lines)
    telegram += "\n\n📰 *오늘의 뉴스*"
    telegram += "\n".join(news_lines)

    # 마크다운 파일
    md = f"# 📈 포트폴리오 일일 브리핑\n\n> {today_full} KST\n\n"
    md += "## 💰 가격 요약\n\n| 종목 | 현재가 | 전일비 |\n|------|--------|--------|\n"
    for t in TICKERS:
        p = prices.get(t, {})
        chg = p.get("chg_pct", 0)
        price = p.get("price", 0)
        currency = p.get("currency", "USD")
        symbol = "₩" if currency == "KRW" else "$"
        price_str = f"{symbol}{price:,.0f}" if currency == "KRW" else f"{symbol}{price:.2f}"
        name = "TIMEFOLIO 나스닥100액티브" if t == "426030" else t
        md += f"| {name} | {price_str} | {chg:+.2f}% |\n"
    md += "\n## 📰 종목별 뉴스\n\n"
    for t in TICKERS:
        name = "TIMEFOLIO 나스닥100액티브 (426030)" if t == "426030" else t
        md += f"### {name}\n"
        for i, n in enumerate(data.get("news", {}).get(t, [])[:2], 1):
            md += f"{i}. {n.get('title_ko','—')} *({n.get('source','')})*\n"
        md += "\n"
    md += f"## 💡 오늘의 핵심\n{data.get('insight','—')}\n\n"
    md += "## 🎯 오늘의 액션\n"
    for a in data.get("actions", []):
        md += f"- {a}\n"

    return telegram, md

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

def main():
    print(f"\n{'='*50}")
    print(f"🚀 포트폴리오 브리핑 — {now_kst()}")
    print(f"{'='*50}")

    try:
        print("\n[1/4] 주가 및 뉴스 수집...")
        data = fetch_all_data()

        print("\n[2/4] 콘텐츠 생성...")
        telegram_msg, md_content = build_content(data)

        print("\n[3/4] 파일 저장...")
        save_markdown(md_content)

        print("\n[4/4] 텔레그램 전송...")
        print(f"📝 메시지 미리보기:\n{telegram_msg}\n")
        send_telegram(telegram_msg)

        print(f"\n{'='*50}")
        print("✨ 모든 작업 완료!")
        print(f"{'='*50}\n")
        return 0

    except Exception as e:
        print(f"\n❌ 오류: {e}")
        error_msg = f"⚠️ *포트폴리오 브리핑 오류*\n\n`{str(e)[:200]}`"
        send_telegram(error_msg)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())
