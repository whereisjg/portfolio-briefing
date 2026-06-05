#!/usr/bin/env python3
"""
포트폴리오 일일 브리핑 — GitHub Actions 자동화용
QLD, SSO, USD 실시간 가격 + 뉴스 → 마크다운 + 카카오톡(PlayMCP)
"""

import json
import os
from datetime import datetime
import pytz
import requests

KST = pytz.timezone("Asia/Seoul")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "").strip()
TICKERS = ["QLD", "SSO", "USD"]

def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def call_claude_api(prompt, system_prompt=None, mcp_servers=None, tools=None):
    """Claude API 호출"""
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
    if mcp_servers:
        payload["mcp_servers"] = mcp_servers
    if tools:
        payload["tools"] = tools

    response = requests.post(url, json=payload, headers=headers)

    if response.status_code != 200:
        print(f"❌ API 에러: {response.status_code}")
        print(f"응답: {response.text[:300]}")

    response.raise_for_status()
    data = response.json()
    texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(texts)

def fetch_prices_and_news():
    """가격 + 뉴스 조회"""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    prompt = f"""Today is {today} KST. Get current prices and 3 recent news for QLD (ProShares Ultra QQQ), SSO (ProShares Ultra S&P500), USD (ProShares Ultra Semiconductors) ETFs.
Respond ONLY as valid JSON (no markdown):
{{
  "prices": {{
    "QLD": {{"price": 150.0, "prev_close": 148.0, "chg_pct": 1.35}},
    "SSO": {{"price": 85.0, "prev_close": 84.0, "chg_pct": 1.19}},
    "USD": {{"price": 50.0, "prev_close": 49.5, "chg_pct": 1.01}}
  }},
  "news": {{
    "QLD": [{{"title_ko": "나스닥 상승", "source": "Reuters", "time": "10:00"}}, {{"title_ko": "기술주 강세", "source": "CNBC", "time": "09:30"}}, {{"title_ko": "AI 관련주 급등", "source": "Bloomberg", "time": "09:00"}}],
    "SSO": [{{"title_ko": "S&P500 신고점", "source": "MarketWatch", "time": "11:00"}}, {{"title_ko": "경기 회복 신호", "source": "WSJ", "time": "10:30"}}, {{"title_ko": "인플레이션 둔화", "source": "CNBC", "time": "09:30"}}],
    "USD": [{{"title_ko": "반도체주 급등", "source": "TechCrunch", "time": "11:30"}}, {{"title_ko": "NVIDIA 실적 호조", "source": "Reuters", "time": "10:00"}}, {{"title_ko": "칩 수급 개선", "source": "Bloomberg", "time": "09:00"}}]
  }},
  "insight": "시장 핵심 한 줄 요약",
  "actions": ["액션1", "액션2"],
  "kakao_summary": "200자 이내 브리핑 요약"
}}"""

    response = call_claude_api(prompt)
    clean = response.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

def build_markdown(data):
    """마크다운 브리핑 생성"""
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
    md += f"## 💡 핵심\n{data.get('insight','—')}\n\n"
    md += "## 🎯 액션\n"
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

def send_kakao_via_playmcp(message):
    """PlayMCP를 통해 카카오톡 메모챗으로 전송"""
    print("\n📱 카카오톡 전송 중 (PlayMCP)...")

    response_text = call_claude_api(
        prompt=f"카카오톡 나에게 메시지 보내기:\n\n{message}",
        system_prompt="You are a helpful assistant. Send the message via KakaoTalk memo chat.",
        mcp_servers=[{
            "type": "url",
            "url": "https://playmcp.kakao.com/mcp",
            "name": "playmcp"
        }]
    )

    print(f"📨 전송 결과: {response_text[:100]}")
    return response_text

def build_kakao_message(data):
    """카카오톡용 메시지 생성 (200자 이내)"""
    today = datetime.now(KST).strftime("%m/%d")
    prices = data.get("prices", {})

    qld_chg = prices.get("QLD", {}).get("chg_pct", 0)
    sso_chg = prices.get("SSO", {}).get("chg_pct", 0)
    usd_chg = prices.get("USD", {}).get("chg_pct", 0)

    summary = data.get("kakao_summary", data.get("insight", ""))

    msg = f"[포트폴리오 브리핑 {today}]\n"
    msg += f"QLD {qld_chg:+.2f}% | SSO {sso_chg:+.2f}% | USD {usd_chg:+.2f}%\n\n"
    msg += summary[:150]

    return msg

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
        filename = save_markdown(md_content)

        print("\n[4/4] 카카오톡 전송...")
        kakao_msg = build_kakao_message(data)
        print(f"📝 메시지 내용:\n{kakao_msg}")
        send_kakao_via_playmcp(kakao_msg)

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
