#!/usr/bin/env python3
import json
import os
from datetime import datetime
import pytz
import requests

KST = pytz.timezone("Asia/Seoul")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "").strip()
TICKERS = ["QLD", "SSO", "USD"]

def call_claude_api(prompt):
    if not CLAUDE_API_KEY:
        print("❌ CLAUDE_API_KEY 없음!")
        raise ValueError("API 키 미설정")
    
    print(f"✅ API 키 감지: {CLAUDE_API_KEY[:15]}...")
    
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": CLAUDE_API_KEY
    }
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2000,
        "system": "You are a financial assistant. Respond ONLY in valid JSON.",
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
    prompt = f"""Today is {today} KST. Get current prices and 3 recent news for QLD, SSO, USD ETFs.
Respond ONLY as JSON:
{{"prices": {{"QLD": {{"price": 150, "prev_close": 148, "chg_pct": 1.35}}, "SSO": {{"price": 85, "prev_close": 84, "chg_pct": 1.19}}, "USD": {{"price": 50, "prev_close": 49.5, "chg_pct": 1.01}}}}, "news": {{"QLD": [{{"title_ko": "나스닥 상승", "source": "Reuters", "time": "10:00"}}], "SSO": [{{"title_ko": "S&P500 신고점", "source": "CNBC", "time": "11:00"}}], "USD": [{{"title_ko": "반도체주 급등", "source": "Bloomberg", "time": "11:30"}}]}}, "insight": "시장 강세", "actions": ["수익 실현"], "kakao_summary": "포트폴리오 요약"}}"""
    
    response = call_claude_api(prompt)
    clean = response.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

def build_markdown(data):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    md = f"# 📈 포트폴리오 일일 브리핑\n\n> {today} KST\n\n"
    md += "## 💰 가격 요약\n\n| 종목 | 현재가 | 전일비 |\n|------|--------|--------|\n"
    for t in ["QLD", "SSO", "USD"]:
        p = data.get("prices", {}).get(t)
        if p:
            chg = p.get("chg_pct", 0)
            md += f"| {t} | ${p.get('price', 0):.2f} | {chg:+.2f}% |\n"
    md += "\n## 📰 종목별 뉴스\n\n"
    for t in ["QLD", "SSO", "USD"]:
        md += f"### {t}\n"
        news_list = data.get("news", {}).get(t, [])
        for i, n in enumerate(news_list[:3], 1):
            md += f"{i}. {n.get('title_ko', '—')} *({n.get('source', '')})*\n"
        md += "\n"
    md += f"## 💡 핵심\n{data.get('insight', '—')}\n\n"
    md += "## 🎯 액션\n"
    for action in data.get("actions", []):
        md += f"- {action}\n"
    return md

def save_markdown(content):
    today = datetime.now(KST).strftime("%Y%m%d")
    filename = f"briefing_{today}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ 저장: {filename}")
    return filename

def main():
    print(f"\n🚀 시작 — {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        data = fetch_prices_and_news()
        md = build_markdown(data)
        save_markdown(md)
        print("✨ 완료!")
        return 0
    except Exception as e:
        print(f"❌ 오류: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())
