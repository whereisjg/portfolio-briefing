"""Research-only backtest for the configured HMA allocation strategy.

This module reads historical closes and writes reports. It deliberately has no
order-submission path, so a backtest cannot place a live order.
"""

import argparse
import json
import math
from datetime import datetime
from pathlib import Path

import kis_client
import trading_execution
from trading_strategy import calculate_trend_state, load_config, weighted_close_series


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


def calculate_backtest(config, asset_closes, index_closes, transaction_cost_bps=10):
    """Compare confirmed HMA allocations with neutral fixed weights close-to-close."""
    if transaction_cost_bps < 0:
        raise ValueError("transaction_cost_bps는 0 이상이어야 합니다.")

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
    if len(dates) < 2:
        raise ValueError("백테스트에 필요한 확정 추세 구간이 부족합니다.")

    neutral_weights = {code: float(weight) for code, weight in trend["weights"]["neutral"].items()}
    previous_hma_weights = neutral_weights
    hma_index = 1.0
    fixed_index = 1.0
    hma_peak = 1.0
    fixed_peak = 1.0
    hma_mdd = 0.0
    fixed_mdd = 0.0
    turnover = 0.0
    state_counts = {state: 0 for state in STATE_SCORE}
    state_changes = 0
    previous_state = None

    for date, next_date in zip(dates, dates[1:]):
        state = states[date]
        hma_weights = {code: float(weight) for code, weight in trend["weights"][state].items()}
        state_counts[state] += 1
        if previous_state is not None and previous_state != state:
            state_changes += 1
        previous_state = state

        period_turnover = 0.5 * sum(
            abs(hma_weights[code] - previous_hma_weights[code])
            for code in target_codes
        ) / 100
        turnover += period_turnover
        cost = period_turnover * float(transaction_cost_bps) / 10000
        hma_return = sum(
            hma_weights[code] / 100 * (asset_maps[code][next_date] / asset_maps[code][date] - 1)
            for code in target_codes
        )
        fixed_return = sum(
            neutral_weights[code] / 100 * (asset_maps[code][next_date] / asset_maps[code][date] - 1)
            for code in target_codes
        )
        hma_index *= (1 - cost) * (1 + hma_return)
        fixed_index *= 1 + fixed_return
        hma_peak = max(hma_peak, hma_index)
        fixed_peak = max(fixed_peak, fixed_index)
        hma_mdd = min(hma_mdd, hma_index / hma_peak - 1)
        fixed_mdd = min(fixed_mdd, fixed_index / fixed_peak - 1)
        previous_hma_weights = hma_weights

    periods = len(dates) - 1
    annual_factor = 252 / periods
    return {
        "start_date": dates[0],
        "end_date": dates[-1],
        "periods": periods,
        "transaction_cost_bps": float(transaction_cost_bps),
        "hma_twr_pct": (hma_index - 1) * 100,
        "fixed_twr_pct": (fixed_index - 1) * 100,
        "difference_pct_points": (hma_index - fixed_index) * 100,
        "hma_annualized_pct": (hma_index ** annual_factor - 1) * 100,
        "fixed_annualized_pct": (fixed_index ** annual_factor - 1) * 100,
        "hma_mdd_pct": hma_mdd * 100,
        "fixed_mdd_pct": fixed_mdd * 100,
        "hma_turnover_pct": turnover * 100,
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
        f"- 가정 거래비용: 편도 {summary['transaction_cost_bps']:.1f}bp",
        "- 종가 기준으로 신호를 계산하고 다음 거래일 수익률에 적용합니다.",
        "- 분배금, 세금, 호가 스프레드, 체결 실패는 반영하지 않은 가격 수익률 분석입니다.",
        "",
        "| 전략 | 누적 TWR | 연환산 | 최대낙폭 | 누적 회전율 |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| HMA 목표비중 | {summary['hma_twr_pct']:+.2f}% | {summary['hma_annualized_pct']:+.2f}% | {summary['hma_mdd_pct']:.2f}% | {summary['hma_turnover_pct']:.1f}% |",
        f"| 중립 고정 비중 | {summary['fixed_twr_pct']:+.2f}% | {summary['fixed_annualized_pct']:+.2f}% | {summary['fixed_mdd_pct']:.2f}% | 0.0% |",
        "",
        f"- 전략 차이: {summary['difference_pct_points']:+.2f}%p",
        f"- 상태 일수: 위험 선호 {counts['risk_on']} / 중립 {counts['neutral']} / 위험 회피 {counts['risk_off']}",
        f"- 상태 전환: {summary['state_changes']}회",
        "",
    ])


def main():
    parser = argparse.ArgumentParser(description="Run a research-only HMA allocation backtest.")
    parser.add_argument("--lookback-days", type=int, default=1260)
    parser.add_argument("--transaction-cost-bps", type=float, default=10)
    parser.add_argument("--output-dir", default=str(BACKTEST_DIR))
    args = parser.parse_args()
    if args.lookback_days < 300:
        raise ValueError("lookback_days는 HMA200 검증을 위해 300 이상이어야 합니다.")

    config = load_config()
    cache_file = kis_client.env_value("KIS_ACCESS_TOKEN_CACHE_FILE")
    asset_closes, index_closes = fetch_kis_history(config, args.lookback_days, cache_file)
    summary = calculate_backtest(config, asset_closes, index_closes, args.transaction_cost_bps)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(kis_client.KST).strftime("%Y%m%d")
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
