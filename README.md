# 📈 포트폴리오 자동 일일 브리핑

> Claude AI + GitHub Actions + Telegram으로 만든 완전 자동화 투자 브리핑 시스템

---

## 🎯 포트폴리오

| 종목 | 설명 | 시장 |
|------|------|------|
| **QLD** | ProShares Ultra QQQ (나스닥100 2x) | 미국 |
| **SSO** | ProShares Ultra S&P500 (S&P500 2x) | 미국 |
| **USD** | ProShares Ultra Semiconductors (반도체 2x) | 미국 |
| **426030** | TIMEFOLIO 미국나스닥100액티브 | 한국 |

---

## ✅ 기능

- 📊 **실시간 가격 조회** — 현재가 + 전일비 등락률
- 📰 **뉴스 요약** — 종목별 오늘 주요 뉴스 (한글)
- 🎯 **대응 전략** — AI가 제안하는 종목별 오늘의 액션
- 📱 **텔레그램 자동 전송** — 매일 아침 8시 자동 발송
- 💾 **GitHub 자동 저장** — 매일 브리핑 마크다운 파일 생성
- 🤖 **완전 자동화** — 컴퓨터 없이 365일 작동

---

## ⏰ 실행 스케줄

**화요일 ~ 토요일 아침 8시 KST** 자동 실행

| 요일 | 실행 | 이유 |
|------|------|------|
| 월요일 | ❌ | 주말 미국 장 휴장 |
| 화~금 | ✅ | 전일 미국 장 마감 데이터 |
| 토요일 | ✅ | 금요일 미국 장 마감 데이터 |
| 일요일 | ❌ | 휴식 |

---

## 📱 텔레그램 메시지 예시

```
📈 포트폴리오 브리핑 06/05

🔴 QLD $99.73 (-0.27%)
🔴 SSO $68.99 (-1.40%)
🔴 USD $104.36 (-10.61%)
🟢 TIMEFOLIO나스닥100 ₩58,985 (+0.82%)

━━━━━━━━━━━━━━━
💡 AI 반도체 급락, 지수형은 견조
━━━━━━━━━━━━━━━

🎯 오늘의 대응
  ▸ QLD: 신고가 부근, 추격매수보다 관망
  ▸ SSO: 조정 시 분할매수 고려
  ▸ USD: 급락 변동성 확대, 단기 비중축소
  ▸ 426030: 신고가 급등, 일부 차익실현

📰 오늘의 뉴스
• QLD: 엔비디아 AI PC 공개 호재로 나스닥100 랠리.
• SSO: S&P500이 5거래일 연속 최고치 후 조정.
• USD: 반도체 차익실현으로 10% 급락.
• 426030: AI 인프라 비중 효과로 신고가 경신.
```

---

## 🛠️ 기술 스택

- **AI** — Claude API (Haiku 4.5) + 웹 검색 도구
- **자동화** — GitHub Actions (cron)
- **알림** — Telegram Bot API
- **언어** — Python 3.11

---

## ⚙️ 환경변수 (GitHub Secrets)

| 변수명 | 설명 |
|--------|------|
| `CLAUDE_API_KEY` | Anthropic API 키 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID |

---

## 📁 파일 구조

```
portfolio-briefing-kakao/
├── .github/
│   └── workflows/
│       └── briefing.yml        # GitHub Actions 스케줄
├── portfolio_briefing.py       # 메인 스크립트
├── briefing_YYYYMMDD.md        # 매일 자동 생성되는 브리핑
└── README.md
```

---

## 💰 API 비용

| 항목 | 비용 |
|------|------|
| 1회 실행 | 약 $0.20 (약 270원) |
| 월간 (화~토, 1일 1회) | 약 $4~5 |

*Claude Haiku 4.5 기준*

---

*Powered by [Claude AI](https://anthropic.com) + [GitHub Actions](https://github.com/features/actions)*
