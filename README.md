# Portfolio Briefing Kakao

Automated daily portfolio briefing using GitHub Actions, cron-job.org, Yahoo Finance data, rule-based guidance, and Telegram.

## Portfolio

| Ticker | Description | Market |
| --- | --- | --- |
| `QLD` | ProShares Ultra QQQ | US |
| `SSO` | ProShares Ultra S&P500 | US |
| `USD` | ProShares Ultra Semiconductors | US |
| `426030` | TIMEFOLIO Nasdaq 100 Active ETF | Korea |

## What It Does

- Fetches market prices directly
- Adds news titles from the last 24 hours using free RSS search
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
├─ portfolio_briefing.py
├─ README.md
└─ SETUP_GUIDE.md
```

## Configuration

Required GitHub Actions secrets:

| Name | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |

Do not commit real secret values to the repository.

## Setup

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for the full setup, maintenance, and troubleshooting guide.
