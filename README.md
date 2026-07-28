# Portfolio Briefing

GitHub Actions, KIS Open API, Telegram을 사용한 일일 포트폴리오 브리핑입니다.

## 현재 동작

- 매일 KIS 계좌의 실제 보유 종목·수량·평단가·평가손익·예수금을 조회합니다.
- 실제 보유 종목의 비중, 시장 등락, 관련 뉴스, 리밸런싱 우선순위를 Telegram으로 보냅니다.
- 결과는 `briefings/briefing_YYYYMMDD.md`에 저장하고 GitHub에 기록합니다.
- `paper`는 모의투자, `live`는 기존 일반계좌, `isa`는 ISA 계좌용 Secret을 각각 사용합니다.
- 자동매매 규칙은 KIS 실계좌의 지정가 주문, 미체결 취소, 한 번의 재시도까지 실행합니다. Telegram에는 주문별 지정가, 체결수량, 평균 체결가, 미체결·취소 상태를 함께 기록합니다.

## 추세별 목표 비중

완료된 일봉으로 계좌 위험자산 묶음(50%), 나스닥100(25%), S&P500(25%)의 20일·60일 이동평균을 계산합니다. 날짜별 복합 점수가 최근 3거래일 연속 같을 때만 비중을 변경하며, 엇갈리면 중립 비중을 유지합니다.

| ETF | 위험 선호 | 중립 | 위험 회피 |
| --- | ---: | ---: | ---: |
| KoAct 미국나스닥성장기업액티브 | 30% | 25% | 15% |
| TIGER 미국나스닥100타겟데일리커버드콜 | 30% | 25% | 20% |
| KODEX 미국S&P500데일리커버드콜OTM | 20% | 25% | 30% |
| KODEX 단기채권 | 20% | 25% | 35% |

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
├─ kis_client.py             # KIS 인증, 토큰, 계좌 잔고 공통 계층
├─ trading_strategy.py       # 목표 비중, 추세 판단, 주문 계획
├─ trading_execution.py      # KIS 주문, 취소, 체결 확인
├─ run_state.py              # workflow 단계 간 임시 상태
├─ portfolio_briefing.py
├─ trade_automation.py       # 이전 실행 명령 호환용 진입점
├─ test_portfolio_briefing.py
└─ README.md
```

`kis_client.py`는 KIS 인증, 토큰 cache, 계좌 정보, 잔고조회를 공통으로 담당합니다. `trading_strategy.py`는 KIS 호출이나 주문 전송을 하지 않고 잔고·가격 Snapshot에서 매수·매도 계획만 반환합니다. `trading_execution.py`는 KIS client와 전략 계획을 사용해 지정가 주문·취소·체결 확인을 실행합니다. `portfolio_briefing.py`는 실행 후 실제 잔고와 결과를 Telegram 및 Markdown으로 전달합니다. `trade_automation.py`는 이전 실행 명령을 유지하기 위한 호환용 진입점입니다.

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
  "target_weight_pct": 25
}
```

국내 ETF는 앞부분에 KIS 종목코드를 둔 심볼을 사용합니다. 선택 항목:

- `target_weight_pct`: 목표 비중과 신규 매수 우선순위

## GitHub Secrets

| Name | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `KIS_APP_KEY` | KIS API App Key for the briefing account |
| `KIS_APP_SECRET` | KIS API App Secret for the briefing account |
| `KIS_ACCOUNT_NO` | KIS account number, first 8 digits |
| `KIS_PRODUCT_CODE` | KIS account product code, last 2 digits |
| `KIS_TEST_APP_KEY` | 현재 일반계좌 테스트용 App Key |
| `KIS_TEST_APP_SECRET` | 현재 일반계좌 테스트용 App Secret |
| `KIS_TEST_ACCOUNT_NO` | 현재 일반계좌 번호 앞 8자리 |
| `KIS_TEST_PRODUCT_CODE` | 현재 일반계좌 상품코드 뒤 2자리 |
| `KIS_ISA_APP_KEY` | ISA 계좌 App Key |
| `KIS_ISA_APP_SECRET` | ISA 계좌 App Secret |
| `KIS_ISA_ACCOUNT_NO` | ISA 계좌번호 앞 8자리 |
| `KIS_ISA_PRODUCT_CODE` | ISA 계좌 상품코드 뒤 2자리 |

Secret 값은 채팅, 코드, `portfolio.json`에 넣지 않습니다.

KIS 접근 토큰은 GitHub Actions cache에 암호화해서 저장합니다. 마지막 발급 후 6시간 이내의 토큰만 재사용하며, 6시간이 지나거나 cache 복호화에 실패하면 새 토큰을 발급합니다. 암호화 키는 `KIS_APP_SECRET`이며 토큰 평문은 GitHub cache에 저장하지 않습니다.

## 모의투자

기본 workflow는 `paper` 모드이며, `KIS_PAPER_*` Secrets와 모의투자 URL, `VTTC...` TR ID만 사용합니다. `live`는 workflow 실행 화면에서 명시적으로 선택할 때만 실전 일반계좌를 사용합니다. 모의투자에서도 주문·5분 후 취소·한 번 재주문 흐름을 검증할 수 있습니다.

## ISA 계좌

`account_mode: isa`를 선택하면 `KIS_ISA_*` Secret을 사용합니다. 자동매매 전에는 `KIS Balance Check` workflow를 `isa` 모드로 실행해 보유 종목과 예수금이 정상 조회되는지 확인합니다.

## 자동매매 규칙

[`trading_config.json`](trading_config.json)에 합의한 규칙을 저장했습니다. `live_orders_enabled: true`, `mode: live`는 주문 실행을 허용하는 설정입니다. workflow 기본 계좌는 `isa`이며, `account_mode: live` 또는 `isa`를 선택한 경우에만 해당 실계좌 주문을 사용합니다. `execute_live_orders` 입력을 끄면 어느 계좌에서도 주문 없이 계획만 만듭니다.

- cron-job.org가 영업일 10:00 KST에 workflow를 실행하면 잔고·예수금·가격을 조회합니다. 실행이 실패하거나 누락되면 GitHub Actions 백업 workflow가 10:12 KST에 한 번만 재실행합니다.
- 계좌 위험자산 묶음(50%, 중립 목표비중 가중), 나스닥100(25%), S&P500(25%)의 완료된 일봉을 합산한 20일·60일 이동평균 신호가 3거래일 연속 같을 때 목표 비중을 변경합니다. 상승 추세는 위험 선호, 하락 추세는 위험 회피, 그 외에는 중립 비중을 적용합니다.
- 매수는 KIS 주문가능금액 범위에서만 계산합니다. 실계좌의 일일 매수·매도 한도는 각각 계좌 총자산의 3%이며, 매도 체결금액은 당일 매수 주문가능금액에 반영됩니다. 1주를 살 수 없는 잔액은 다음 영업일로 이월합니다.
- 위 매수·매도 3% 한도는 실계좌에만 적용합니다. 모의투자는 당일 한도와 누적 체결액 차감을 적용하지 않되, 한 번의 workflow에서 매수·매도 각각 최대 10만원(`paper_test_order_limit_krw`)까지만 주문해 반복 검증할 수 있게 합니다.
- 실주문은 KRX 거래일 중 09:00~15:20 KST에만 전송합니다. 그 밖의 시간에는 브리핑만 보내고 자동매매는 대기 상태로 기록합니다.
- 매도는 추세별 목표 비중보다 2%p를 초과한 ETF를 해당 목표 비중까지 계산하며, 종목별 하루 100만원을 넘기지 않습니다.
- 목표 종목에서 제외된 ETF는 `liquidation_codes`에 등록해, 하루 매매 한도 안에서 우선 매도합니다. 현재 `KODEX 미국머니마켓액티브`는 `KODEX 단기채권`으로 전환 중입니다.
- 실주문은 최우선 매수호가·최우선 매도호가의 지정가만 허용합니다.
- 같은 날 이미 체결된 대상 ETF 주문은 일일 총 매매 한도에서 차감합니다. 미체결 주문이 남은 경우에만 추가 주문을 보류합니다. GitHub Actions concurrency도 동시에 두 실행이 겹치지 않게 합니다.

브리핑 workflow는 KIS 잔고를 조회한 뒤 같은 계좌의 주문가능금액과 호가를 다시 확인해 리밸런싱 주문을 전송합니다. 실행 결과는 Telegram과 날짜별 Markdown 이력에 함께 남깁니다.

## cron-job.org Setup

Triggers `workflow_dispatch` every business day at 10:00 KST:

```
URL:    https://api.github.com/repos/{owner}/portfolio-briefing/actions/workflows/briefing.yml/dispatches
Method: POST
Headers:
  Authorization: Bearer {GITHUB_PERSONAL_ACCESS_TOKEN}
  Content-Type: application/json
Body:   {"ref":"main","inputs":{"account_mode":"isa","execute_live_orders":"true"}}
```

Required token permission: `Actions: Read and write` on this repository only.

## Local Preview

```bash
SEND_TELEGRAM=false python3 portfolio_briefing.py
```

Verify before pushing:

```bash
python3 -m py_compile portfolio_briefing.py
python3 -m py_compile portfolio_briefing.py kis_client.py trading_execution.py trading_strategy.py run_state.py
python3 -m unittest
python3 -c "import json; [json.load(open(path, encoding='utf-8')) for path in ('portfolio.json', 'trading_config.json')]"
```

## Troubleshooting

**Telegram message does not arrive** — check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in GitHub Secrets, then trigger the workflow manually.

**Workflow cannot push the briefing file** — confirm `permissions: contents: write` is set in `briefing.yml`.

**KIS 조회 오류** — KIS Secret과 계좌 상태를 확인한 뒤 workflow를 다시 실행합니다.

**Workflow push fails with "non-fast-forward"** — run `git pull --rebase origin main` locally before pushing, or re-trigger the workflow.
