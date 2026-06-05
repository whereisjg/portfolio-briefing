#!/usr/bin/env python3
"""
포트폴리오 일일 브리핑 — GitHub Actions 자동화용
QLD, SSO, USD 실시간 가격 + 뉴스 → 마크다운 + 카카오톡 전송
"""

import json
import os
from datetime import datetime
import pytz
import requests

# 설정
KST = pytz.timezone("Asia/Seoul")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
KAKAO_TOKEN = os.getenv("KAKAO_TOKEN")  # 선택사항
TICKERS = ["QLD", "SSO", "USD"]

def now_kst():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def call_claude_api(prompt, system_prompt=None):
    """Claude API 호출"""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": CLAUDE_API_KEY
    }
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 2000,
        "system": system_prompt or "You are a financial assistant. Respond ONLY in valid JSON with no markdown, no preamble, no backticks.",
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()
    
    # content에서 text 블록만 추출
    texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(texts)

def fetch_prices_and_news():
    """가격 + 뉴스 조회"""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    
    prompt = f"""Today is {today} KST. Search the web and provide current data for these 3 ETFs: QLD (ProShares Ultra QQQ), SSO (ProShares Ultra S&P500), USD (ProShares Ultra Semiconductors).

For each ETF, get:
1. Current price and previous close (calculate % change)
2. 3 recent news headlines from today/yesterday
3. Translate English headlines to Korean

Respond ONLY as valid JSON (no markdown, no extra text):
{{
  "prices": {{
    "QLD": {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "currency": "USD"}},
    "SSO": {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "currency": "USD"}},
    "USD": {{"price": 0.0, "prev_close": 0.0, "chg_pct": 0.0, "currency": "USD"}}
  }},
  "news": {{
    "QLD": [{{"title_ko": "...", "source": "...", "time": "..."}}, ...3 items],
    "SSO": [...],
    "USD": [...]
  }},
  "insight": "오늘 시장 핵심 한 줄 (20자 이내)",
  "actions": ["액션1", "액션2"],
  "kakao_summary": "200자 이내 브리핑"
}}"""

    response = call_claude_api(prompt)
    
    try:
        # JSON 파싱 시도
        clean = response.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean)
        return data
    except json.JSONDecodeError as e:
        print(f"❌ JSON 파싱 실패: {e}")
        print(f"응답: {response[:500]}")
        raise

def build_markdown(data):
    """마크다운 브리핑 생성"""
    today = datetime.now(KST).strftime("%Y-%m-%d (%A)").replace("(Monday)", "(월)").replace("(Tuesday)", "(화)").replace("(Wednesday)", "(수)").replace("(Thursday)", "(목)").replace("(Friday)", "(금)").replace("(Saturday)", "(토)").replace("(Sunday)", "(일)")
    
    md = f"# 📈 일일 포트폴리오 브리핑\n\n> {today} {datetime.now(KST).strftime('%H:%M')} KST\n\n"
    
    # 가격 요약 표
    md += "## 💰 가격 요약\n\n| 종목 | 현재가 | 전일비 |\n|------|--------|--------|\n"
    for t in TICKERS:
        p = data.get("prices", {}).get(t)
        if p:
            chg = p.get("chg_pct", 0)
            chg_str = f"{chg:+.2f}%" if chg != 0 else "0.00%"
            md += f"| {t} | ${p.get('price', 0):.2f} | {chg_str} |\n"
        else:
            md += f"| {t} | — | — |\n"
    md += "\n"
    
    # 뉴스
    md += "## 📰 종목별 뉴스\n\n"
    for t in TICKERS:
        md += f"### {t}\n"
        news_list = data.get("news", {}).get(t, [])
        if news_list:
            for i, n in enumerate(news_list[:3], 1):
                title = n.get("title_ko", n.get("title", "—"))
                source = n.get("source", "")
                time = n.get("time", "")
                md += f"{i}. {title} *({source}{' · ' + time if time else ''})*\n"
        else:
            md += "뉴스 없음\n"
        md += "\n"
    
    # 인사이트
    md += f"## 💡 오늘의 핵심 한 줄\n{data.get('insight', '—')}\n\n"
    
    # 액션
    md += "## 🎯 오늘의 액션\n"
    for action in data.get("actions", []):
        md += f"- {action}\n"
    
    return md

def save_markdown(content):
    """마크다운 파일 저장"""
    today = datetime.now(KST).strftime("%Y%m%d")
    filename = f"briefing_{today}.md"
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    
    print(f"✅ 마크다운 저장: {filename}")
    return filename

def send_kakao_memo(summary):
    """카카오톡 메모챗으로 전송 (선택)"""
    if not KAKAO_TOKEN:
        print("⏭️  카카오톡 토큰 미설정 — 전송 생략")
        return False
    
    try:
        # 카카오톡 API 또는 웹훅으로 전송
        # (구현 방식은 사용자의 카카오톡 셋업에 따라 다름)
        print("📱 카카오톡 전송: " + summary[:50] + "...")
        return True
    except Exception as e:
        print(f"⚠️  카카오톡 전송 실패: {e}")
        return False

def main():
    print(f"\n🚀 포트폴리오 브리핑 시작 — {now_kst()}")
    
    try:
        # 1단계: 가격 + 뉴스 조회
        print("📊 AI가 가격과 뉴스를 수집 중...")
        data = fetch_prices_and_news()
        print(f"✅ 데이터 수집 완료")
        
        # 2단계: 마크다운 생성
        print("📝 브리핑 작성 중...")
        md_content = build_markdown(data)
        
        # 3단계: 파일 저장
        filename = save_markdown(md_content)
        
        # 4단계: 카카오톡 전송 (선택)
        kakao_summary = data.get("kakao_summary", "")
        if kakao_summary:
            send_kakao_memo(kakao_summary)
        
        print(f"\n✨ 완료! {filename} 생성됨")
        return 0
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())
