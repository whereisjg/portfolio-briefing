"""Read-only KIS domestic stock balance check for GitHub Actions."""

import sys

import requests

import portfolio_briefing as briefing
import trade_automation as trading


def main():
    holdings, summary, access_token = briefing.fetch_kis_balance()
    print(f"KIS balance query succeeded: {len(holdings)} holding(s)")
    print(f"evaluated amount: {summary.get('tot_evlu_amt', 'N/A')}")
    print(f"cash available: {summary.get('prvs_rcdl_excc_amt', 'N/A')}")
    context = trading.get_kis_context(access_token)
    orders = trading.fetch_today_orders(context)
    open_orders = [order for order in orders if briefing.as_float(order.get("rmn_qty"), 0) > 0]
    print(f"open orders today: {len(open_orders)}")
    for order in open_orders:
        print(
            "open order: "
            f"{order.get('pdno', '')} / no {order.get('odno', '')} / "
            f"remaining {order.get('rmn_qty', '')} / price {order.get('ord_unpr', '')}"
        )


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"KIS balance query failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
