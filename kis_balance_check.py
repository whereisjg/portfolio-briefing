"""Read-only KIS domestic stock balance check for GitHub Actions."""

import sys

import requests

import portfolio_briefing as briefing


def main():
    holdings, summary, _access_token = briefing.fetch_kis_balance()
    print(f"KIS balance query succeeded: {len(holdings)} holding(s)")
    print(f"evaluated amount: {summary.get('tot_evlu_amt', 'N/A')}")
    print(f"cash available: {summary.get('prvs_rcdl_excc_amt', 'N/A')}")


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"KIS balance query failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
