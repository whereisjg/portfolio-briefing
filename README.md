# Portfolio Briefing Kakao

Automated daily portfolio briefing using GitHub Actions, cron-job.org, Yahoo Finance data, rule-based guidance, and Telegram.

## Portfolio

| Ticker | Description | Market |
| --- | --- | --- |
| `QLD` | ProShares Ultra QQQ | US |
| `SSO` | ProShares Ultra S&P500 | US |
| `USD` | ProShares Ultra Semiconductors | US |
| `AIPO` | Defiance AI & Power Infrastructure ETF | US |
| `AMD` | Advanced Micro Devices | US |
| `SPCX` | SPCX ETF | US |
| `SCHD` | Schwab US Dividend Equity ETF | US |

## What It Does

- Fetches market prices directly
- Screens configured stocks for ROE/PER/PSR/PBR value criteria
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
├─ screener.json
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

Use Yahoo Finance symbols.

Optional fields:

- `shares`: holding quantity. If set, the briefing shows estimated daily P/L.
- `weight_pct`: portfolio weight. If set, the briefing shows asset weight.
- `news_queries`: fallback news searches for tickers with weak direct coverage.
- `news_include`: terms that make a news title relevant to the asset.
- `news_exclude`: terms to exclude from news results.

## Daily Value Screener

The daily run can read [screener.json](screener.json), but it is disabled by default.
Yahoo Finance frequently returns `401 Unauthorized` for fundamentals endpoints, so the screener should stay off unless a stable data source is added.

Current filter:

- `ROE >= 15%`
- `PER <= 15`
- Exclude `PSR >= 3`
- `PBR <= 1.5`

`symbols` controls the search universe. The screener is informational only and does not place orders.

## Configuration

Required GitHub Actions secrets:

| Name | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |

Do not commit real secret values to the repository.

## Local Preview

Run without sending Telegram:

```bash
SEND_TELEGRAM=false python portfolio_briefing.py
```

## Setup

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for the full setup, maintenance, and troubleshooting guide.
