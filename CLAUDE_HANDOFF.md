# Portfolio Briefing 작업 인수인계

## 프로젝트

- Repository: `whereisjg/portfolio-briefing`
- Local path: `/Users/leesunghun/Documents/New project`
- 목적: KIS Open API로 ISA 계좌 잔고를 조회하고 Telegram 포트폴리오 브리핑과 자동매매를 실행하는 GitHub Actions 프로젝트
- 사용자 응답 언어: 한국어
- 계좌번호, App Key, App Secret은 절대 코드·Markdown·로그에 기록하지 않음

## 현재 상태

현재 `main` 브랜치에는 다음 기능이 반영되어 있습니다.

1. KIS 계좌 잔고, 보유 종목, 평단가, 평가손익, 예수금 조회
2. KIS 기반 나스닥100·S&P500 지수 조회
3. HMA20/HMA40 및 HMA200 필터 기반 복합 추세 판단
4. 추세별 목표 비중과 리밸런싱 주문 계산
5. 실계좌 지정가 매수·매도, 미체결 취소, 체결 확인, 1회 자동 복구
6. Telegram 및 `briefings/briefing_YYYYMMDD.md` 전송
7. KIS 계좌 권리내역에서 현금 분배금 조회
8. 평가손익과 누적 분배금을 합산한 총손익 표시

최근 주요 커밋:

- `f51a141` 누적 분배금·총손익 표시
- `09e1390` KIS 분배금 권리코드 `32` 인식
- `31a1b76` KIS 권리내역 진단 로그 추가

작업 디렉터리의 `tmp/`는 사용자가 만든 untracked 파일이므로 삭제하거나 수정하지 않습니다.

## 현재 포트폴리오

`portfolio.json`이 설정의 기준입니다.

| 종목 | KIS 코드 | 중립 목표 |
| --- | --- | ---: |
| KoAct 미국나스닥성장기업액티브 | `0015B0` | 20% |
| TIGER 미국나스닥100타겟데일리커버드콜 | `486290` | 15% |
| KODEX 미국S&P500데일리커버드콜OTM | `0005A0` | 15% |
| TIGER 일본니케이225 | `241180` | 20% |
| KODEX 단기채권 | `153130` | 30% |

제외·정리 대상: `0036D0`, `0048J0`, `101280`

## 추세 전략

`trading_config.json` 기준:

- 평균선: `HMA`
- 단기/장기: 20일 / 40일
- 장기 필터: HMA200
- 상태 확정: 최근 3거래일 확인
- 신호 구성: 계좌 위험자산 50%, 나스닥100 25%, S&P500 25%

| 상태 | KoAct | TIGER Nasdaq CC | KODEX S&P CC | TIGER Nikkei | 단기채 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 위험 선호 | 30% | 15% | 15% | 20% | 20% |
| 중립 | 20% | 15% | 15% | 20% | 30% |
| 위험 회피 | 10% | 15% | 15% | 20% | 40% |

## 자동매매 규칙

- 실계좌 일일 매수 한도: 총자산의 3%
- 실계좌 일일 매도 한도: 총자산의 3%
- 매도 체결금액은 당일 매수 가능 한도에 재사용 가능
- 종목별 매도 한도: 100만원
- 리밸런싱 밴드: 목표 비중 대비 2%p
- 주문 유형: 지정가
- 매수 가격: 최우선 매도호가
- 매도 가격: 최우선 매수호가
- 1차 주문 후 5분 뒤 미체결 확인 및 취소
- 주문 상태가 불명확하면 중복 주문하지 않음
- 주문 전 조회 실패는 5분 뒤 1회 자동 복구
- 모의계좌는 실계좌 3% 제한 대신 workflow 1회 매수·매도 각각 10만원 제한
- 주문 없이 테스트할 때는 반드시 `execute_live_orders=false`

## 분배금 구현

구현 위치:

- `kis_client.py`: KIS `/uapi/domestic-stock/v1/trading/period-rights` 호출
- `portfolio_briefing.py`: 권리코드 필터, 세후 분배금 합산, Telegram/Markdown 표시
- `test_portfolio_briefing.py`: 분배금 및 총손익 테스트

현재 기준:

- 조회 시작일: `portfolio.json`의 `dividend_start_date` (현재 `20200101`, env `KIS_DIVIDEND_START_DATE`가 우선)
- 종료일: 실행일
- KIS 권리코드 `32`를 분배금 코드로 인식
- `last_alct_amt + last_ftsk_chgs - tax_amt` 계산
- 출력: `평가손익`, `누적 분배금`, `총손익`

계좌를 전부 매도·출금한 뒤 새로 운용을 시작하면 사용자가 알려줄 예정입니다. 그때 `portfolio.json`의 `dividend_start_date`를 새 운용 시작일로 바꾸고 필요하면 분배금 기준을 재설정합니다.

## Workflow

주요 workflow: `.github/workflows/briefing.yml`

주문 없는 ISA 실행:

```bash
gh workflow run briefing.yml --ref main \
  -f account_mode=isa \
  -f execute_live_orders=false \
  -f refresh_kis_token=false
```

실행 확인:

```bash
gh run list --workflow briefing.yml --limit 1
gh run watch RUN_ID --exit-status
gh run view RUN_ID --log
```

실주문 실행은 사용자가 명시적으로 요청한 경우에만 수행합니다.

## 검증 명령

```bash
python3 -m py_compile portfolio_briefing.py kis_client.py
python3 -m unittest
git diff --check
```

현재 전체 테스트는 89개이며 마지막 검증에서 모두 통과했습니다.

## 작업 시 주의사항

1. 먼저 `git status`, `git log`, `README.md`, `portfolio.json`, `trading_config.json`을 확인합니다.
2. 기존 사용자의 변경사항과 `tmp/`를 되돌리지 않습니다.
3. KIS Secret은 출력·로그·커밋에 포함하지 않습니다.
4. 코드 수정 후 `py_compile`, `unittest`, `git diff --check`를 실행합니다.
5. GitHub Actions가 `briefings/` 또는 `performance/`를 커밋할 수 있으므로 push 전에 `git pull --rebase origin main`을 실행합니다.
6. 액션 테스트는 기본적으로 주문 없는 ISA 실행으로 검증합니다.
7. 사용자가 “액션”, “런”이라고만 말하면 실행과 결과 확인까지 수행하되, 주문 여부가 불명확하면 주문 없는 실행을 우선합니다.

## 다음 작업 후보

- 분배금 조회 시작일을 계좌 재운용 시작일로 변경하는 workflow input 또는 상태 파일 설계
- 분배금 포함 TWR 계산
- KIS 권리내역의 중복 조회·중복 합산 방지 검증
- Telegram 메시지에서 평가손익·누적 분배금·총손익의 의미를 짧게 설명할지 검토
