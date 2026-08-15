"""Research-only backtest for the configured HMA allocation strategy.

This module reads historical closes and writes reports. It deliberately has no
order-submission path, so a backtest cannot place a live order.
"""

import argparse
from copy import deepcopy
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import kis_client
import trading_execution
from trading_strategy import (
    calculate_trend_state,
    load_config,
    plan_orders,
    weighted_close_series,
)


BACKTEST_DIR = Path("backtests")
STATE_SCORE = {"risk_on": 1, "neutral": 0, "risk_off": -1}


def common_dates(series_by_key):
    if not series_by_key:
        raise ValueError("백테스트 가격 시계열이 없습니다.")
    date_sets = [{date for date, _price in series} for series in series_by_key.values()]
    dates = set.intersection(*date_sets)
    if not dates:
        raise ValueError("백테스트 가격 시계열의 공통 거래일이 없습니다.")
    return sorted(dates)


def series_until(series, indexes, date):
    index = indexes[date]
    return series[:index + 1]


def component_series(config, asset_closes, index_closes):
    trend = config["trend_strategy"]
    components = []
    for signal in trend["signals"]:
        if signal["kind"] == "portfolio":
            codes = signal["codes"]
            closes = weighted_close_series(
                {code: asset_closes[code] for code in codes},
                {code: config["target_weights"][code] for code in codes},
            )
        else:
            closes = index_closes[signal["symbol"]]
        components.append({**signal, "closes": closes})
    return components


def confirmed_composite_states(config, asset_closes, index_closes):
    """Return close-of-day composite states without using future closes."""
    trend = config["trend_strategy"]
    components = component_series(config, asset_closes, index_closes)
    indexes = {
        item["label"]: {date: index for index, (date, _price) in enumerate(item["closes"])}
        for item in components
    }
    dates = common_dates({item["label"]: item["closes"] for item in components})
    raw_states = []
    for date in dates:
        states = []
        try:
            for item in components:
                state = calculate_trend_state(
                    series_until(item["closes"], indexes[item["label"]], date),
                    int(trend["short_window_days"]),
                    int(trend["long_window_days"]),
                    1,
                    trend.get("average_type", "sma"),
                    trend.get("long_filter_window_days"),
                )["state"]
                states.append((item, state))
        except ValueError:
            continue
        score = sum(float(item["weight_pct"]) / 100 * STATE_SCORE[state] for item, state in states)
        threshold = float(trend.get("composite_threshold", 0.5))
        raw_states.append({
            "date": date,
            "state": "risk_on" if score >= threshold else "risk_off" if score <= -threshold else "neutral",
            "score": score,
        })

    confirmation_days = int(trend["confirmation_days"])
    result = {}
    for index, item in enumerate(raw_states):
        recent = raw_states[index - confirmation_days + 1:index + 1]
        result[item["date"]] = item["state"] if len(recent) == confirmation_days and len(
            {entry["state"] for entry in recent}
        ) == 1 else "neutral"
    return result


def required_trend_closes(config):
    trend = config["trend_strategy"]
    windows = [int(trend["short_window_days"]), int(trend["long_window_days"])]
    if trend.get("long_filter_window_days"):
        windows.append(int(trend["long_filter_window_days"]))
    required = max(windows) + int(trend["confirmation_days"]) - 1
    if trend.get("average_type", "sma") == "hma":
        required += math.isqrt(max(windows)) - 1
    return required


def warmup_calendar_days(config):
    """Convert required trading closes to a conservative calendar-day warm-up."""
    return math.ceil(required_trend_closes(config) * 7 / 5) + 30


def portfolio_value(portfolio, prices):
    return portfolio["cash"] + sum(
        portfolio["quantities"].get(code, 0) * price
        for code, price in prices.items()
    )


def initial_portfolio(codes, prices, weights, capital):
    quantities = {}
    invested = 0.0
    for code in codes:
        quantity = math.floor(capital * float(weights[code]) / 100 / prices[code])
        quantities[code] = quantity
        invested += quantity * prices[code]
    return {"cash": capital - invested, "quantities": quantities}


def apply_virtual_orders(portfolio, plan, cost_rate):
    bought = 0.0
    sold = 0.0
    costs = 0.0
    sold_by_code = {}
    for order in plan["sells"]:
        code = order["code"]
        quantity = min(int(order["quantity"]), int(portfolio["quantities"].get(code, 0)))
        if quantity < 1:
            continue
        gross = quantity * order["price"]
        fee = gross * cost_rate
        portfolio["quantities"][code] -= quantity
        portfolio["cash"] += gross - fee
        sold += gross
        costs += fee
        sold_by_code[code] = sold_by_code.get(code, 0.0) + gross

    for order in plan["buys"]:
        code = order["code"]
        unit_cost = order["price"] * (1 + cost_rate)
        quantity = min(int(order["quantity"]), math.floor(portfolio["cash"] / unit_cost))
        if quantity < 1:
            continue
        gross = quantity * order["price"]
        fee = gross * cost_rate
        portfolio["quantities"][code] = portfolio["quantities"].get(code, 0) + quantity
        portfolio["cash"] -= gross + fee
        bought += gross
        costs += fee
    return {"buy": bought, "sell": sold, "cost": costs, "sold_by_code": sold_by_code}


def virtual_rebalance(config, portfolio, prices, target_weights, transaction_cost_bps):
    """Apply the live strategy's two-pass limits to a virtual integer-share account."""
    effective = deepcopy(config)
    effective["target_weights"] = target_weights
    effective["liquidation_codes"] = []
    total = portfolio_value(portfolio, prices)
    buy_cap = total * float(effective.get("daily_buy_limit_pct", 100)) / 100
    sell_cap = total * float(effective.get("daily_sell_limit_pct", 100)) / 100
    effective.setdefault("daily_sell_limit_per_asset_krw", total)
    effective.setdefault("rebalance_band_pct", 0)
    cost_rate = float(transaction_cost_bps) / 10000

    def positions():
        return {
            code: {"quantity": portfolio["quantities"].get(code, 0), "price": prices[code]}
            for code in target_weights
        }

    first_plan = plan_orders(
        effective,
        positions(),
        prices,
        portfolio["cash"],
        portfolio["cash"],
        buy_limit=buy_cap,
        sell_turnover_limit=sell_cap,
    )
    first = apply_virtual_orders(portfolio, first_plan, cost_rate)
    per_asset_remaining = {
        code: max(float(effective["daily_sell_limit_per_asset_krw"]) - first["sold_by_code"].get(code, 0), 0)
        for code in target_weights
    }
    second_plan = plan_orders(
        effective,
        positions(),
        prices,
        portfolio["cash"],
        portfolio["cash"],
        per_asset_remaining,
        buy_limit=max(buy_cap - first["buy"], 0),
        sell_turnover_limit=max(sell_cap - first["sell"], 0),
    )
    second = apply_virtual_orders(portfolio, second_plan, cost_rate)
    traded = first["buy"] + first["sell"] + second["buy"] + second["sell"]
    return {
        "turnover": traded / (2 * total) if total > 0 else 0,
        "cost": first["cost"] + second["cost"],
    }


def simulate_strategy(config, asset_maps, dates, states, transaction_cost_bps, initial_capital, dynamic):
    codes = list(config["target_weights"])
    neutral = config["trend_strategy"]["weights"]["neutral"]
    start_prices = {code: asset_maps[code][dates[0]] for code in codes}
    portfolio = initial_portfolio(codes, start_prices, neutral, initial_capital)
    peak = 1.0
    mdd = 0.0
    turnover = 0.0

    for signal_date, execution_date in zip(dates, dates[1:]):
        prices = {code: asset_maps[code][execution_date] for code in codes}
        before = portfolio_value(portfolio, prices)
        before_index = before / initial_capital
        peak = max(peak, before_index)
        mdd = min(mdd, before_index / peak - 1)
        state = states[signal_date] if dynamic else "neutral"
        target = config["trend_strategy"]["weights"][state]
        trade = virtual_rebalance(config, portfolio, prices, target, transaction_cost_bps)
        turnover += trade["turnover"]
        index_value = portfolio_value(portfolio, prices) / initial_capital
        peak = max(peak, index_value)
        mdd = min(mdd, index_value / peak - 1)
        if before <= 0:
            raise ValueError("가상 포트폴리오 가치가 0 이하입니다.")

    final_prices = {code: asset_maps[code][dates[-1]] for code in codes}
    final_index = portfolio_value(portfolio, final_prices) / initial_capital
    return {
        "twr_pct": (final_index - 1) * 100,
        "mdd_pct": mdd * 100,
        "turnover_pct": turnover * 100,
    }


def calculate_backtest(
    config,
    asset_closes,
    index_closes,
    transaction_cost_bps=10,
    evaluation_start_date=None,
    initial_capital_krw=10000000,
):
    """Compare HMA and neutral allocations using the live rebalance constraints."""
    if transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps는 0 이상이어야 합니다.")
    if initial_capital_krw <= 0:
        raise ValueError("initial_capital_krw는 0보다 커야 합니다.")

    trend = config["trend_strategy"]
    if not trend.get("enabled") or not trend.get("signals"):
        raise ValueError("복합 HMA 추세 전략이 활성화되어 있어야 합니다.")
    target_codes = list(config["target_weights"])
    if set(target_codes) != set(asset_closes):
        raise ValueError("백테스트 ETF 가격 종목이 목표 비중과 일치하지 않습니다.")

    asset_maps = {
        code: {date: price for date, price in closes}
        for code, closes in asset_closes.items()
    }
    available_dates = set(common_dates(asset_closes))
    states = confirmed_composite_states(config, asset_closes, index_closes)
    dates = sorted(available_dates & set(states))
    if evaluation_start_date:
        requested_start = datetime.strptime(evaluation_start_date, "%Y%m%d").date()
        dates = [date for date in dates if date >= evaluation_start_date]
        if dates and (datetime.strptime(dates[0], "%Y%m%d").date() - requested_start).days > 14:
            raise ValueError(
                f"요청한 평가 시작일의 데이터가 부족합니다: {evaluation_start_date} / {dates[0]}"
            )
    if len(dates) < 2:
        raise ValueError("백테스트에 필요한 확정 추세 구간이 부족합니다.")

    state_counts = {state: 0 for state in STATE_SCORE}
    state_changes = 0
    previous_state = None
    for date in dates[:-1]:
        state = states[date]
        state_counts[state] += 1
        if previous_state is not None and previous_state != state:
            state_changes += 1
        previous_state = state

    periods = len(dates) - 1
    annual_factor = 252 / periods
    hma = simulate_strategy(
        config, asset_maps, dates, states, transaction_cost_bps, initial_capital_krw, True
    )
    fixed = simulate_strategy(
        config, asset_maps, dates, states, transaction_cost_bps, initial_capital_krw, False
    )
    hma_index = 1 + hma["twr_pct"] / 100
    fixed_index = 1 + fixed["twr_pct"] / 100
    return {
        "requested_start_date": evaluation_start_date,
        "start_date": dates[0],
        "end_date": dates[-1],
        "periods": periods,
        "initial_capital_krw": float(initial_capital_krw),
        "transaction_cost_bps": float(transaction_cost_bps),
        "hma_twr_pct": hma["twr_pct"],
        "fixed_twr_pct": fixed["twr_pct"],
        "difference_pct_points": (hma_index - fixed_index) * 100,
        "hma_annualized_pct": (hma_index ** annual_factor - 1) * 100,
        "fixed_annualized_pct": (fixed_index ** annual_factor - 1) * 100,
        "hma_mdd_pct": hma["mdd_pct"],
        "fixed_mdd_pct": fixed["mdd_pct"],
        "hma_turnover_pct": hma["turnover_pct"],
        "fixed_turnover_pct": fixed["turnover_pct"],
        "state_counts": state_counts,
        "state_changes": state_changes,
    }


def fetch_kis_history(config, lookback_days, cache_file=""):
    """Read KIS historical prices only; this function cannot place an order."""
    app_key = kis_client.required("KIS_APP_KEY")
    app_secret = kis_client.required("KIS_APP_SECRET")
    base_url = kis_client.env_value("KIS_API_BASE_URL", kis_client.DEFAULT_BASE_URL)
    token = kis_client.get_access_token(app_key, app_secret, base_url, cache_file)
    context = trading_execution.get_kis_context(token)
    target_codes = config["target_weights"]
    asset_closes = {
        code: trading_execution.fetch_kis_daily_closes(code, context, lookback_days)
        for code in target_codes
    }
    index_closes = {
        signal["symbol"]: trading_execution.fetch_kis_index_daily_closes(
            signal["symbol"], context, lookback_days
        )
        for signal in config["trend_strategy"]["signals"]
        if signal["kind"] == "index"
    }
    return asset_closes, index_closes


def report_markdown(summary):
    counts = summary["state_counts"]
    return "\n".join([
        "# Quant Backtest",
        "",
        f"- 기간: {summary['start_date']} ~ {summary['end_date']} ({summary['periods']} 거래일)",
        f"- 가상 초기자산: ₩{summary['initial_capital_krw']:,.0f}",
        f"- 가정 거래비용: 편도 {summary['transaction_cost_bps']:.1f}bp",
        "- 완료 종가로 계산한 신호를 다음 거래일 종가에 체결한 것으로 보수적으로 가정합니다.",
        "- KIS 수정주가와 실거래의 일일 한도·밴드·정수 수량·2차 주문 규칙을 적용합니다.",
        "- 세금, 실제 호가 스프레드와 체결 실패는 반영하지 않습니다.",
        "",
        "| 전략 | 누적 TWR | 연환산 | 최대낙폭 | 누적 회전율 |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| HMA 목표비중 | {summary['hma_twr_pct']:+.2f}% | {summary['hma_annualized_pct']:+.2f}% | {summary['hma_mdd_pct']:.2f}% | {summary['hma_turnover_pct']:.1f}% |",
        f"| 중립 고정 비중 | {summary['fixed_twr_pct']:+.2f}% | {summary['fixed_annualized_pct']:+.2f}% | {summary['fixed_mdd_pct']:.2f}% | {summary['fixed_turnover_pct']:.1f}% |",
        "",
        f"- 전략 차이: {summary['difference_pct_points']:+.2f}%p",
        f"- 상태 일수: 위험 선호 {counts['risk_on']} / 중립 {counts['neutral']} / 위험 회피 {counts['risk_off']}",
        f"- 상태 전환: {summary['state_changes']}회",
        "",
    ])


def main():
    parser = argparse.ArgumentParser(description="Run a research-only HMA allocation backtest.")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--transaction-cost-bps", type=float, default=10)
    parser.add_argument("--initial-capital-krw", type=float, default=10000000)
    parser.add_argument("--output-dir", default=str(BACKTEST_DIR))
    args = parser.parse_args()
    if args.lookback_days < 300:
        raise ValueError("lookback_days는 HMA200 검증을 위해 300 이상이어야 합니다.")

    config = load_config()
    cache_file = kis_client.env_value("KIS_ACCESS_TOKEN_CACHE_FILE")
    evaluation_end = datetime.now(kis_client.KST).date()
    evaluation_start = evaluation_end - timedelta(days=args.lookback_days)
    history_days = args.lookback_days + warmup_calendar_days(config)
    asset_closes, index_closes = fetch_kis_history(config, history_days, cache_file)
    summary = calculate_backtest(
        config,
        asset_closes,
        index_closes,
        args.transaction_cost_bps,
        evaluation_start.strftime("%Y%m%d"),
        args.initial_capital_krw,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(kis_client.KST).strftime("%Y%m%d_%H%M%S")
    payload = {
        "method": "close-to-close HMA allocation backtest",
        "target_weights": config["target_weights"],
        "summary": summary,
    }
    (output_dir / f"quant_backtest_{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown = report_markdown(summary)
    (output_dir / f"quant_backtest_{stamp}.md").write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
