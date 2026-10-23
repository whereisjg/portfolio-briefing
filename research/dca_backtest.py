#!/usr/bin/env python3
"""Research-only daily DCA backtest of US ETF mixes (no orders, not advice).

Assumptions: dividend-adjusted closes (dividends reinvested), daily equal
contributions split by target weight, fractional shares, no tax, FX or fees.
"""

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

TICKERS = ["QLD", "TQQQ", "SCHD", "DGRO", "VOO", "QQQ", "IEF", "TLT", "GLD"]
PORTFOLIOS = {
    "A QLD100": {"QLD": 100},
    "B TQQQ50/SCHD50": {"TQQQ": 50, "SCHD": 50},
    "C QLD50/SCHD25/DGRO25": {"QLD": 50, "SCHD": 25, "DGRO": 25},
    "D QLD40/SCHD20/IEF25/GLD15": {"QLD": 40, "SCHD": 20, "IEF": 25, "GLD": 15},
    "E VOO100": {"VOO": 100},
    "F QQQ100": {"QQQ": 100},
    "G QQQ50/SCHD50": {"QQQ": 50, "SCHD": 50},
    "H VOO60/IEF25/GLD15": {"VOO": 60, "IEF": 25, "GLD": 15},
}
STRESS_WINDOWS = {
    "2020 코로나 (02-19~03-23)": ("2020-02-19", "2020-03-23"),
    "2022 하락장 (01-03~12-30)": ("2022-01-03", "2022-12-30"),
}
DAILY_CONTRIBUTION = 10.0  # USD per trading day
TRADING_DAYS = 252


def load_prices(start, synthetic=False):
    if synthetic:
        rng = np.random.default_rng(0)
        index = pd.bdate_range(start, "2026-10-15")
        base = rng.normal(0.0005, 0.012, len(index))
        prices = {}
        for i, ticker in enumerate(TICKERS):
            lev = {"QLD": 2, "TQQQ": 3}.get(ticker, 1)
            noise = rng.normal(0, 0.004, len(index))
            prices[ticker] = 100 * np.cumprod(1 + lev * base + noise * (1 + i % 3))
        return pd.DataFrame(prices, index=index)
    import yfinance as yf

    data = yf.download(TICKERS, start=start, auto_adjust=True, progress=False)["Close"]
    data = data[TICKERS].dropna(how="all")
    first_common = data.apply(lambda s: s.first_valid_index()).max()
    return data.loc[first_common:].dropna()


def simulate(prices, weights, rebalance):
    """Daily DCA. Returns account value, cumulative invested, and a TWR index."""
    codes = list(weights)
    target = np.array([weights[c] for c in codes], dtype=float)
    target /= target.sum()
    px = prices[codes].to_numpy()
    shares = np.zeros(len(codes))
    values, invested, twr = [], [], []
    nav, prev_value, total_in = 1.0, 0.0, 0.0
    years = prices.index.year
    for t in range(len(prices)):
        value_before = float(shares @ px[t])
        if t > 0 and prev_value > 0:
            nav *= value_before / prev_value
        if rebalance and t > 0 and years[t] != years[t - 1]:
            shares = value_before * target / px[t]
        shares = shares + DAILY_CONTRIBUTION * target / px[t]
        total_in += DAILY_CONTRIBUTION
        prev_value = float(shares @ px[t])
        values.append(prev_value)
        invested.append(total_in)
        twr.append(nav)
    index = prices.index
    return pd.Series(values, index), pd.Series(invested, index), pd.Series(twr, index)


def xirr(dates, flows):
    days = np.array([(d - dates[0]).days for d in dates], dtype=float) / 365.0
    flows = np.array(flows, dtype=float)
    low, high = -0.99, 5.0
    npv = lambda r: float(np.sum(flows / (1 + r) ** days))
    for _ in range(200):
        mid = (low + high) / 2
        if npv(mid) > 0:
            low = mid
        else:
            high = mid
    return mid


def drawdown_info(twr):
    peak = twr.cummax()
    dd = twr / peak - 1
    trough = dd.idxmin()
    peak_date = twr.loc[:trough].idxmax()
    after = twr.loc[trough:]
    recovered = after[after >= twr.loc[peak_date]]
    recovery = recovered.index[0].date() if len(recovered) else None
    return dd, float(dd.min()), peak_date.date(), trough.date(), recovery


def metrics(name, rebalance, value, invested, twr):
    dates = list(value.index)
    flows = [-DAILY_CONTRIBUTION] * len(dates)
    flows[-1] += float(value.iloc[-1])
    rets = twr.pct_change().dropna()
    vol = float(rets.std() * np.sqrt(TRADING_DAYS))
    years = (dates[-1] - dates[0]).days / 365.25
    twr_cagr = float(twr.iloc[-1] ** (1 / years) - 1)
    _, mdd, peak, trough, recovery = drawdown_info(twr)
    return {
        "포트폴리오": name,
        "리밸런싱": "연1회" if rebalance else "없음",
        "투입원금": round(float(invested.iloc[-1])),
        "최종평가액": round(float(value.iloc[-1])),
        "원금대비수익률%": round((value.iloc[-1] / invested.iloc[-1] - 1) * 100, 1),
        "IRR%": round(xirr(dates, flows) * 100, 2),
        "TWR_CAGR%": round(twr_cagr * 100, 2),
        "MDD%": round(mdd * 100, 1),
        "MDD구간": f"{peak}→{trough}",
        "회복일": str(recovery) if recovery else "미회복",
        "연변동성%": round(vol * 100, 1),
        "Sharpe(rf=0)": round(float(rets.mean() * TRADING_DAYS / vol), 2) if vol else None,
    }


def stress_table(results):
    rows = []
    for (name, rebalance), (_, _, twr) in results.items():
        if rebalance:
            continue
        row = {"포트폴리오": name}
        for label, (start, end) in STRESS_WINDOWS.items():
            window = twr.loc[start:end]
            row[label] = round((window.iloc[-1] / window.iloc[0] - 1) * 100, 1) if len(window) > 1 else None
            row[label + " MDD"] = round(float((window / window.cummax() - 1).min()) * 100, 1) if len(window) > 1 else None
        rows.append(row)
    return pd.DataFrame(rows)


def decay_table(prices):
    qqq = prices["QQQ"].pct_change().dropna()
    years = (prices.index[-1] - prices.index[0]).days / 365.25
    rows = []
    for ticker, lev in (("QLD", 2), ("TQQQ", 3)):
        actual = prices[ticker].iloc[-1] / prices[ticker].iloc[0] - 1
        daily_synth = float(np.prod(1 + lev * qqq) - 1)
        naive = lev * float(prices["QQQ"].iloc[-1] / prices["QQQ"].iloc[0] - 1)
        cagr = lambda total: ((1 + total) ** (1 / years) - 1) * 100
        rows.append({
            "ETF": ticker,
            "배율": lev,
            "실제 누적%": round(actual * 100, 1),
            "QQQ 일간배율 합성(비용0) 누적%": round(daily_synth * 100, 1),
            "QQQ 누적×배율 단순%": round(naive * 100, 1),
            "실제 CAGR%": round(cagr(actual), 2),
            "합성 CAGR%": round(cagr(daily_synth), 2),
            "QQQ CAGR×배율%": round(cagr(float(prices['QQQ'].iloc[-1] / prices['QQQ'].iloc[0] - 1)) * lev, 2),
            "비용·괴리 연%": round(cagr(daily_synth) - cagr(actual), 2),
        })
    for label, (start, end) in STRESS_WINDOWS.items():
        window = prices.loc[start:end]
        if len(window) < 2:
            continue
        q = window["QQQ"].iloc[-1] / window["QQQ"].iloc[0] - 1
        for ticker, lev in (("QLD", 2), ("TQQQ", 3)):
            a = window[ticker].iloc[-1] / window[ticker].iloc[0] - 1
            rows.append({"ETF": f"{ticker} {label}", "배율": lev, "실제 누적%": round(a * 100, 1),
                         "QQQ 누적×배율 단순%": round(q * lev * 100, 1)})
    return pd.DataFrame(rows)


def plot(results, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 6))
    invested = None
    for (name, rebalance), (value, inv, _) in results.items():
        if rebalance:
            ax.plot(value.index, value / 1000, label=name, linewidth=1.2)
            invested = inv
    ax.plot(invested.index, invested / 1000, "k--", label="Invested", linewidth=1)
    ax.set_title("Daily DCA $10/day, annual rebalance — account value (k USD)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "value.png", dpi=120)

    fig, ax = plt.subplots(figsize=(11, 6))
    for (name, rebalance), (_, _, twr) in results.items():
        if rebalance:
            ax.plot(twr.index, (twr / twr.cummax() - 1) * 100, label=name, linewidth=1)
    ax.set_title("Drawdown of time-weighted index (%), annual rebalance")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "drawdown.png", dpi=120)


def summary_lines(table, decay, prices):
    reb = table[table["리밸런싱"] == "연1회"].set_index("포트폴리오")
    best = reb["IRR%"].idxmax()
    calm = reb["MDD%"].idxmax()
    sharpe = reb["Sharpe(rf=0)"].idxmax()
    lines = [
        f"기간 {prices.index[0].date()}~{prices.index[-1].date()}, 매 거래일 ${DAILY_CONTRIBUTION:g} 적립, 연 1회 리밸런싱 기준.",
        f"IRR 최고는 {best}({reb.loc[best, 'IRR%']}%), MDD {reb.loc[best, 'MDD%']}%.",
        f"낙폭이 가장 작은 건 {calm}(MDD {reb.loc[calm, 'MDD%']}%, IRR {reb.loc[calm, 'IRR%']}%), Sharpe 최고는 {sharpe}({reb.loc[sharpe, 'Sharpe(rf=0)']}).",
        f"벤치마크: VOO IRR {reb.loc['E VOO100', 'IRR%']}% / MDD {reb.loc['E VOO100', 'MDD%']}%, QQQ IRR {reb.loc['F QQQ100', 'IRR%']}% / MDD {reb.loc['F QQQ100', 'MDD%']}%.",
    ]
    d = decay.set_index("ETF")
    for ticker in ("QLD", "TQQQ"):
        crash = d.loc[f"{ticker} 2022 하락장 (01-03~12-30)"]
        lines.append(
            f"{ticker}: 전체 CAGR {d.loc[ticker, '실제 CAGR%']}% (QQQ CAGR×{d.loc[ticker, '배율']} = "
            f"{d.loc[ticker, 'QQQ CAGR×배율%']}%), 2022년 {crash['실제 누적%']}% (QQQ×배율 단순 {crash['QQQ 누적×배율 단순%']}%)."
        )
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2014-06-01")
    parser.add_argument("--out", default="research/dca_output")
    parser.add_argument("--synthetic", action="store_true", help="offline logic check with random prices")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    prices = load_prices(args.start, args.synthetic)
    results = {}
    for name, weights in PORTFOLIOS.items():
        for rebalance in (True, False):
            results[(name, rebalance)] = simulate(prices, weights, rebalance)

    table = pd.DataFrame([metrics(n, r, *results[(n, r)]) for (n, r) in results])
    stress = stress_table(results)
    decay = decay_table(prices)
    table.to_csv(out_dir / "metrics.csv", index=False, encoding="utf-8-sig")
    stress.to_csv(out_dir / "stress.csv", index=False, encoding="utf-8-sig")
    decay.to_csv(out_dir / "volatility_decay.csv", index=False, encoding="utf-8-sig")
    plot(results, out_dir)

    header = [
        "# ETF 적립식 백테스트 (과거 데이터 비교, 투자 권유 아님)",
        "",
        f"- 데이터: {'가상 데이터(로직 점검용)' if args.synthetic else 'yfinance 수정주가(배당 재투자)'}, "
        f"{prices.index[0].date()} ~ {prices.index[-1].date()} ({len(prices)}거래일)",
        f"- 매 거래일 ${DAILY_CONTRIBUTION:g}를 목표 비중대로 나눠 매수, 소수점 매수 허용",
        "- 세금·환율·수수료 무시. Sharpe는 무위험수익률 0 가정",
        "- IRR은 적립 현금흐름 기준 연수익률, MDD·변동성·Sharpe는 적립 효과를 뺀 시간가중(TWR) 지수 기준",
        "",
    ]
    report = header + ["## 요약", ""] + [f"- {l}" for l in summary_lines(table, decay, prices)] + [
        "", "## 지표", "", table.to_markdown(index=False),
        "", "## 하락장 구간 (리밸런싱 없음, TWR)", "", stress.to_markdown(index=False),
        "", "## 레버리지 변동성 손실", "", decay.to_markdown(index=False), "",
    ]
    (out_dir / "report.md").write_text("\n".join(report), encoding="utf-8")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 20)
    print("\n".join(header))
    print(table.to_string(index=False))
    print()
    print(stress.to_string(index=False))
    print()
    print(decay.to_string(index=False))
    print()
    print("\n".join(summary_lines(table, decay, prices)))


if __name__ == "__main__":
    main()
