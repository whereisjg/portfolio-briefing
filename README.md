# Portfolio Briefing

Automated daily portfolio briefing using GitHub Actions, Yahoo Finance, and Telegram.

## Portfolio

| Ticker | Description | Market |
| --- | --- | --- |
| `SOXL` | Direxion Daily Semiconductor Bull 3x Shares | US |
| `426030.KS` | TIMEFOLIO 미국나스닥100액티브 | Korea |
| `494300.KS` | KODEX 미국나스닥100데일리커버드콜OTM | Korea |
| `495060.KS` | TIME 코리아밸류업액티브 | Korea |

## What It Does

- Fetches market prices from Yahoo Finance
- Screens configured stocks for ROE/PER/PSR/PBR value criteria
- Adds news titles from the last 36 hours using Yahoo Finance News API
- Translates English headlines to Korean (Claude Haiku if key set, otherwise Google Translate)
- Generates a concise Korean briefing with rule-based guidance
- Sends the result to Telegram
- Saves and commits each briefing under `briefings/`

## Repository Structure

```text
portfolio-briefing/
├─ .github/workflows/briefing.yml
├─ briefings/briefing_YYYYMMDD.md
├─ portfolio.json
├─ screener.json
├─ portfolio_briefing.py
├─ test_portfolio_briefing.py
└─ README.md
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
  "weight_pct": null
}
```

Use Yahoo Finance symbols. Optional fields:

- `shares`: holding quantity — shows estimated daily P/L in the briefing
- `weight_pct`: portfolio weight — shows asset weight in the briefing
- `news_include`: terms that make a news title relevant to the asset
- `news_exclude`: terms to exclude from news results
- `news_optional`: set to `true` when missing daily news should not be treated as an alert

## Daily Value Screener

Configured in [screener.json](screener.json). Disabled by default — Yahoo Finance fundamentals endpoints return `401 Unauthorized` intermittently.

Criteria: `ROE >= 15%`, `PER <= 15`, `PSR < 3`, `PBR <= 1.5`

## GitHub Secrets

| Name | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `CLAUDE_API_KEY` | (Optional) Claude Haiku for news translation and action commentary |

## cron-job.org Setup

Triggers `workflow_dispatch` every morning at 07:00 KST:

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
python3 -m unittest
python3 -c "import json; json.load(open('portfolio.json', encoding='utf-8')); json.load(open('screener.json', encoding='utf-8'))"
```

## Troubleshooting

**Telegram message does not arrive** — check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in GitHub Secrets, then trigger the workflow manually.

**Workflow cannot push the briefing file** — confirm `permissions: contents: write` is set in `briefing.yml`.

**Price data errors** — Yahoo Finance does not have a stable public API. Retry the workflow or check the ticker symbol.

**Workflow push fails with "non-fast-forward"** — run `git pull --rebase origin main` locally before pushing, or re-trigger the workflow.
