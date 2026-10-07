#!/usr/bin/env python3
"""Research-only comparison of ETF drawdowns using KIS daily closes and KSD distributions."""

import argparse
from datetime import datetime, timedelta

import kis_client
import trading_execution

DEFAULT_CODES = "486290,0005A0,379810,379800"
DEFAULT_PAIRS = "486290:379810,0005A0:379800"


def fetch_kis_distributions(code, context, start, end, chunk_days=365):
    """Return {record_date: cash per share} from KIS 예탁원정보(배당일정)."""
    result = {}
    cursor = datetime.strptime(start, "%Y%m%d")
    last = datetime.strptime(end, "%Y%m%d")
    while cursor <= last:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), last)
        trading_execution.wait_for_kis_request_slot(context)
        response = context["session"].get(
            f"{context['base_url']}/uapi/domestic-stock/v1/ksdinfo/dividend",
            headers={**context["headers"], "tr_id": "HHKDB669102C0"},
            params={
                "CTS": "",
                "GB1": "0",
                "F_DT": cursor.strftime("%Y%m%d"),
                "T_DT": chunk_end.strftime("%Y%m%d"),
                "SHT_CD": code,
                "HIGH_GB": "",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("rt_cd") != "0":
            raise ValueError(f"KIS 배당일정 조회 실패({code}): {payload.get('msg1', '알 수 없는 오류')}")
        for row in payload.get("output1") or []:
            if str(row.get("sht_cd", "")).strip() not in ("", code):
                continue
            date = str(row.get("record_date", "")).strip()
            amount = kis_client.as_float(row.get("per_sto_divi_amt"), 0)
            if date and amount > 0:
                result[date] = amount
        cursor = chunk_end + timedelta(days=1)
    return result


def total_return_series(closes, distributions):
    """Reinvest each distribution on the last trading day before its record date (ex-date)."""
    dates = [date for date, _ in closes]
    by_ex_date = {}
    for record_date, amount in distributions.items():
        ex_dates = [date for date in dates if date < record_date]
        if ex_dates:
            by_ex_date[ex_dates[-1]] = by_ex_date.get(ex_dates[-1], 0.0) + amount
    series = []
    value = 1.0
    previous = None
    for date, close in closes:
        if previous is not None:
            value *= (close + by_ex_date.get(date, 0.0)) / previous
        series.append((date, value))
        previous = close
    return series


def max_drawdown(series):
    """Return drawdown, peak, trough and recovery dates (recovery None if not regained)."""
    peak_date, peak_value = series[0]
    worst = (0.0, series[0][0], series[0][0])
    for date, value in series:
        if value > peak_value:
            peak_date, peak_value = date, value
        drawdown = value / peak_value - 1
        if drawdown < worst[0]:
            worst = (drawdown, peak_date, date)
    drawdown, peak, trough = worst
    peak_value = dict(series)[peak]
    recovery = next((date for date, value in series if date > trough and value >= peak_value), None)
    return {"drawdown": drawdown, "peak": peak, "trough": trough, "recovery": recovery}


def slice_from(series, start):
    sliced = [(date, value) for date, value in series if date >= start]
    base = sliced[0][1]
    return [(date, value / base) for date, value in sliced]


def summarize(series):
    mdd = max_drawdown(series)
    return {
        "start": series[0][0],
        "end": series[-1][0],
        "return": series[-1][1] / series[0][1] - 1,
        **mdd,
        "current_drawdown": series[-1][1] / max(value for _, value in series) - 1,
    }


def format_row(label, price, total, adjusted):
    recovery = "-" if total["drawdown"] == 0 else total["recovery"] or "미회복"
    return (
        f"| {label} | {price['return'] * 100:+.2f}% | {total['return'] * 100:+.2f}% | "
        f"{adjusted['return'] * 100:+.2f}% | {price['drawdown'] * 100:.2f}% | "
        f"{total['drawdown'] * 100:.2f}% | {adjusted['drawdown'] * 100:.2f}% | "
        f"{total['peak']}→{total['trough']} | {recovery} | {total['current_drawdown'] * 100:.2f}% |"
    )


def report(codes, closes, adjusted_closes, distributions, labels, start):
    lines = [
        f"### 기간 {start} ~ {min(closes[code][-1][0] for code in codes)}",
        "",
        "| 종목 | 원주가 수익률 | 총수익률 | 수정주가 수익률 | 원주가 MDD | 총수익 MDD | 수정주가 MDD | 총수익 MDD 구간 | 회복일 | 현재 낙폭 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for code in codes:
        price = summarize(slice_from(closes[code], start))
        total = summarize(slice_from(total_return_series(closes[code], distributions[code]), start))
        adjusted = summarize(slice_from(adjusted_closes[code], start))
        lines.append(format_row(f"{labels.get(code, code)} `{code}`", price, total, adjusted))
    return lines


def daily_returns(closes, dates):
    prices = dict(closes)
    return [prices[today] / prices[yesterday] - 1 for yesterday, today in zip(dates, dates[1:])]


def correlation(left, right):
    n = len(left)
    mean_left, mean_right = sum(left) / n, sum(right) / n
    covariance = sum((x - mean_left) * (y - mean_right) for x, y in zip(left, right))
    spread = (
        sum((x - mean_left) ** 2 for x in left) * sum((y - mean_right) ** 2 for y in right)
    ) ** 0.5
    return covariance / spread if spread else 0.0


def correlation_lines(codes, adjusted_closes, labels, start):
    """Daily-return correlation matrix on common trading dates from start."""
    dates = sorted(
        set.intersection(*({date for date, _ in adjusted_closes[code]} for code in codes))
    )
    dates = [date for date in dates if date >= start]
    returns = {code: daily_returns(adjusted_closes[code], dates) for code in codes}
    names = [f"{labels.get(code, code)}" for code in codes]
    lines = [
        "",
        f"### 일간 수익률 상관계수 ({dates[0]} ~ {dates[-1]})",
        "",
        "| 종목 | " + " | ".join(names) + " |",
        "| --- |" + " ---: |" * len(codes),
    ]
    for code, name in zip(codes, names):
        lines.append(
            f"| {name} | "
            + " | ".join(f"{correlation(returns[code], returns[other]):.2f}" for other in codes)
            + " |"
        )
    return lines


def main():
    parser = argparse.ArgumentParser(description="Compare ETF drawdowns with KIS data (no orders).")
    parser.add_argument("--codes", default=DEFAULT_CODES)
    parser.add_argument("--pairs", default=DEFAULT_PAIRS)
    parser.add_argument("--lookback-days", type=int, default=1500)
    args = parser.parse_args()

    codes = [code.strip() for code in args.codes.split(",") if code.strip()]
    context = trading_execution.get_kis_context()
    labels = trading_execution.load_asset_labels()
    closes, adjusted_closes, distributions = {}, {}, {}
    for code in codes:
        closes[code] = trading_execution.fetch_kis_daily_closes(
            code, context, args.lookback_days, adjusted=False
        )
        adjusted_closes[code] = trading_execution.fetch_kis_daily_closes(code, context, args.lookback_days)
        if not closes[code]:
            raise ValueError(f"일봉 데이터가 없습니다: {code}")
        try:
            distributions[code] = fetch_kis_distributions(
                code, context, closes[code][0][0], closes[code][-1][0]
            )
        except Exception as exc:
            print(f"WARNING: {code} 분배금 조회 실패, 가격 기준만 사용: {exc}")
            distributions[code] = {}
        print(
            f"{code}: {len(closes[code])} closes from {closes[code][0][0]}, "
            f"{len(distributions[code])} distributions, total {sum(distributions[code].values()):,.0f}원/주"
        )

    lines = ["# ETF 낙폭 비교 (KIS)", "", "- 총수익은 원주가에 분배금을 기준일 직전 거래일(배당락)에 재투자한 값입니다. KIS 수정주가는 분배금이 반영된 교차검증용입니다.", ""]
    lines += report(codes, closes, adjusted_closes, distributions, labels, max(closes[code][0][0] for code in codes))
    lines += correlation_lines(
        codes, adjusted_closes, labels, max(closes[code][0][0] for code in codes)
    )
    for pair in args.pairs.split(","):
        if ":" not in pair:
            continue
        pair_codes = [code.strip() for code in pair.split(":")]
        if all(code in closes for code in pair_codes):
            lines += [""] + report(
                pair_codes, closes, adjusted_closes, distributions, labels,
                max(closes[code][0][0] for code in pair_codes),
            )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
