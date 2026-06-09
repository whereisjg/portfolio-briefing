# Claude Handoff: Portfolio Briefing Automation

## Purpose

This repository generates a daily Korean portfolio briefing, sends it to Telegram, and saves a markdown copy under `briefings/`.

The current implementation is intentionally free/rule-based. It does not use the Claude API or any paid AI API.

## Current Repository

- GitHub repository: `whereisjg/portfolio-briefing-kakao`
- Main script: `portfolio_briefing.py`
- Portfolio config: `portfolio.json`
- Workflow: `.github/workflows/briefing.yml`
- Generated briefings: `briefings/briefing_YYYYMMDD.md`
- Main docs: `README.md`, `SETUP_GUIDE.md`

## Current Operation

The GitHub Actions workflow uses:

- `workflow_dispatch`
- `permissions: contents: write`
- Python 3.11
- `requests`
- `pytz`

Required GitHub Actions secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Do not add real secret values to files or commit history.

The workflow is usually triggered by cron-job.org every morning. GitHub Actions native cron is not currently the main scheduling mechanism.

## Important Security Context

A Telegram bot token was previously exposed in a local setup guide. The user has already replaced/overwritten the GitHub secret with a new token.

Current files should not contain real Telegram tokens or chat IDs. Keep setup docs using placeholders only.

## Major Changes Already Made

### Removed Paid Claude API Usage

The previous briefing flow used a Claude API key and consumed paid API tokens.

Current state:

- `CLAUDE_API_KEY` is no longer required.
- The workflow no longer passes `CLAUDE_API_KEY`.
- `portfolio_briefing.py` does not call Anthropic/Claude.
- Guidance is generated with simple rule-based logic.

### Telegram Message Format

Telegram sends plain text, not Markdown parse mode.

Reason:

- Telegram Markdown parsing previously failed because generated text could contain characters such as underscores in filenames.

Current Telegram message:

- Does not include saved filename/path.
- Starts with Nasdaq and S&P 500 index movement.
- Then lists portfolio assets.
- Then includes core summary, action guidance, volatility check, and reference news.

### Briefing File Location

Generated briefing files are saved under:

```text
briefings/briefing_YYYYMMDD.md
```

Old root-level `briefing_*.md` files were moved into `briefings/`.

### Portfolio Config

Portfolio symbols are now configured in `portfolio.json`, not hardcoded in Python.

Current tracked indexes:

- `NASDAQ` / `^IXIC`
- `SP500` / `^GSPC`

Current tracked assets:

- `QLD`
- `SSO`
- `USD`
- `AIPO`
- `426030.KS`

When adding a new asset, verify both price and news before committing.

Recommended checks:

1. Confirm Yahoo Finance chart API returns valid price data.
2. Confirm Google News RSS returns recent news within 24 hours.
3. If direct news is weak, add `news_queries` fallback terms.
4. Validate `portfolio.json`.
5. Validate Python syntax.

Optional asset fields:

- `shares`: holding quantity. When present, the briefing shows estimated daily P/L.
- `weight_pct`: portfolio weight. When present, the briefing shows asset weight.
- `news_queries`: fallback news searches.
- `news_exclude`: terms to filter out polluted news results.

The Telegram briefing now includes a top `먼저 볼 것` section for quick alerts such as surge/drop, missing news, and data issues.

## News Logic

News uses free Google News RSS search.

Current behavior:

- Query includes `when:1d`.
- Script also filters RSS `pubDate` to the last 24 hours.
- English headlines are translated to Korean through a free Google Translate endpoint when possible.
- If translation fails, the original headline is kept.

For assets with poor direct news coverage, `portfolio.json` can include:

```json
"news_queries": [
  "direct ticker query",
  "fund name query",
  "related theme query"
]
```

`AIPO` currently uses fallback news queries because `AIPO ETF when:1d` often returns zero Google News RSS results.

Current AIPO fallback queries:

- `AIPO ETF`
- `Defiance AI Power Infrastructure ETF`
- `AI power infrastructure data centers energy stocks`
- `data center power infrastructure AI stocks`

`USD` is the ProShares Ultra Semiconductors ETF, not the US dollar. It uses explicit semiconductor ETF queries to avoid currency-news contamination.

Current USD fallback queries:

- `ProShares Ultra Semiconductors ETF`
- `USD ETF ProShares Ultra Semiconductors`
- `semiconductor ETF stocks`
- `SOXL SMH semiconductor stocks`

If headline translation fails, the script prefixes the headline with `[원문]` so the user can tell that the English title is untranslated fallback text.

## Price Logic

Prices use Yahoo Finance chart API:

```text
https://query2.finance.yahoo.com/v8/finance/chart/{symbol}
```

Important fix:

The script now uses `range=1d&interval=1m`, then calculates change from:

- `regularMarketPrice`
- `previousClose` or `chartPreviousClose`

This replaced the previous `5d / 1d` close-to-close calculation.

Reason:

- For `426030.KS`, Yahoo's 5-day daily close data contained `None` for an intermediate date.
- The previous logic compared the wrong valid close values.
- The corrected calculation matched the expected result:

```text
56,135 - 54,525 = 1,610
1,610 / 54,525 * 100 = 2.95%
```

## Current Known Limitations

- Google News RSS may return no results for narrow tickers.
- Google Translate free endpoint is unofficial and may fail. Failed translations are marked with `[원문]`.
- Yahoo Finance unofficial endpoints may change behavior.
- The local Windows terminal can display Korean as mojibake, but repository files should remain UTF-8.
- The GitHub connector previously allowed reading but not direct workflow dispatch.

## Useful Validation Commands

From the repository root:

```powershell
& 'C:\Users\star1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -c "import json; json.load(open('portfolio.json', encoding='utf-8')); print('json ok')"
```

```powershell
& 'C:\Users\star1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -c "import ast, pathlib; ast.parse(pathlib.Path('portfolio_briefing.py').read_text(encoding='utf-8')); print('syntax ok')"
```

To test one quote:

```powershell
& 'C:\Users\star1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -c "import portfolio_briefing as p; q=p.fetch_quote({'ticker':'426030','symbol':'426030.KS','currency':'KRW'}); print(q['price'], q['prev_close'], round(q['chg_pct'], 2))"
```

If `__pycache__/` is generated during local testing, remove it before committing.

## Git Notes

The workflow commits generated briefing files back to `main`, so push may be rejected if a new daily briefing commit appears remotely.

Usual fix:

```powershell
git fetch origin
git rebase origin/main
git push origin main
```

Do not use destructive git commands unless explicitly requested.

## Recent Relevant Commits

- `ded7800` Move portfolio symbols to config file
- `8c4d226` Add AIPO to portfolio
- `d139fbf` Improve AIPO news fallback and formatting
- `a8690c1` Use previous close for quote changes

