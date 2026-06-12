# Portfolio Briefing Setup Guide

This is the operational setup guide for `whereisjg/portfolio-briefing-kakao`.

## What This Project Does

The workflow generates a daily portfolio briefing for:

- `QLD`
- `SSO`
- `USD`
- `AMD`
- `AIPO`
- `SPCX`
- `SCHD`

It fetches Yahoo Finance prices directly, adds news titles from the last 24 hours using free RSS search, translates English headlines to Korean when possible, applies rule-based guidance, sends the briefing to Telegram, and saves a markdown copy under `briefings/`.

## Source Of Truth

- `README.md`: public overview of the project
- `SETUP_GUIDE.md`: operational setup and maintenance guide
- `.github/workflows/briefing.yml`: GitHub Actions workflow
- `portfolio_briefing.py`: main rule-based briefing script
- `portfolio.json`: editable portfolio and index configuration
- `screener.json`: optional value-screening universe and criteria. Disabled by default because Yahoo fundamentals can return `401 Unauthorized`.
- `briefings/`: generated daily briefing files

## Security Rules

Never commit real API keys, Telegram bot tokens, chat IDs, or GitHub tokens.

Use placeholders in documentation:

```text
{TELEGRAM_BOT_TOKEN}
{TELEGRAM_CHAT_ID}
{GITHUB_PERSONAL_ACCESS_TOKEN}
```

If a Telegram bot token was ever committed or shared, revoke it in BotFather and update the GitHub Secret with the newly issued token.

## Required GitHub Secrets

Go to:

```text
Repository -> Settings -> Secrets and variables -> Actions
```

Create or update these repository secrets:

| Name | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram BotFather token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |

If a secret already exists, use `Update`. Do not create a second secret with a different name unless the workflow is also updated.

## Current Workflow

The repository currently uses `workflow_dispatch` so it can be triggered manually or by cron-job.org.

```yaml
name: Daily Portfolio Briefing

on:
  workflow_dispatch:

permissions:
  contents: write

jobs:
  briefing:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install requests pytz
      - name: Validate config and script
        run: |
          python -m py_compile portfolio_briefing.py
          python -c "import json; json.load(open('portfolio.json', encoding='utf-8'))"
          python -m unittest
      - name: Run briefing
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python portfolio_briefing.py
      - name: Commit and push
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          git config user.name "Portfolio Bot"
          git config user.email "bot@portfolio.local"
          git add briefings/*.md 2>/dev/null || true
          git commit -m "Daily briefing - $(date +%Y-%m-%d)" || true
          git push
```

## cron-job.org Setup

cron-job.org calls the GitHub Actions `workflow_dispatch` endpoint every morning.

```text
URL: https://api.github.com/repos/whereisjg/portfolio-briefing-kakao/actions/workflows/briefing.yml/dispatches
Method: POST
Headers:
  Authorization: Bearer {GITHUB_PERSONAL_ACCESS_TOKEN}
  Content-Type: application/json
Body:
  {"ref":"main"}
Timezone: Asia/Seoul
Schedule: 07:00 KST
```

Recommended fine-grained GitHub token settings:

```text
Repository access: whereisjg/portfolio-briefing-kakao
Permissions:
  Actions: Read and write
```

Do not store this GitHub token in the repository.

## Python Settings

Portfolio symbols are configured in `portfolio.json`, not inside the Python script.

Example asset:

```json
{
  "ticker": "QLD",
  "symbol": "QLD",
  "name": "QLD",
  "display": "QLD",
  "currency": "USD",
  "news_query": "ProShares Ultra QQQ QLD ETF",
  "news_include": ["QLD", "ProShares Ultra QQQ", "Nasdaq 100"],
  "news_exclude": ["UK tech"]
}
```

To add a symbol, add an object to the `assets` list.
To remove a symbol, delete that object from the `assets` list.
Use Yahoo Finance symbols.

News quality is controlled by:

- `news_queries`: search phrases used in order
- `news_include`: terms that make a title relevant
- `news_exclude`: terms that remove noisy or misleading titles

To preview locally without sending Telegram:

```bash
SEND_TELEGRAM=false python portfolio_briefing.py
```

The script saves generated files here:

```text
briefings/briefing_YYYYMMDD.md
```

## Verification Checklist

After changing secrets or workflow settings:

1. Confirm `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` exist in GitHub Actions secrets.
2. Run `python -m py_compile portfolio_briefing.py`.
3. Run `python -m unittest`.
4. Run the workflow manually from GitHub Actions.
5. Confirm a Telegram message arrives.
6. Confirm a new file appears under `briefings/`.
7. Confirm the workflow commit is pushed to `main`.

## Troubleshooting

### Telegram Message Does Not Arrive

Check that the bot token and chat ID are correct in GitHub Secrets. GitHub does not show secret values after saving, so verify by running the workflow.

### Workflow Cannot Push The Briefing File

Confirm the workflow includes:

```yaml
permissions:
  contents: write
```

Also confirm the commit step adds the correct folder:

```bash
git add briefings/*.md 2>/dev/null || true
```

### Duplicate Briefing Files Appear In The Repository Root

The current script should save to `briefings/`. If root-level `briefing_*.md` files appear again, check that `portfolio_briefing.py` still uses:

```python
output_dir = "briefings"
```

### Price Data Errors

Check whether Yahoo Finance is returning data for the ticker symbols.

## Notes

- GitHub Actions cron uses UTC. The current setup uses cron-job.org with `Asia/Seoul`, which is easier to reason about for a 07:00 KST schedule.
- The current version does not call any paid AI API.
- News items are titles only from the last 24 hours. They are not AI summaries.
- English news headlines are translated to Korean through a free translation endpoint. If translation fails, the original title is kept.
- Generated briefing files are committed to the repository for historical tracking.
- Keep operational secrets only in GitHub Secrets or the external service that owns them.
