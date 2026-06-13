# Portfolio Briefing Kakao 요약

## 목적

이 프로젝트는 매일 포트폴리오 가격, 주요 뉴스, 변동성 신호, 대응 문구를 자동으로 만들어 Telegram으로 보내는 자동 브리핑 시스템입니다.

현재 운영 기준은 안정적인 아침 자동 브리핑입니다. Toss Open API는 IP whitelist 문제 때문에 자동 실행 경로에서 제거했고, GitHub Actions에서는 Yahoo Finance 기반 가격만 사용합니다.

## 현재 포트폴리오

| Ticker | 설명 | 보유 수량 | 비중 |
| --- | --- | ---: | ---: |
| `QLD` | ProShares Ultra QQQ | 40.173111 | 27.92% |
| `SSO` | ProShares Ultra S&P500 | 56.125231 | 27.70% |
| `USD` | ProShares Ultra Semiconductors | 37.337306 | 27.30% |
| `AIPO` | Defiance AI & Power Infrastructure ETF | 12 | 2.80% |
| `AMD` | Advanced Micro Devices | 3 | 11.47% |
| `SPCX` | SPCX ETF | 2 | 2.56% |
| `SCHD` | Schwab US Dividend Equity ETF | 1 | 0.24% |

`SCHD`는 일간 뉴스가 적은 자산이라 `news_optional: true`로 설정되어 있습니다. 뉴스가 없어도 `먼저 볼 것`에 경고처럼 표시하지 않습니다.

## 자동화 흐름

1. `cron-job.org`가 GitHub Actions의 `workflow_dispatch`를 호출합니다.
2. GitHub Actions가 Python 3.11 환경을 준비합니다.
3. `portfolio_briefing.py` 문법, `portfolio.json`, `screener.json`, unit test를 먼저 검증합니다.
4. 가격과 뉴스 데이터를 조회합니다.
5. 한국어 브리핑 메시지와 markdown 파일을 생성합니다.
6. Telegram으로 브리핑을 보냅니다.
7. 생성된 `briefings/briefing_YYYYMMDD.md` 파일을 GitHub에 커밋합니다.

## 데이터 소스

| 항목 | 현재 상태 |
| --- | --- |
| 가격 | Yahoo Finance chart API |
| 뉴스 | Google News RSS |
| 번역 | Google Translate 비공식 endpoint |
| 계좌/주문 | 사용 안 함 |
| Toss Open API | 자동 브리핑 경로에서 제거 |
| 가치 조건 검색 | `screener.json`은 있지만 기본 비활성 |

## Toss API를 뺀 이유

Toss Open API는 IP whitelist 방식입니다. GitHub Actions는 실행 IP가 고정되지 않기 때문에 정상 API key가 있어도 `403 IP address not allowed`가 발생할 수 있습니다.

따라서 현재 운영은 다음처럼 분리했습니다.

- 아침 자동 브리핑: GitHub Actions + Yahoo Finance
- Toss 계좌/주가/주문 관리: 추후 고정 IP 환경 또는 로컬 전용 기능으로 분리

## 주요 파일

| 파일 | 역할 |
| --- | --- |
| `portfolio_briefing.py` | 브리핑 생성 메인 스크립트 |
| `portfolio.json` | 포트폴리오 종목, 수량, 비중, 뉴스 필터 설정 |
| `screener.json` | 가치 조건 검색 설정. 현재 `enabled: false` |
| `.github/workflows/briefing.yml` | GitHub Actions 자동 실행 흐름 |
| `test_portfolio_briefing.py` | 뉴스 필터, 가격 경로, screener, alert 로직 테스트 |
| `briefings/` | 날짜별 생성 브리핑 저장소 |
| `README.md` | 공개용 프로젝트 설명 |
| `SETUP_GUIDE.md` | 운영 및 유지보수 가이드 |

## 브리핑 구성

Telegram 브리핑은 대략 다음 순서로 생성됩니다.

1. 한 줄 판단
2. 먼저 볼 것
3. 주요 지수와 포트폴리오 가격
4. 오늘의 대응
5. 변동성 체크
6. 참고 뉴스
7. 데이터 확인 필요 항목

가격 문구에는 보유 수량이 있을 경우 예상 평가손익이 함께 표시됩니다.

## 현재 주의점

- Yahoo Finance는 공식 안정 API가 아니므로 간헐적인 오류 가능성이 있습니다.
- Yahoo fundamentals endpoint는 `401 Unauthorized`가 자주 발생해 screener는 기본 비활성입니다.
- SCHD처럼 일간 뉴스가 적은 ETF는 뉴스가 없을 수 있습니다.
- GitHub Actions 자동 커밋 때문에 push 전 `git pull --rebase origin main`이 필요할 수 있습니다.
- Windows PowerShell 출력에서 한글이 깨져 보일 수 있지만, 파일 자체가 깨진 것은 아닐 수 있습니다.

## 운영 명령

로컬에서 Telegram 전송 없이 미리보기:

```bash
SEND_TELEGRAM=false python portfolio_briefing.py
```

검증:

```bash
python -m py_compile portfolio_briefing.py
python -m unittest
python -c "import json; json.load(open('portfolio.json', encoding='utf-8')); json.load(open('screener.json', encoding='utf-8'))"
```

## 최근 운영 결정

- Toss API는 자동 브리핑에서 제거했습니다.
- `XOVR`, `TSLA`, `CEG`, `426030`, `VTV`는 현재 포트폴리오에서 제외했습니다.
- `SCHD`를 새로 추가했습니다.
- 가치 조건 검색은 Yahoo 401 문제 때문에 기본 비활성화했습니다.
- 자동 브리핑의 핵심 목표는 안정적인 전달입니다.
