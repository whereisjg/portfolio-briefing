# Portfolio Briefing Setup Guide

This is the operational setup guide for `whereisjg/portfolio-briefing-kakao`.

## What This Project Does

The workflow generates a daily portfolio briefing for:

- `QLD`
- `SSO`
- `USD`
- `426030` / TIMEFOLIO Nasdaq 100 Active ETF

It uses Claude to collect market data and news, sends the briefing to Telegram, and saves a markdown copy under `briefings/`.

## Source Of Truth

- `README.md`: public overview of the project
- `SETUP_GUIDE.md`: operational setup and maintenance guide
- `.github/workflows/briefing.yml`: GitHub Actions workflow
- `portfolio_briefing.py`: main briefing script
- `briefings/`: generated daily briefing files

## Security Rules

Never commit real API keys, Telegram bot tokens, chat IDs, or GitHub tokens.

Use placeholders in documentation:

```text
{CLAUDE_API_KEY}
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
| `CLAUDE_API_KEY` | Anthropic Claude API key |
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
      - name: Run briefing
        env:
          CLAUDE_API_KEY: ${{ secrets.CLAUDE_API_KEY }}
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

Current important script settings:

```python
MODEL = "claude-sonnet-4-6"
TICKERS = ["QLD", "SSO", "USD", "426030"]
```

The script saves generated files here:

```text
briefings/briefing_YYYYMMDD.md
```

## Verification Checklist

After changing secrets or workflow settings:

1. Confirm `CLAUDE_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` exist in GitHub Actions secrets.
2. Run the workflow manually from GitHub Actions.
3. Confirm a Telegram message arrives.
4. Confirm a new file appears under `briefings/`.
5. Confirm the workflow commit is pushed to `main`.

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

### Claude API Errors

Check the Anthropic API key, billing status, and usage limits in the Anthropic Console.

## Notes

- GitHub Actions cron uses UTC. The current setup uses cron-job.org with `Asia/Seoul`, which is easier to reason about for a 07:00 KST schedule.
- Generated briefing files are committed to the repository for historical tracking.
- Keep operational secrets only in GitHub Secrets or the external service that owns them.
