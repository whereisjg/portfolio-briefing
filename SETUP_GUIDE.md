# 📈 포트폴리오 브리핑 자동화 — 완성 가이드

## 개요
QLD, SSO, USD, 426030(TIMEFOLIO 나스닥100액티브) 4개 종목의 매일 아침 7시 자동 브리핑 시스템

---

## 아키텍처
```
cron-job.org (매일 07:00 KST)
    → GitHub API 트리거
        → GitHub Actions 실행
            → Claude Sonnet 4.6 (웹검색)
                → 텔레그램 전송 + GitHub md 저장
```

---

## 저장소
`whereisjg/portfolio-briefing-kakao`

---

## 파일 구조
```
portfolio-briefing-kakao/
├── .github/
│   └── workflows/
│       └── briefing.yml        # GitHub Actions 워크플로우
├── portfolio_briefing.py       # 메인 스크립트
├── briefing_YYYYMMDD.md        # 매일 자동 생성 브리핑
└── README.md
```

---

## GitHub Secrets
| Name | 설명 |
|------|------|
| `CLAUDE_API_KEY` | Anthropic API 키 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 (`8884538180:AAE9arH2ZxzTr1fRK4CDJpIbUpzpFxZ_cuU`) |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID (`8582253187`) |

---

## briefing.yml 전체 내용
```yaml
name: Daily Portfolio Briefing

on:
  schedule:
    - cron: '0 23 * * 2-6'
  workflow_dispatch:

jobs:
  briefing:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install requests pytz
      - name: Run briefing
        env:
          CLAUDE_API_KEY: ${{ secrets.CLAUDE_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python portfolio_briefing.py
      - name: Commit and push
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          git config user.name "Portfolio Bot"
          git config user.email "bot@portfolio.local"
          git add briefing_*.md 2>/dev/null || true
          git commit -m "📈 Daily briefing - $(date +%Y-%m-%d)" || true
          git push
```

---

## portfolio_briefing.py 핵심 설정
```python
# 모델
"model": "claude-sonnet-4-6"

# 웹 검색 (max_uses: 5로 비용 절약)
"tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

# 종목
TICKERS = ["QLD", "SSO", "USD", "426030"]

# 뉴스: 종목당 2개
```

---

## cron-job.org 설정
- **URL**: `https://api.github.com/repos/whereisjg/portfolio-briefing-kakao/actions/workflows/briefing.yml/dispatches`
- **Method**: POST
- **Headers**:
  - `Authorization`: `Bearer {GitHub Personal Access Token}`
  - `Content-Type`: `application/json`
- **Body**: `{"ref":"main"}`
- **Timezone**: Asia/Seoul
- **Schedule**: 매일 07:00

### GitHub Personal Access Token 설정
- Fine-grained token
- Repository: `portfolio-briefing-kakao`
- Permissions: Actions → Read and write

---

## 텔레그램 봇
- 봇: `@shportfolio_briefing_bot`
- Chat ID: `8582253187`

---

## 스케줄
- **화~토 아침 7시** KST 자동 실행
- 월요일: 주말 미국 장 휴장으로 제외
- 일요일: 휴식

---

## 비용
| 항목 | 비용 |
|------|------|
| 모델 | Claude Sonnet 4.6 |
| 1회 실행 | ~$0.12 (약 170원) |
| 월간 (화~토 × 22일) | ~$2.6 |

---

## 텔레그램 메시지 형식
```
📈 포트폴리오 브리핑 06/06

🔴 QLD $89.54 (-9.57%)
🔴 SSO $68.99 (-1.40%)
🔴 USD $104.36 (-10.61%)
🟢 TIMEFOLIO나스닥100 ₩57,500 (+0.82%)

━━━━━━━━━━━━━━━
💡 오늘 시장 핵심 한 줄
━━━━━━━━━━━━━━━

🎯 오늘의 대응
  ▸ QLD: 대응전략
  ▸ SSO: 대응전략
  ▸ USD: 대응전략
  ▸ 426030: 대응전략

📰 오늘의 뉴스
QLD
• 뉴스 제목 (출처)
• 뉴스 제목 (출처)
...
```

---

## 트러블슈팅

### GitHub Actions cron이 자동 실행 안 될 때
→ cron-job.org가 매일 07:00에 workflow_dispatch로 트리거하므로 GitHub cron 무관

### JSON 파싱 에러
→ 모델을 claude-sonnet-4-6 으로 유지 (Haiku는 JSON 불안정)

### API 에러 400 (usage limits)
→ console.anthropic.com → Limits에서 일일 한도 확인/조정

### 텔레그램 전송 실패
→ TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID GitHub Secrets 확인

### 주가 $0.00 으로 나올 때
→ Claude 웹 검색이 실시간 데이터를 못 찾은 것
→ max_uses를 8로 늘려보거나 프롬프트 확인

---

## 시도했다가 포기한 것들
- **카카오톡**: PlayMCP 10분 일회용 토큰으로 자동화 불가
- **yfinance**: GitHub Actions 환경에서 403 에러
- **Alpha Vantage**: 무료 플랜 분당 5회 제한으로 연속 호출 시 실패
- **Claude Haiku**: JSON 형식 불안정, 영어 사과문 반환
