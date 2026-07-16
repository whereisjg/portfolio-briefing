# Portfolio Briefing

GitHub Actions, KIS Open API, Telegram을 사용한 일일 포트폴리오 브리핑입니다.

## 현재 동작

- 매일 KIS 계좌의 실제 보유 종목·수량·평단가·평가손익·예수금을 조회합니다.
- 실제 보유 종목의 비중, 시장 등락, 관련 뉴스, 리밸런싱 우선순위를 Telegram으로 보냅니다.
- 결과는 `briefings/briefing_YYYYMMDD.md`에 저장하고 GitHub에 기록합니다.
- 현재는 일반계좌용 `KIS_TEST_*` Secret을 사용합니다. ISA 이전이 완료되면 ISA Secret으로 전환합니다.
- 자동매매 규칙은 KIS 실계좌의 지정가 주문, 미체결 취소, 한 번의 재시도까지 실행합니다.

## 목표 비중

| ETF | 목표 비중 |
| --- | ---: |
| KoAct 미국나스닥성장기업액티브 | 25% |
| TIGER 미국나스닥100타겟데일리커버드콜 | 25% |
| KODEX 미국S&P500데일리커버드콜OTM | 25% |
| KODEX 미국머니마켓액티브 | 25% |

## What It Does

- KIS 기반 보유 종목·평단가·평가손익·예수금·나스닥100·S&P500
- KIS ETF 코드 기반 관련 뉴스 제목
- 목표 비중 대비 리밸런싱 우선순위
- Telegram 전송 및 Markdown 이력 저장

## Repository Structure

```text
portfolio-briefing/
├─ .github/workflows/briefing.yml
├─ briefings/briefing_YYYYMMDD.md
├─ portfolio.json
├─ trading_config.json
├─ portfolio_briefing.py
├─ trade_automation.py
├─ test_portfolio_briefing.py
└─ README.md
```

## 종목 설정

[`portfolio.json`](portfolio.json)은 실제 보유 수량을 적는 파일이 아니라 ETF 식별 정보와 목표 비중을 관리하는 설정입니다. 실제 보유 종목과 수량은 KIS 잔고조회 결과가 항상 우선합니다.

새 종목을 미리 등록하면 해당 종목을 보유했을 때 KIS 뉴스와 목표 비중을 적용합니다. 등록하지 않은 KIS 보유 종목도 브리핑에는 표시되지만, 뉴스는 선택적으로만 조회합니다.

각 종목은 다음 형식을 사용합니다.

```json
{
  "ticker": "KoAct미국나스닥성장기업액티브",
  "symbol": "0015B0.KS",
  "name": "KoAct 미국나스닥성장기업액티브",
  "display": "KoAct나스닥성장",
  "currency": "KRW",
  "target_weight_pct": 25,
  "news_optional": true
}
```

국내 ETF는 앞부분에 KIS 종목코드를 둔 심볼을 사용합니다. 선택 항목:

- `target_weight_pct`: 목표 비중과 신규 매수 우선순위
- `news_optional`: 뉴스가 없어도 경고하지 않음

## GitHub Secrets

| Name | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `CLAUDE_API_KEY` | (Optional) Claude Haiku for action commentary |
| `KIS_APP_KEY` | KIS API App Key for the briefing account |
| `KIS_APP_SECRET` | KIS API App Secret for the briefing account |
| `KIS_ACCOUNT_NO` | KIS account number, first 8 digits |
| `KIS_PRODUCT_CODE` | KIS account product code, last 2 digits |
| `KIS_TEST_APP_KEY` | 현재 일반계좌 테스트용 App Key |
| `KIS_TEST_APP_SECRET` | 현재 일반계좌 테스트용 App Secret |
| `KIS_TEST_ACCOUNT_NO` | 현재 일반계좌 번호 앞 8자리 |
| `KIS_TEST_PRODUCT_CODE` | 현재 일반계좌 상품코드 뒤 2자리 |

Secret 값은 채팅, 코드, `portfolio.json`에 넣지 않습니다.

KIS 접근 토큰은 GitHub Actions cache에 암호화해서 저장합니다. 마지막 발급 후 6시간 이내의 토큰만 재사용하며, 6시간이 지나거나 cache 복호화에 실패하면 새 토큰을 발급합니다. 암호화 키는 `KIS_APP_SECRET`이며 토큰 평문은 GitHub cache에 저장하지 않습니다.

## ISA 전환

ISA 이전이 완료되고 KIS API에서 잔고조회가 성공하면 `.github/workflows/briefing.yml`의 `Run briefing` 단계에서 아래 네 환경변수를 ISA용 Secret으로 바꿉니다.

```yaml
KIS_APP_KEY: ${{ secrets.KIS_APP_KEY }}
KIS_APP_SECRET: ${{ secrets.KIS_APP_SECRET }}
KIS_ACCOUNT_NO: ${{ secrets.KIS_ACCOUNT_NO }}
KIS_PRODUCT_CODE: ${{ secrets.KIS_PRODUCT_CODE }}
```

전환 후 `KIS Balance Check` workflow를 먼저 실행해 보유 종목과 예수금이 정상 조회되는지 확인합니다.

## 자동매매 규칙

[`trading_config.json`](trading_config.json)에 합의한 규칙을 저장했습니다. 현재 `live_orders_enabled: true`, `mode: live`이며 기본 workflow 실행은 실주문 모드입니다. `execute_live_orders` 입력을 끄면 주문 없이 계획만 만듭니다.

- 매일 10:00 KST 기준으로 잔고·예수금·가격을 조회합니다.
- 목표 비중은 네 ETF 각각 25%입니다.
- 매수는 KIS 주문가능금액 범위에서만, 하루 총 50만원까지 계산합니다. 1주를 살 수 없는 잔액은 다음 영업일로 이월합니다.
- 매도는 한 ETF가 30% 초과일 때 27%까지 계산하며, 종목별 하루 100만원을 넘기지 않습니다.
- 실주문은 최우선 매수호가·최우선 매도호가의 지정가만 허용합니다.
- 같은 날 대상 ETF 주문 이력이 있으면 실행을 중단해 중복 주문을 막습니다. GitHub Actions concurrency도 동시에 두 실행이 겹치지 않게 합니다.

브리핑 workflow는 KIS 잔고를 조회한 뒤 같은 계좌의 주문가능금액과 호가를 다시 확인해 리밸런싱 주문을 전송합니다. 실행 결과는 Telegram과 날짜별 Markdown 이력에 함께 남깁니다.

## cron-job.org Setup

Triggers `workflow_dispatch` every business day at 10:00 KST:

```
URL:    https://api.github.com/repos/{owner}/portfolio-briefing/actions/workflows/briefing.yml/dispatches
Method: POST
Headers:
  Authorization: Bearer {GITHUB_PERSONAL_ACCESS_TOKEN}
  Content-Type: application/json
Body:   {"ref":"main"}
```

Required token permission: `Actions: Read and write` on this repository only.

## Local Preview

```bash
SEND_TELEGRAM=false python3 portfolio_briefing.py
```

Verify before pushing:

```bash
python3 -m py_compile portfolio_briefing.py
python3 -m py_compile trade_automation.py
python3 -m unittest
python3 -c "import json; [json.load(open(path, encoding='utf-8')) for path in ('portfolio.json', 'trading_config.json')]"
```

## Troubleshooting

**Telegram message does not arrive** — check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in GitHub Secrets, then trigger the workflow manually.

**Workflow cannot push the briefing file** — confirm `permissions: contents: write` is set in `briefing.yml`.

**KIS 조회 오류** — KIS Secret과 계좌 상태를 확인한 뒤 workflow를 다시 실행합니다.

**Workflow push fails with "non-fast-forward"** — run `git pull --rebase origin main` locally before pushing, or re-trigger the workflow.
