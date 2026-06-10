# Portfolio Briefing Kakao

Automated daily portfolio briefing using GitHub Actions, cron-job.org, Yahoo Finance data, rule-based guidance, and Telegram.

## Portfolio

| Ticker | Description | Market |
| --- | --- | --- |
| `QLD` | ProShares Ultra QQQ | US |
| `SSO` | ProShares Ultra S&P500 | US |
| `USD` | ProShares Ultra Semiconductors | US |
| `AIPO` | Defiance AI & Power Infrastructure ETF | US |
| `426030` | TIMEFOLIO Nasdaq 100 Active ETF | Korea |

## What It Does

- Fetches market prices directly
- Reads Toss holdings and account-side trading info when configured
- Adds an account status section to the briefing using holdings, buying power, and open orders
- Provides guarded Toss order API helpers with dry-run as the default
- Adds news titles from the last 24 hours using free RSS search
- Translates English news headlines to Korean when possible
- Generates a concise Korean briefing with rule-based guidance
- Sends the result to Telegram
- Saves each briefing under `briefings/`
- Commits generated briefing files back to GitHub

## Current Operation

The workflow is triggered through `workflow_dispatch`, usually by cron-job.org every morning.

Generated files are stored here:

```text
briefings/briefing_YYYYMMDD.md
```

## Repository Structure

```text
portfolio-briefing-kakao/
├─ .github/
│  └─ workflows/
│     └─ briefing.yml
├─ briefings/
│  └─ briefing_YYYYMMDD.md
├─ portfolio.json
├─ portfolio_briefing.py
├─ test_portfolio_briefing.py
├─ README.md
└─ SETUP_GUIDE.md
```

## Editing The Portfolio

Add or remove tracked symbols in [portfolio.json](portfolio.json).

Each asset needs:

```json
{
  "ticker": "QLD",
  "symbol": "QLD",
  "name": "QLD",
  "display": "QLD",
  "currency": "USD",
  "shares": null,
  "weight_pct": null,
  "news_query": "Nasdaq 100"
}
```

Use Yahoo Finance symbols. For Korean stocks and ETFs, use the `.KS` suffix, for example `426030.KS`.

Optional fields:

- `shares`: holding quantity. If set, the briefing shows estimated daily P/L.
- `weight_pct`: portfolio weight. If set, the briefing shows asset weight.
- `news_queries`: fallback news searches for tickers with weak direct coverage.
- `news_include`: terms that make a news title relevant to the asset.
- `news_exclude`: terms to exclude from news results.

## Configuration

Required GitHub Actions secrets:

| Name | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `TOSS_CLIENT_ID` | Toss Securities Open API client ID |
| `TOSS_CLIENT_SECRET` | Toss Securities Open API client secret |
| `TOSS_ACCESS_TOKEN` | Optional pre-issued Toss access token |

Do not commit real secret values to the repository. `TOSS_CLIENT_ID` and `TOSS_CLIENT_SECRET` are preferred because access tokens can expire.

Legacy aliases `TOSS_API_KEY` and `TOSS_API_SECRET` are still accepted, but `TOSS_CLIENT_ID` and `TOSS_CLIENT_SECRET` are preferred.

Optional GitHub Actions variables for Toss price provider:

| Name | Purpose |
| --- | --- |
| `TOSS_BASE_URL` | Toss Open API base URL, defaults to `https://openapi.tossinvest.com` |
| `TOSS_ACCOUNT_SEQ` | Optional account sequence for holdings. If omitted, the first account is used. |
| `TOSS_TOKEN_URL` | Optional override for `/oauth2/token` |
| `TOSS_QUOTE_URL_TEMPLATE` | Optional override for `/api/v1/prices` |
| `TOSS_CANDLE_URL_TEMPLATE` | Optional override for `/api/v1/candles` |
| `TOSS_ENABLE_LIVE_ORDERS` | Must be `true` before any live Toss order can be submitted |

Optional GitHub Actions secret for live order confirmation:

| Name | Purpose |
| --- | --- |
| `TOSS_LIVE_ORDER_CONFIRM` | Must equal `LIVE_ORDER_APPROVED` before any live Toss order can be submitted |

When Toss settings are present, prices use Toss first and fall back to Yahoo Finance if Toss fails.
Toss current prices come from `/api/v1/prices`; previous close for daily change comes from `/api/v1/candles?interval=1d&count=2`.
If account access is available, `/api/v1/accounts` and `/api/v1/holdings` are used read-only to fill `shares` and daily P/L.
The briefing also includes account status from Toss account APIs: total account value, purchase amount, holding value, daily change, accumulated P/L, KRW/USD buying power, and open order count.

Toss management helpers are available for `/api/v1/buying-power`, `/api/v1/sellable-quantity`, `/api/v1/commissions`, and `/api/v1/orders`.
Order create, modify, and cancel helpers default to dry-run and do not send live orders unless both `TOSS_ENABLE_LIVE_ORDERS=true` and `TOSS_LIVE_ORDER_CONFIRM=LIVE_ORDER_APPROVED` are set. The daily briefing workflow does not automatically call order submission.

## Local Preview

Run without sending Telegram:

```bash
SEND_TELEGRAM=false python portfolio_briefing.py
```

## Setup

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for the full setup, maintenance, and troubleshooting guide.
