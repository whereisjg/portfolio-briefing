import unittest
from unittest.mock import patch
import tempfile
import json

import portfolio_briefing as briefing
import kis_client
import market_calendar
import performance_tracking as performance
import quant_backtest
import trading_execution as trading
import trading_strategy as strategy


class FormattingTests(unittest.TestCase):
    def test_formats_signed_usd_amount(self):
        self.assertEqual(briefing.format_signed_amount(12.3, "USD"), "+$12.30")
        self.assertEqual(briefing.format_signed_amount(-4.5, "USD"), "-$4.50")

    def test_market_snapshot_summarizes_direction(self):
        quotes = [
            {"ticker": "QLD", "chg_pct": -3.77},
            {"ticker": "SSO", "chg_pct": -2.03},
            {"ticker": "AMD", "chg_pct": 2.95},
        ]

        self.assertEqual(
            briefing.market_snapshot(quotes),
            "3개 중 상승 1개, 하락 2개 / 상대강세 AMD +2.95% / 최대약세 QLD -3.77%",
        )

    def test_alerts_do_not_report_missing_news(self):
        quotes = [{"ticker": "SCHD", "chg_pct": 0.5}]

        self.assertEqual(briefing.build_alert_lines(quotes, []), ["특이사항 없음"])


class PerformanceTrackingTests(unittest.TestCase):
    def test_strategy_twr_compares_hma_with_fixed_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/strategy_twr.json"
            first = performance.update_strategy_comparison(
                path,
                "2026-08-07",
                {"A": 100, "B": 100},
                {"A": 50, "B": 50},
                {"A": 25, "B": 75},
            )
            second = performance.update_strategy_comparison(
                path,
                "2026-08-08",
                {"A": 110, "B": 100},
                {"A": 25, "B": 75},
                {"A": 25, "B": 75},
            )

            self.assertEqual(first["periods"], 0)
            self.assertAlmostEqual(second["hma_twr_pct"], 5.0)
            self.assertAlmostEqual(second["fixed_twr_pct"], 2.5)
            self.assertAlmostEqual(second["difference_pct_points"], 2.5)
            self.assertAlmostEqual(second["hma_turnover_pct"], 25.0)

    def test_strategy_twr_replaces_same_day_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/strategy_twr.json"
            performance.update_strategy_comparison(
                path,
                "2026-08-07",
                {"A": 100},
                {"A": 100},
                {"A": 100},
            )
            performance.update_strategy_comparison(
                path,
                "2026-08-08",
                {"A": 110},
                {"A": 100},
                {"A": 100},
            )
            summary = performance.update_strategy_comparison(
                path,
                "2026-08-08",
                {"A": 120},
                {"A": 100},
                {"A": 100},
            )

            history = performance.load_history(path)
            self.assertEqual(len(history["records"]), 2)
            self.assertAlmostEqual(summary["hma_twr_pct"], 20.0)

    def test_strategy_twr_geometrically_links_returns_and_tracks_drawdown(self):
        records = [
            {"date": "2026-08-07", "prices": {"A": 100}, "hma_weights": {"A": 100}, "fixed_weights": {"A": 100}},
            {"date": "2026-08-08", "prices": {"A": 110}, "hma_weights": {"A": 100}, "fixed_weights": {"A": 100}},
            {"date": "2026-08-09", "prices": {"A": 99}, "hma_weights": {"A": 100}, "fixed_weights": {"A": 100}},
        ]

        summary = performance.calculate_summary(records)

        self.assertAlmostEqual(summary["hma_twr_pct"], -1.0)
        self.assertAlmostEqual(summary["hma_mdd_pct"], -10.0)

    def test_briefing_shows_compact_strategy_twr(self):
        quotes = [{
            "ticker": "ETF",
            "display": "ETF",
            "name": "ETF",
            "currency": "KRW",
            "price": 10000,
            "prev_close": 10000,
            "chg_amount": 0,
            "chg_pct": 0,
            "provider": "KIS",
        }]
        performance_summary = {
            "start_date": "2026-08-07",
            "end_date": "2026-08-08",
            "observations": 2,
            "periods": 1,
            "hma_twr_pct": 1.25,
            "fixed_twr_pct": 1.0,
            "difference_pct_points": 0.25,
            "hma_mdd_pct": -0.5,
            "fixed_mdd_pct": -0.8,
            "hma_turnover_pct": 10.0,
        }

        telegram, markdown = briefing.build_content(
            [], quotes, [], performance_summary=performance_summary
        )

        self.assertIn("📐 전략 TWR · HMA +1.25% · 고정 +1.00% · 차이 +0.25%p", telegram)
        self.assertIn("## 📐 전략 TWR 비교", markdown)
        self.assertIn("최대낙폭: HMA -0.50% / 고정 -0.80%", markdown)


class QuantBacktestTests(unittest.TestCase):
    def test_backtest_executes_a_close_signal_on_the_next_trading_day(self):
        config = {
            "target_weights": {"A": 50, "B": 50},
            "daily_buy_limit_pct": 100,
            "daily_sell_limit_pct": 100,
            "daily_sell_limit_per_asset_krw": 10000,
            "rebalance_band_pct": 0,
            "trend_strategy": {
                "weights": {
                    "risk_on": {"A": 100, "B": 0},
                    "neutral": {"A": 50, "B": 50},
                    "risk_off": {"A": 0, "B": 100},
                },
            },
        }
        asset_maps = {
            "A": {"20260101": 100, "20260102": 200, "20260103": 200},
            "B": {"20260101": 100, "20260102": 100, "20260103": 100},
        }
        states = {"20260101": "risk_on", "20260102": "risk_on", "20260103": "risk_on"}

        result = quant_backtest.simulate_strategy(
            config,
            asset_maps,
            ["20260101", "20260102", "20260103"],
            states,
            transaction_cost_bps=0,
            initial_capital=1000,
            dynamic=True,
        )

        self.assertAlmostEqual(result["twr_pct"], 50.0)

    def test_backtest_uses_confirmed_signal_without_future_prices(self):
        config = {
            "target_weights": {"A": 50, "B": 25, "C": 25},
            "trend_strategy": {
                "enabled": True,
                "average_type": "sma",
                "short_window_days": 2,
                "long_window_days": 3,
                "confirmation_days": 2,
                "composite_threshold": 0.5,
                "signals": [{"kind": "portfolio", "label": "위험자산", "weight_pct": 100, "codes": ["A", "B"]}],
                "weights": {
                    "risk_on": {"A": 70, "B": 15, "C": 15},
                    "neutral": {"A": 50, "B": 25, "C": 25},
                    "risk_off": {"A": 20, "B": 20, "C": 60},
                },
            },
        }
        asset_closes = {
            "A": [(f"202601{day:02d}", price) for day, price in enumerate((100, 101, 103, 106, 105, 103, 100, 98, 97, 99), 1)],
            "B": [(f"202601{day:02d}", price) for day, price in enumerate((100, 100, 101, 102, 101, 100, 99, 98, 98, 99), 1)],
            "C": [(f"202601{day:02d}", 100) for day in range(1, 11)],
        }

        no_cost = quant_backtest.calculate_backtest(config, asset_closes, {}, 0)
        with_cost = quant_backtest.calculate_backtest(config, asset_closes, {}, 100)

        self.assertEqual(no_cost["periods"], 7)
        self.assertGreater(no_cost["state_changes"], 0)
        self.assertLess(with_cost["hma_twr_pct"], no_cost["hma_twr_pct"])
        self.assertLess(with_cost["fixed_twr_pct"], no_cost["fixed_twr_pct"])
        self.assertGreater(with_cost["fixed_turnover_pct"], 0)

    def test_hma_warmup_is_added_before_the_requested_evaluation_period(self):
        config = strategy.load_config()

        self.assertEqual(quant_backtest.required_trend_closes(config), 214)
        self.assertGreater(quant_backtest.warmup_calendar_days(config), 300)


class ConfigurationTests(unittest.TestCase):
    def test_repository_config_replaces_topix_with_unhedged_nikkei225(self):
        config = strategy.load_config()

        self.assertEqual(config["target_weights"]["241180"], 20)
        self.assertNotIn("101280", config["target_weights"])
        self.assertNotIn("0036D0", config["target_weights"])
        self.assertIn("0036D0", config["liquidation_codes"])
        self.assertIn("101280", config["liquidation_codes"])
        portfolio_signal = next(
            signal for signal in config["trend_strategy"]["signals"]
            if signal["kind"] == "portfolio"
        )
        self.assertIn("241180", portfolio_signal["codes"])
        self.assertNotIn("101280", portfolio_signal["codes"])
        self.assertNotIn("0036D0", portfolio_signal["codes"])
        for state in ("risk_on", "neutral", "risk_off"):
            self.assertEqual(config["trend_strategy"]["weights"][state]["241180"], 20)

        _indexes, assets = briefing.load_portfolio()
        nikkei = next(asset for asset in assets if asset["symbol"] == "241180.KS")
        self.assertEqual(nikkei["name"], "TIGER 일본니케이225")
        self.assertEqual(nikkei["target_weight_pct"], 20)
        topix = next(asset for asset in assets if asset["symbol"] == "101280.KS")
        self.assertEqual(topix["name"], "KODEX 일본TOPIX100")
        self.assertIsNone(topix["target_weight_pct"])
        time_dividend = next(asset for asset in assets if asset["symbol"] == "0036D0.KS")
        self.assertIsNone(time_dividend["target_weight_pct"])

    def test_env_value_uses_default_for_empty_environment_value(self):
        with patch.dict(briefing.os.environ, {"EMPTY_SETTING": ""}):
            self.assertEqual(
                briefing.env_value("EMPTY_SETTING", "fallback"),
                "fallback",
            )

    def test_load_portfolio_warns_when_target_weights_are_invalid(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as file:
            json.dump(
                {
                    "assets": [
                        {"ticker": "A", "target_weight_pct": 60},
                        {"ticker": "B", "target_weight_pct": 30},
                    ],
                },
                file,
            )
            file.flush()
            with patch.object(briefing, "PORTFOLIO_FILE", file.name):
                with patch("builtins.print") as print_mock:
                    briefing.load_portfolio()

        self.assertTrue(
            any("목표 비중 합계 90.00%" in str(call) for call in print_mock.call_args_list)
        )

    def test_strategy_module_does_not_depend_on_kis_or_briefing_modules(self):
        self.assertNotIn("portfolio_briefing", strategy.__dict__)
        self.assertNotIn("trade_automation", strategy.__dict__)

    def test_legacy_trade_automation_entry_point_exports_executor(self):
        import trade_automation

        self.assertIs(trade_automation.plan_orders, trading.plan_orders)

    def test_execution_module_does_not_depend_on_briefing_module(self):
        self.assertNotIn("portfolio_briefing", trading.__dict__)

    def test_plan_format_uses_asset_labels(self):
        plan = {
            "total_value": 100000,
            "cash": 100000,
            "orderable_cash": 100000,
            "daily_buy_limit": 100000,
            "daily_sell_limit": 100000,
            "daily_buy_cap": None,
            "sells": [],
            "buys": [{"code": "0015B0", "quantity": 1, "price": 21000, "value": 21000}],
            "unallocated_cash": 79000,
            "trend": {"state": "neutral", "signal_code": "0015B0", "latest_date": "20260716", "latest_close": 21335, "short_average": 23750, "long_average": 22881},
        }

        report = strategy.format_plan(plan, asset_labels={"0015B0": "KoAct나스닥성장"})

        self.assertIn("🤖 자동매매 dry-run · 매수 10만 · 매도 10만", report)
        self.assertIn("KoAct나스닥성장 1주", report)
        self.assertNotIn("0015B0 1주", report)

class KisBalanceTests(unittest.TestCase):
    def test_krx_market_status_closes_constitution_day_and_weekends(self):
        self.assertFalse(market_calendar.is_krx_trading_day(market_calendar.date(2026, 7, 17)))
        self.assertEqual(
            market_calendar.krx_market_status(market_calendar.date(2026, 7, 17))["reason"],
            "제헌절",
        )
        self.assertFalse(market_calendar.is_krx_trading_day(market_calendar.date(2026, 7, 18)))
        self.assertTrue(market_calendar.is_krx_trading_day(market_calendar.date(2026, 7, 20)))

    def test_krx_order_status_closes_holidays_and_outside_regular_hours(self):
        holiday = market_calendar.KST.localize(market_calendar.datetime(2026, 9, 24, 10))
        before_open = market_calendar.KST.localize(market_calendar.datetime(2026, 7, 20, 8, 59))
        trading = market_calendar.KST.localize(market_calendar.datetime(2026, 7, 20, 10))
        after_cutoff = market_calendar.KST.localize(market_calendar.datetime(2026, 7, 20, 15, 20))

        self.assertFalse(market_calendar.krx_order_status(holiday)["orderable"])
        self.assertFalse(market_calendar.krx_order_status(before_open)["orderable"])
        self.assertTrue(market_calendar.krx_order_status(trading)["orderable"])
        self.assertFalse(market_calendar.krx_order_status(after_cutoff)["orderable"])

    def test_static_calendar_fails_closed_for_an_unregistered_year(self):
        status = market_calendar.krx_market_status(market_calendar.date(2027, 1, 4))

        self.assertFalse(status["open"])
        self.assertEqual(status["reason"], "KRX 휴장일 정보 미등록")

    def test_kis_market_status_uses_official_open_flag(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "rt_cd": "0",
                    "output": [{"bass_dt": "20260720", "opnd_yn": "Y"}],
                }

        class FakeSession:
            def __init__(self):
                self.get_kwargs = None

            def post(self, *_args, **_kwargs):
                return type("TokenResponse", (), {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {"access_token": "token"},
                })()

            def get(self, *_args, **kwargs):
                self.get_kwargs = kwargs
                return FakeResponse()

        session = FakeSession()
        environment = {
            "KIS_APP_KEY": "key",
            "KIS_APP_SECRET": "secret",
            "KIS_API_BASE_URL": "https://example.test",
        }
        with patch.dict(kis_client.os.environ, environment, clear=True):
            status = market_calendar.fetch_kis_krx_market_status(
                market_calendar.date(2026, 7, 20),
                session_factory=lambda retries: session,
            )

        self.assertTrue(status["open"])
        self.assertEqual(session.get_kwargs["headers"]["tr_id"], "CTCA0903R")
        self.assertEqual(session.get_kwargs["params"]["BASS_DT"], "20260720")

    def test_kis_order_status_fails_closed_when_market_check_fails(self):
        now = market_calendar.KST.localize(market_calendar.datetime(2026, 7, 20, 10))

        status = market_calendar.kis_krx_order_status(
            now,
            market_status_fetcher=lambda _day: (_ for _ in ()).throw(RuntimeError("timeout")),
        )

        self.assertFalse(status["open"])
        self.assertFalse(status["orderable"])
        self.assertIn("KIS 거래일 확인 실패", status["reason"])

    def test_paper_balance_uses_virtual_tr_id_and_unpr_dvsn(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"rt_cd": "0", "output1": [{"pdno": "0015B0"}], "output2": [{}]}

        class FakeSession:
            def post(self, *args, **kwargs):
                return type("TokenResponse", (), {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {"access_token": "token"},
                })()

            def get(self, *args, **kwargs):
                self.kwargs = kwargs
                return FakeResponse()

        session = FakeSession()
        environment = {
            "KIS_APP_KEY": "key",
            "KIS_APP_SECRET": "secret",
            "KIS_ACCOUNT_NO": "12345678",
            "KIS_PRODUCT_CODE": "01",
            "KIS_ACCOUNT_MODE": "paper",
        }
        with patch.dict(briefing.os.environ, environment):
            with patch.object(briefing, "get_http_session", return_value=session):
                holdings, _summary, _token = briefing.fetch_kis_balance()

        self.assertEqual(session.kwargs["headers"]["tr_id"], "VTTC8434R")
        self.assertEqual(session.kwargs["params"]["UNPR_DVSN"], "01")
        self.assertEqual(holdings, [{"pdno": "0015B0"}])

    def test_balance_retries_transient_mci_error(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeSession:
            def __init__(self):
                self.responses = [
                    FakeResponse({"rt_cd": "1", "msg1": "호출 후처리(MCI전송) 오류 입니다."}),
                    FakeResponse({"rt_cd": "0", "output1": [{"pdno": "0015B0"}], "output2": [{}]}),
                ]

            def post(self, *args, **kwargs):
                return FakeResponse({"access_token": "token"})

            def get(self, *args, **kwargs):
                return self.responses.pop(0)

        environment = {
            "KIS_APP_KEY": "key",
            "KIS_APP_SECRET": "secret",
            "KIS_ACCOUNT_NO": "12345678",
            "KIS_PRODUCT_CODE": "01",
        }
        with patch.dict(kis_client.os.environ, environment):
            with patch.object(kis_client.time, "sleep") as sleep:
                holdings, _summary, _token = kis_client.fetch_balance(lambda retries: FakeSession())

        self.assertEqual(holdings, [{"pdno": "0015B0"}])
        sleep.assert_called_once_with(3)

    def test_kis_access_token_cache_reuses_token_within_six_hours(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"access_token": "issued-token"}

        class FakeSession:
            def __init__(self):
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                return FakeResponse()

        session = FakeSession()
        with tempfile.TemporaryDirectory() as directory:
            cache_file = f"{directory}/kis-token.json"
            with patch.dict(briefing.os.environ, {"KIS_ACCESS_TOKEN_CACHE_FILE": cache_file}):
                with patch.object(briefing, "KIS_ACCESS_TOKEN_CACHE_FILE", cache_file):
                    with patch.object(briefing, "get_http_session", return_value=session):
                        self.assertEqual(briefing.get_kis_access_token("key", "secret", "https://example.test"), "issued-token")
                        self.assertEqual(briefing.get_kis_access_token("key", "secret", "https://example.test"), "issued-token")

        self.assertEqual(session.calls, 1)

    def test_assets_from_kis_balance_uses_actual_account_values(self):
        configured_assets = [{
            "ticker": "NASDAQ",
            "symbol": "0015B0.KS",
            "display": "나스닥성장",
            "target_weight_pct": 70,
        }]
        holdings = [{
            "pdno": "0015B0",
            "prdt_name": "KoAct 미국나스닥성장기업액티브",
            "hldg_qty": "10",
            "prpr": "22000",
            "prdy_vrss": "500",
            "pchs_avg_pric": "25000",
            "evlu_pfls_amt": "-30000",
        }]

        assets = briefing.assets_from_kis_balance(configured_assets, holdings)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["shares"], 10)
        self.assertEqual(assets[0]["price"], 22000)
        self.assertEqual(assets[0]["prev_close"], 21500)
        self.assertEqual(assets[0]["average_price"], 25000)
        self.assertEqual(assets[0]["evaluation_profit_loss_amount"], -30000)

    def test_assets_from_kis_balance_prefers_current_quote_change(self):
        holdings = [{
            "pdno": "0015B0",
            "hldg_qty": "10",
            "prpr": "22000",
            "prdy_vrss": "0",
        }]

        assets = briefing.assets_from_kis_balance([], holdings, {
            "0015B0": {
                "price": 22100,
                "prev_close": 22000,
                "chg_amount": 100,
                "chg_pct": 100 / 22000 * 100,
            }
        })

        self.assertEqual(assets[0]["price"], 22100)
        self.assertEqual(assets[0]["chg_amount"], 100)
        self.assertAlmostEqual(assets[0]["chg_pct"], 100 / 22000 * 100)

    def test_fetch_kis_domestic_quotes_parses_signed_change(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "rt_cd": "0",
                    "output": {
                        "stck_prpr": "22000",
                        "prdy_vrss": "500",
                        "prdy_vrss_sign": "4",
                        "prdy_ctrt": "-2.22",
                    },
                }

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        environment = {"KIS_APP_KEY": "key", "KIS_APP_SECRET": "secret"}
        with patch.dict(briefing.os.environ, environment):
            with patch.object(briefing, "get_http_session", return_value=FakeSession()):
                quotes, errors = briefing.fetch_kis_domestic_quotes(["0015B0"], "token")

        self.assertEqual(errors, [])
        self.assertEqual(quotes["0015B0"]["chg_amount"], -500)
        self.assertEqual(quotes["0015B0"]["chg_pct"], -2.22)

    def test_assets_from_kis_balance_adds_unconfigured_holding(self):
        assets = briefing.assets_from_kis_balance([], [{
            "pdno": "123456",
            "prdt_name": "테스트 ETF",
            "hldg_qty": "2",
            "prpr": "10000",
            "prdy_vrss": "0",
        }])

        self.assertEqual(assets[0]["ticker"], "123456")
        self.assertEqual(assets[0]["display"], "테스트 ETF")

    def test_fetch_kis_index_quote_parses_kis_response(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "rt_cd": "0",
                    "output1": {"ovrs_nmix_prpr": "22000", "ovrs_nmix_prdy_clpr": "21800"},
                }

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        with patch.dict(briefing.os.environ, {"KIS_APP_KEY": "key", "KIS_APP_SECRET": "secret"}):
            with patch.object(briefing, "get_http_session", return_value=FakeSession()):
                quote = briefing.fetch_kis_index_quote(
                    {"ticker": "NASDAQ100", "kis_symbol": "NDX", "currency": "POINT"},
                    "token",
                )

        self.assertEqual(quote["price"], 22000)
        self.assertEqual(quote["chg_pct"], 200 / 21800 * 100)
        self.assertEqual(quote["provider"], "KIS")


class TradingPlanTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "mode": "dry-run",
            "daily_sell_limit_per_asset_krw": 1000000,
            "rebalance_band_pct": 2,
            "target_weights": {"A": 25, "B": 25, "C": 25, "D": 25},
        }
        self.prices = {"A": 10000, "B": 10000, "C": 10000, "D": 10000}

    def test_buy_plan_limits_initial_cash_to_daily_turnover_limit(self):
        positions = {code: {"quantity": 0, "price": 0} for code in self.prices}

        plan = trading.plan_orders(
            {**self.config, "daily_turnover_limit_pct": 3}, positions, self.prices, 22000000
        )

        self.assertEqual(plan["daily_turnover_limit"], 660000)
        self.assertEqual(sum(order["value"] for order in plan["buys"]), 660000)

    def test_sell_plan_rebalances_to_the_active_target_weight(self):
        config = {
            **self.config,
            "target_weights": {"A": 15, "B": 20, "C": 30, "D": 35},
            "daily_sell_limit_per_asset_krw": 10000000,
        }
        positions = {
            "A": {"quantity": 500, "price": 10000},
            "B": {"quantity": 500, "price": 10000},
            "C": {"quantity": 500, "price": 10000},
            "D": {"quantity": 500, "price": 10000},
        }

        plan = trading.plan_orders(config, positions, self.prices, 0)

        self.assertEqual(plan["sells"], [
            {"code": "A", "quantity": 200, "price": 10000, "value": 2000000},
            {"code": "B", "quantity": 100, "price": 10000, "value": 1000000},
        ])

    def test_sell_plan_does_not_trigger_within_target_band(self):
        positions = {
            "A": {"quantity": 540, "price": 10000},
            "B": {"quantity": 487, "price": 10000},
            "C": {"quantity": 487, "price": 10000},
            "D": {"quantity": 486, "price": 10000},
        }

        plan = trading.plan_orders(self.config, positions, self.prices, 0)

        self.assertEqual(plan["sells"], [])

    def test_liquidation_code_is_sold_within_the_daily_turnover_limit(self):
        config = {
            **self.config,
            "daily_turnover_limit_pct": 100,
            "liquidation_codes": ["LEGACY"],
        }
        positions = {
            **{code: {"quantity": 1, "price": 10000} for code in self.prices},
            "LEGACY": {"quantity": 10, "price": 10000},
        }
        prices = {**self.prices, "LEGACY": 10000}

        plan = trading.plan_orders(config, positions, prices, 0)

        self.assertEqual(plan["sells"], [
            {"code": "LEGACY", "quantity": 10, "price": 10000, "value": 100000},
        ])

    def test_buy_and_sell_limits_are_independent(self):
        config = {**self.config, "daily_buy_limit_pct": 3, "daily_sell_limit_pct": 3}
        positions = {
            "A": {"quantity": 682, "price": 10000},
            "B": {"quantity": 506, "price": 10000},
            "C": {"quantity": 506, "price": 10000},
            "D": {"quantity": 506, "price": 10000},
        }

        plan = trading.plan_orders(config, positions, self.prices, 660000)

        self.assertEqual(plan["daily_buy_limit"], 679800)
        self.assertEqual(plan["daily_sell_limit"], 679800)
        self.assertGreater(sum(order["value"] for order in plan["sells"]), 0)
        self.assertGreater(sum(order["value"] for order in plan["buys"]), 0)

    def test_buy_plan_does_not_exceed_available_cash(self):
        positions = {code: {"quantity": 0, "price": 0} for code in self.prices}

        plan = trading.plan_orders(self.config, positions, self.prices, 35000)

        self.assertEqual(sum(order["value"] for order in plan["buys"]), 30000)
        self.assertEqual(plan["unallocated_cash"], 5000)

    def test_buy_plan_respects_kis_orderable_cash(self):
        positions = {code: {"quantity": 0, "price": 0} for code in self.prices}

        plan = trading.plan_orders(self.config, positions, self.prices, 100000, orderable_cash=25000)

        self.assertEqual(sum(order["value"] for order in plan["buys"]), 20000)
        self.assertEqual(plan["orderable_cash"], 25000)

    def test_buy_plan_does_not_spend_past_an_asset_deficit(self):
        config = {
            **self.config,
            "target_weights": {"A": 50, "B": 50},
            "daily_buy_limit_pct": 100,
            "daily_sell_limit_pct": 100,
        }
        positions = {"A": {"quantity": 0}, "B": {"quantity": 0}}

        plan = trading.plan_orders(config, positions, {"A": 30, "B": 80}, 100)

        self.assertEqual(plan["buys"], [{"code": "A", "quantity": 2, "price": 30, "value": 60}])

    def test_cash_from_balance_prefers_available_cash_after_orders(self):
        summary = {
            "dnca_tot_amt": "10000000",
            "prvs_rcdl_excc_amt": "9500000",
        }

        self.assertEqual(trading.cash_from_balance(summary), 9500000)

    def test_load_balance_snapshot_reuses_briefing_response(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as file:
            json.dump(
                {"holdings": [{"pdno": "A"}], "summary": {"dnca_tot_amt": "100"}, "access_token": "token"},
                file,
            )
            file.flush()
            with patch.dict(trading.os.environ, {"KIS_BALANCE_SNAPSHOT_FILE": file.name}):
                holdings, summary, access_token = trading.load_balance_snapshot()

        self.assertEqual(holdings, [{"pdno": "A"}])
        self.assertEqual(summary["dnca_tot_amt"], "100")
        self.assertEqual(access_token, "token")

    def test_submit_cash_buy_uses_complete_kis_order_payload(self):
        class FakeResponse:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {"rt_cd": "0", "output": {"ODNO": "123"}}

        class FakeSession:
            def post(self, _url, **kwargs):
                self.kwargs = kwargs
                return FakeResponse()

        session = FakeSession()
        context = {
            "account_no": "12345678",
            "product_code": "01",
            "base_url": "https://example.test",
            "headers": {"authorization": "Bearer token"},
            "session": session,
        }

        result = trading.submit_cash_buy("0015B0", 1, 21300, context)

        self.assertEqual(result["ODNO"], "123")
        self.assertEqual(session.kwargs["json"]["SLL_TYPE"], "")
        self.assertEqual(session.kwargs["json"]["CNDT_PRIC"], "")
        self.assertEqual(session.kwargs["headers"]["content-type"], "application/json; charset=utf-8")

    def test_submit_cash_sell_uses_sell_transaction_id(self):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"rt_cd": "0", "output": {"ODNO": "123"}}

        class FakeSession:
            def post(self, _url, **kwargs):
                self.kwargs = kwargs
                return FakeResponse()

        session = FakeSession()
        context = {
            "account_no": "12345678",
            "product_code": "01",
            "base_url": "https://example.test",
            "headers": {"authorization": "Bearer token"},
            "session": session,
        }

        trading.submit_cash_sell("0015B0", 1, 21300, context)

        self.assertEqual(session.kwargs["headers"]["tr_id"], "TTTC0011U")
        self.assertEqual(session.kwargs["json"]["SLL_TYPE"], "01")

    def test_paper_order_uses_virtual_transaction_id(self):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"rt_cd": "0", "output": {"ODNO": "123"}}

        class FakeSession:
            def post(self, _url, **kwargs):
                self.kwargs = kwargs
                return FakeResponse()

        session = FakeSession()
        context = {
            "account_no": "12345678",
            "product_code": "01",
            "base_url": "https://example.test",
            "headers": {"authorization": "Bearer token"},
            "session": session,
            "is_paper": True,
        }

        trading.submit_cash_buy("0015B0", 1, 21300, context)

        self.assertEqual(session.kwargs["headers"]["tr_id"], "VTTC0012U")

    def test_cancel_unfilled_order_uses_cancelable_quantity(self):
        class FakeResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"rt_cd": "0", "output": {}}

        class FakeSession:
            def post(self, _url, **kwargs):
                self.kwargs = kwargs
                return FakeResponse()

        session = FakeSession()
        context = {
            "account_no": "12345678",
            "product_code": "01",
            "base_url": "https://example.test",
            "headers": {"authorization": "Bearer token"},
            "session": session,
        }
        result = trading.cancel_unfilled_order(
            {"order_no": "123", "price": 21300},
            [{"odno": "123", "psbl_qty": "2", "ord_gno_brno": "00001", "ord_dvsn": "00", "ord_unpr": "21300"}],
            context,
        )

        self.assertEqual(result, {"order_no": "123", "quantity": 2})
        self.assertEqual(session.kwargs["headers"]["tr_id"], "TTTC0013U")
        self.assertEqual(session.kwargs["json"]["QTY_ALL_ORD_YN"], "Y")

    def test_paper_unfilled_orders_uses_remaining_quantity_only(self):
        orders = trading.paper_unfilled_orders(
            [{"odno": "1", "rmn_qty": "2"}, {"odno": "2", "rmn_qty": "0"}],
            [{"order_no": "1", "quantity": 3}, {"order_no": "2", "quantity": 1}],
        )

        self.assertEqual(orders, [{"order_no": "1", "quantity": 2}])

    def test_execution_report_shows_compact_fill_and_cancellation_status(self):
        report = trading.format_execution_report(
            [
                {"odno": "1", "tot_ccld_qty": "2", "rmn_qty": "0", "avg_prvs": "9900"},
                {"odno": "2", "tot_ccld_qty": "0", "rmn_qty": "3", "avg_prvs": "0"},
            ],
            [
                {"order_no": "1", "side": "buy", "code": "A", "quantity": 2, "price": 10000},
                {"order_no": "2", "side": "sell", "code": "B", "quantity": 3, "price": 11000},
            ],
            [{"order_no": "2", "quantity": 3}],
        )

        self.assertEqual(report[0], "🤖 자동매매 결과")
        self.assertEqual(report[1], "매수 A 2주 · 2만 체결")
        self.assertIn("미체결 취소", report[2])
        self.assertNotIn("지정가", "\n".join(report))

    def test_execution_report_uses_asset_labels(self):
        report = trading.format_execution_report(
            [],
            [{"order_no": "1", "side": "buy", "code": "0015B0", "quantity": 1, "price": 20000}],
            [],
            {"0015B0": "KoAct나스닥성장"},
        )

        self.assertIn("매수 KoAct나스닥성장", report[1])
        self.assertNotIn("0015B0", report[1])
        self.assertNotIn("지정가", report[1])

    def test_reprice_orders_never_exceeds_live_cash_limit(self):
        orders = [
            {"code": "A", "quantity": 3, "price": 10000, "value": 30000},
            {"code": "B", "quantity": 3, "price": 10000, "value": 30000},
        ]

        repriced = trading.reprice_orders(orders, {"A": 12000, "B": 11000}, 35000)

        self.assertEqual(repriced, [{"code": "A", "quantity": 2, "price": 12000, "value": 24000}, {"code": "B", "quantity": 1, "price": 11000, "value": 11000}])

    def test_completed_orders_do_not_block_a_new_run(self):
        orders = [{"pdno": "A", "rmn_qty": "0"}]

        self.assertFalse(trading.has_open_target_order(orders, {"A"}))

    def test_open_orders_block_a_new_run(self):
        orders = [{"pdno": "A", "rmn_qty": "2"}]

        self.assertTrue(trading.has_open_target_order(orders, {"A"}))

    def test_retry_safety_blocks_a_retry_when_first_order_status_is_missing(self):
        reason = trading.retry_safety_reason(
            [],
            [{"order_no": "1"}],
            [],
            [],
        )

        self.assertIn("상태 조회가 지연", reason)

    def test_retry_safety_blocks_a_retry_with_open_first_order(self):
        reason = trading.retry_safety_reason(
            [{"odno": "1", "rmn_qty": "2"}],
            [{"order_no": "1"}],
            [],
            [{"odno": "1", "psbl_qty": "2"}],
        )

        self.assertIn("미체결 잔량", reason)

    def test_submit_live_orders_stops_after_a_submission_error(self):
        orders = [
            {"code": "A", "quantity": 1, "price": 10000, "value": 10000},
            {"code": "B", "quantity": 1, "price": 10000, "value": 10000},
        ]
        with patch.object(
            trading,
            "submit_cash_order",
            side_effect=[{"ODNO": "1"}, RuntimeError("KIS timeout")],
        ) as submit:
            submitted, errors = trading.submit_live_orders([], orders, {})

        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["order_no"], "1")
        self.assertEqual(errors, [{"side": "buy", "code": "B", "message": "KIS timeout"}])
        self.assertEqual(submit.call_count, 2)

    def test_live_rebalance_does_not_retry_when_first_order_status_is_missing(self):
        config = {
            "target_weights": {"A": 100},
            "liquidation_codes": [],
            "daily_buy_limit_pct": 100,
            "daily_sell_limit_pct": 100,
            "daily_sell_limit_per_asset_krw": 1000000,
            "rebalance_band_pct": 0,
            "order_policy": {"first_order_check_minutes": 0},
        }
        trend = {"state": "neutral", "weights": {"A": 100}}
        first_order = {
            "side": "buy",
            "code": "A",
            "quantity": 1,
            "price": 10000,
            "value": 10000,
            "order_no": "1",
        }

        with patch.object(trading, "resolve_trend_strategy", return_value=trend):
            with patch.object(trading, "fetch_today_orders", side_effect=[[], []]):
                with patch.object(trading, "fetch_kis_prices", return_value={"A": 10000}):
                    with patch.object(trading, "fetch_kis_orderable_cash", return_value=10000):
                        with patch.object(trading, "load_asset_labels", return_value={"A": "테스트 ETF"}):
                            with patch.object(trading, "live_orders_for_plan", return_value=([], [first_order])):
                                with patch.object(
                                    trading,
                                    "submit_live_orders",
                                    return_value=([first_order], []),
                                ) as submit:
                                    with patch.object(trading, "fetch_cancelable_orders", side_effect=[[], []]):
                                        with patch.object(trading.time, "sleep"):
                                            result = trading.execute_live_rebalance(
                                                config,
                                                [],
                                                {"prvs_rcdl_excc_amt": "10000"},
                                                {"is_paper": False},
                                            )

        self.assertEqual(submit.call_count, 1)
        self.assertIn("2차 주문 보류", "\n".join(result["execution_report"]))
        self.assertIn("상태 조회가 지연", "\n".join(result["execution_report"]))

    def test_dry_run_stops_when_trend_calculation_fails(self):
        config = {
            "mode": "dry-run",
            "target_weights": {"A": 100},
            "liquidation_codes": [],
        }
        failed_trend = {
            "state": "neutral",
            "weights": {"A": 100},
            "error": "KIS 일봉조회 실패",
        }
        with patch.object(trading, "load_config", return_value=config):
            with patch.object(trading, "load_balance_snapshot", return_value=([], {}, "token")):
                with patch.object(trading, "get_kis_context", return_value={}):
                    with patch.object(trading, "resolve_trend_strategy", return_value=failed_trend):
                        with patch.object(trading, "fetch_kis_prices") as fetch_prices:
                            with patch("sys.argv", ["trading_execution.py"]):
                                with patch("builtins.print") as print_mock:
                                    trading.main()

        fetch_prices.assert_not_called()
        self.assertIn("추세 계산 실패", print_mock.call_args.args[0])

    def test_main_keeps_the_workflow_alive_after_live_order_error(self):
        config = {
            "mode": "live",
            "live_orders_enabled": True,
            "target_weights": {"A": 100},
            "liquidation_codes": [],
        }
        with patch.object(trading, "load_config", return_value=config):
            with patch.object(trading, "load_balance_snapshot", return_value=([], {}, "token")):
                with patch.object(trading, "get_kis_context", return_value={}):
                    with patch.object(
                        trading,
                        "execute_live_rebalance",
                        side_effect=RuntimeError("KIS timeout"),
                    ):
                        with patch("sys.argv", ["trading_execution.py", "--execute-live"]):
                            with patch("builtins.print") as print_mock:
                                trading.main()

        self.assertIn("주문 처리 중 오류", print_mock.call_args.args[0])

    def test_live_rebalance_stops_when_trend_calculation_fails(self):
        failed_trend = {
            "state": "neutral",
            "weights": {"A": 100},
            "error": "KIS 일봉조회 실패",
        }
        with patch.object(trading, "resolve_trend_strategy", return_value=failed_trend):
            with patch.object(trading, "fetch_today_orders") as fetch_orders:
                result = trading.execute_live_rebalance(
                    {"target_weights": {"A": 100}}, [], {}, {}
                )

        self.assertEqual(result["status"], "skipped")
        self.assertIn("추세 계산 실패", result["reason"])
        fetch_orders.assert_not_called()

    def test_filled_trade_values_count_only_managed_etfs(self):
        orders = [
            {"pdno": "A", "sll_buy_dvsn_cd": "02", "tot_ccld_amt": "10000"},
            {"pdno": "B", "sll_buy_dvsn_cd": "01", "tot_ccld_qty": "2", "avg_prvs": "5000"},
            {"pdno": "OTHER", "sll_buy_dvsn_cd": "01", "tot_ccld_amt": "90000"},
        ]

        self.assertEqual(trading.filled_trade_values_for_codes(orders, {"A", "B"}), {"buy": 10000, "sell": 10000})

    def test_trend_state_requires_confirmed_moving_average_direction(self):
        rising = [(f"20260{index:03}", float(100 + index)) for index in range(65)]
        falling = [(f"20260{index:03}", float(200 - index)) for index in range(65)]

        rising_state = trading.calculate_trend_state(rising, 20, 60, 3)
        self.assertEqual(rising_state["state"], "risk_on")
        self.assertGreater(rising_state["short_average"], rising_state["long_average"])
        self.assertEqual(
            trading.calculate_trend_state(falling, 20, 60, 3)["state"], "risk_off"
        )

    def test_hma_trend_state_uses_shorter_confirmation(self):
        rising = [(f"20260{index:03}", float(100 + index)) for index in range(220)]
        falling = [(f"20260{index:03}", float(400 - index)) for index in range(220)]

        rising_state = trading.calculate_trend_state(rising, 20, 40, 2, "hma", 200)
        self.assertEqual(rising_state["state"], "risk_on")
        self.assertEqual(rising_state["average_type"], "hma")
        self.assertIsNotNone(rising_state["filter_average"])
        self.assertEqual(
            trading.calculate_trend_state(falling, 20, 40, 2, "hma", 200)["state"], "risk_off"
        )

    def test_hma_200_filter_blocks_risk_on_during_a_bear_market_rebound(self):
        closes = [(f"2026{index:04}", 200.0) for index in range(210)]
        closes.extend((f"2027{index:04}", 200.0 - 5 * (index + 1)) for index in range(10))
        bottom = closes[-1][1]
        closes.extend((f"2028{index:04}", bottom + 3 * (index + 1)) for index in range(10))

        self.assertEqual(trading.calculate_trend_state(closes, 20, 40, 2, "hma")["state"], "risk_on")
        filtered = trading.calculate_trend_state(closes, 20, 40, 2, "hma", 200)
        self.assertEqual(filtered["state"], "neutral")
        self.assertLess(filtered["latest_close"], filtered["filter_average"])

    def test_weighted_close_series_uses_equal_weights_by_default(self):
        closes = strategy.weighted_close_series({
            "A": [("20260720", 100), ("20260721", 110)],
            "B": [("20260720", 200), ("20260721", 220)],
        })

        self.assertEqual([date for date, _close in closes], ["20260720", "20260721"])
        self.assertAlmostEqual(closes[0][1], 100)
        self.assertAlmostEqual(closes[1][1], 110)

    def test_weighted_close_series_uses_supplied_target_weights(self):
        closes = strategy.weighted_close_series({
            "A": [("20260720", 100), ("20260721", 120)],
            "B": [("20260720", 100), ("20260721", 100)],
        }, {"A": 75, "B": 25})

        self.assertEqual(
            [(day, round(close)) for day, close in closes],
            [("20260720", 100), ("20260721", 115)],
        )

    def test_composite_trend_requires_all_three_daily_scores_to_confirm(self):
        components = [
            {
                "label": "계좌 위험자산",
                "weight_pct": 50,
                "daily_states": [
                    {"date": "20260720", "state": "risk_on"},
                    {"date": "20260721", "state": "risk_on"},
                    {"date": "20260722", "state": "risk_on"},
                ],
            },
            {
                "label": "나스닥100",
                "weight_pct": 25,
                "daily_states": [
                    {"date": "20260720", "state": "risk_on"},
                    {"date": "20260721", "state": "risk_on"},
                    {"date": "20260722", "state": "risk_on"},
                ],
            },
            {
                "label": "S&P500",
                "weight_pct": 25,
                "daily_states": [
                    {"date": "20260720", "state": "risk_off"},
                    {"date": "20260721", "state": "risk_on"},
                    {"date": "20260722", "state": "risk_on"},
                ],
            },
        ]

        result = strategy.calculate_composite_trend_state(components, 3)

        self.assertEqual(result["state"], "risk_on")
        self.assertEqual(result["signals"], ["risk_on", "risk_on", "risk_on"])

    def test_fetch_kis_index_daily_closes_parses_completed_candles(self):
        today = trading.datetime.now(kis_client.KST).strftime("%Y%m%d")

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "rt_cd": "0",
                    "output2": [
                        {"stck_bsop_date": "20260724", "ovrs_nmix_prpr": "28000"},
                        {"stck_bsop_date": today, "ovrs_nmix_prpr": "28100"},
                    ],
                }

        class FakeSession:
            def __init__(self):
                self.calls = []

            def get(self, *_args, **_kwargs):
                self.calls.append(_kwargs["params"])
                return FakeResponse()

        session = FakeSession()
        context = {
            "base_url": "https://example.test",
            "headers": {},
            "session": session,
            "next_kis_request_at": 0,
        }
        with patch.object(trading, "wait_for_kis_request_slot"):
            closes = trading.fetch_kis_index_daily_closes("NDX", context)

        self.assertEqual(closes, [("20260724", 28000)])
        self.assertEqual(len(session.calls), 4)
        self.assertLessEqual(
            max(
                (
                    trading.datetime.strptime(call["FID_INPUT_DATE_2"], "%Y%m%d")
                    - trading.datetime.strptime(call["FID_INPUT_DATE_1"], "%Y%m%d")
                ).days
                for call in session.calls
            ),
            119,
        )

    def test_fetch_kis_daily_closes_requests_adjusted_prices(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"rt_cd": "0", "output2": []}

        class FakeSession:
            def __init__(self):
                self.calls = []

            def get(self, *_args, **kwargs):
                self.calls.append(kwargs["params"])
                return FakeResponse()

        session = FakeSession()
        context = {
            "base_url": "https://example.test",
            "headers": {},
            "session": session,
            "next_kis_request_at": 0,
        }
        with patch.object(trading, "wait_for_kis_request_slot"):
            trading.fetch_kis_daily_closes("0015B0", context, lookback_days=10)

        self.assertTrue(session.calls)
        self.assertTrue(all(call["FID_ORG_ADJ_PRC"] == "0" for call in session.calls))

    def test_trend_error_is_exposed_in_telegram(self):
        telegram, markdown = briefing.build_content(
            [],
            [],
            [],
            trend_state={"state": "neutral", "weights": {}, "error": "일봉 100/214"},
        )

        self.assertIn("추세 계산 실패 · 중립 적용", telegram)
        self.assertIn("일봉 100/214", telegram)
        self.assertIn("추세 계산 실패로 중립 적용", markdown)

    def test_composite_strategy_uses_portfolio_and_both_market_indexes(self):
        rising = [(f"20260{index:03}", float(100 + index)) for index in range(65)]
        trend = {
            "short_window_days": 20,
            "long_window_days": 60,
            "confirmation_days": 3,
            "composite_threshold": 0.5,
            "signals": [
                {"kind": "portfolio", "label": "계좌 위험자산", "weight_pct": 50, "codes": ["A", "B", "C"]},
                {"kind": "index", "label": "나스닥100", "weight_pct": 25, "symbol": "NDX"},
                {"kind": "index", "label": "S&P500", "weight_pct": 25, "symbol": "SPX"},
            ],
        }
        with patch.object(trading, "fetch_kis_daily_closes", return_value=rising) as domestic:
            with patch.object(trading, "fetch_kis_index_daily_closes", return_value=rising) as index:
                result = trading.resolve_composite_trend_strategy(trend, {})

        self.assertEqual(result["state"], "risk_on")
        self.assertEqual(domestic.call_count, 3)
        self.assertEqual([call.args[0] for call in index.call_args_list], ["NDX", "SPX"])

    def test_composite_strategy_uses_recent_common_dates_after_a_market_holiday(self):
        domestic_dates = [f"2026{day:04d}" for day in range(1, 30)]
        index_dates = [f"2026{day:04d}" for day in range(1, 31)]
        domestic = [(day, float(100 + index)) for index, day in enumerate(domestic_dates)]
        indexes = [(day, float(100 + index)) for index, day in enumerate(index_dates)]
        trend = {
            "short_window_days": 2,
            "long_window_days": 3,
            "confirmation_days": 2,
            "common_history_days": 20,
            "composite_threshold": 0.5,
            "signals": [
                {"kind": "portfolio", "label": "계좌 위험자산", "weight_pct": 50, "codes": ["A", "B"]},
                {"kind": "index", "label": "나스닥100", "weight_pct": 25, "symbol": "NDX"},
                {"kind": "index", "label": "S&P500", "weight_pct": 25, "symbol": "SPX"},
            ],
        }
        with patch.object(trading, "fetch_kis_daily_closes", return_value=domestic):
            with patch.object(trading, "fetch_kis_index_daily_closes", return_value=indexes):
                result = trading.resolve_composite_trend_strategy(
                    trend,
                    {},
                    {"A": 50, "B": 50},
                )

        self.assertEqual(result["state"], "risk_on")
        self.assertEqual(result["latest_date"], domestic_dates[-1])
        self.assertEqual(result["signals"], ["risk_on", "risk_on"])

    def test_paper_account_uses_per_run_test_limit(self):
        budgets = trading.daily_trade_budgets(
            {**self.config, "paper_test_order_limit_krw": 100000},
            10000000,
            [{"pdno": "A", "tot_ccld_amt": "300000"}],
            {"A"},
            {"is_paper": True},
        )

        self.assertIsNone(budgets["buy_cap"])
        self.assertEqual(budgets["buy_used"], 0)
        self.assertEqual(budgets["buy_remaining"], 100000)
        self.assertEqual(budgets["sell_remaining"], 100000)

    def test_daily_buy_and_sell_budgets_are_independent(self):
        budgets = trading.daily_trade_budgets(
            {**self.config, "daily_buy_limit_pct": 2, "daily_sell_limit_pct": 2},
            10000000,
            [{"pdno": "A", "sll_buy_dvsn_cd": "02", "tot_ccld_amt": "100000"}],
            {"A"},
            {"is_paper": False},
        )

        self.assertEqual(budgets["buy_remaining"], 100000)
        self.assertEqual(budgets["sell_remaining"], 200000)

    def test_kis_request_spacing_waits_until_the_next_slot(self):
        context = {"next_kis_request_at": 11.1}
        with patch.object(trading.time, "monotonic", side_effect=[10.0, 11.1]):
            with patch.object(trading.time, "sleep") as sleep:
                trading.wait_for_kis_request_slot(context)

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 1.1)
        self.assertAlmostEqual(context["next_kis_request_at"], 13.1)

    def test_submit_order_retries_once_after_kis_rate_limit(self):
        class RateLimitedResponse:
            status_code = 500
            text = '{"msg_cd":"EGW00201"}'

        class SuccessResponse:
            status_code = 200
            text = ""

            def json(self):
                return {"rt_cd": "0", "output": {"ODNO": "123"}}

        class FakeSession:
            def __init__(self):
                self.responses = [RateLimitedResponse(), SuccessResponse()]

            def post(self, *_args, **_kwargs):
                return self.responses.pop(0)

        context = {
            "account_no": "12345678",
            "product_code": "01",
            "base_url": "https://example.test",
            "headers": {"authorization": "Bearer token"},
            "session": FakeSession(),
            "next_kis_request_at": 0,
        }
        with patch.object(trading.time, "sleep"):
            result = trading.submit_cash_buy("0015B0", 1, 21300, context)

        self.assertEqual(result["ODNO"], "123")


class ContentTests(unittest.TestCase):
    def test_compute_weights_uses_total_account_assets_including_cash(self):
        quotes = [
            {"shares": 1, "price": 100.0},
            {"shares": 1, "price": 100.0},
        ]

        briefing.compute_weights(quotes, {"tot_evlu_amt": "1000"})

        self.assertEqual([item["weight_pct"] for item in quotes], [10.0, 10.0])

    def test_apply_trend_weights_overrides_static_targets(self):
        assets = [
            {"symbol": "0015B0.KS", "target_weight_pct": 25},
            {"symbol": "0048J0.KS", "target_weight_pct": 25},
        ]
        result = briefing.apply_trend_weights(assets, {
            "state": "risk_off",
            "weights": {"0015B0": 15, "0048J0": 35},
        })

        self.assertEqual([asset["target_weight_pct"] for asset in result], [15, 35])

    def test_build_content_shows_rebalancing_buy_priorities(self):
        quotes = [
            {
                "ticker": "GROWTH",
                "display": "성장",
                "name": "성장",
                "currency": "KRW",
                "price": 1000.0,
                "prev_close": 990.0,
                "chg_amount": 10.0,
                "chg_pct": 1.01,
                "shares": 1,
                "weight_pct": 66.6,
                "target_weight_pct": 70,
                "provider": "KIS",
            },
            {
                "ticker": "INCOME",
                "display": "인컴",
                "name": "인컴",
                "currency": "KRW",
                "price": 1000.0,
                "prev_close": 990.0,
                "chg_amount": 10.0,
                "chg_pct": 1.01,
                "shares": 1,
                "weight_pct": 33.4,
                "target_weight_pct": 30,
                "provider": "KIS",
            },
        ]

        telegram, markdown = briefing.build_content([], quotes, [])

        self.assertNotIn("📊 조정", telegram)
        self.assertIn("## 📊 리밸런싱", markdown)

    def test_build_content_shows_zero_share_tracking_asset_without_position_values(self):
        quotes = [
            {
                "ticker": "SPMO",
                "display": "SPMO",
                "name": "SPMO",
                "currency": "USD",
                "price": 100.0,
                "prev_close": 99.0,
                "chg_amount": 1.0,
                "chg_pct": 1.01,
                "shares": 0,
                "provider": "KIS",
            }
        ]

        telegram, markdown = briefing.build_content([], quotes, [])

        self.assertNotIn("영업일 적립", telegram)
        self.assertIn("🔴 SPMO", telegram)
        self.assertNotIn("$+0.00", telegram)
        self.assertIn("| SPMO |", markdown)
        self.assertIn("| - | - |", markdown)

    def test_build_content_has_no_account_section(self):
        quotes = [
            {
                "ticker": "QLD",
                "display": "QLD",
                "name": "QLD",
                "currency": "USD",
                "price": 90.0,
                "prev_close": 91.0,
                "chg_amount": -1.0,
                "chg_pct": -1.1,
                "provider": "KIS",
            }
        ]

        telegram, markdown = briefing.build_content([], quotes, [])

        self.assertNotIn("계좌", telegram)
        self.assertNotIn("계좌", markdown)

    def test_build_content_shows_kis_account_summary_and_evaluation_profit_loss(self):
        quotes = [{
            "ticker": "ETF",
            "display": "테스트 ETF",
            "name": "테스트 ETF",
            "currency": "KRW",
            "price": 22000,
            "prev_close": 21500,
            "chg_amount": 500,
            "chg_pct": 2.33,
            "shares": 10,
            "average_price": 25000,
            "evaluation_profit_loss_amount": -30000,
            "provider": "KIS",
        }]

        telegram, markdown = briefing.build_content(
            [], quotes, [],
            account_summary={"tot_evlu_amt": "220000", "prvs_rcdl_excc_amt": "50000"},
        )

        self.assertIn("자산 22만 · 예수금 5만", telegram)
        self.assertIn("평가손익 -30,000원 (-12.00%)", telegram)
        self.assertIn("₩22,000 · -30,000원", telegram)
        self.assertNotIn("평단 ₩25,000", telegram)
        self.assertIn("- 평가손익: -30,000원 (-12.00%)", markdown)
        self.assertIn("## 💳 계좌 요약", markdown)

    def test_build_content_includes_market_notice(self):
        quotes = [{
            "ticker": "ETF",
            "display": "테스트 ETF",
            "name": "테스트 ETF",
            "currency": "KRW",
            "price": 22000,
            "prev_close": 21500,
            "chg_amount": 500,
            "chg_pct": 2.33,
            "provider": "KIS",
        }]

        telegram, markdown = briefing.build_content(
            [], quotes, [], market_notice="📌 오늘은 KRX 휴장일입니다."
        )

        self.assertIn("📌 오늘은 KRX 휴장일입니다.", telegram)
        self.assertIn("## 📌 시장 상태", markdown)

    def test_telegram_keeps_usd_effect_as_usd_without_fx_rate(self):
        quotes = [
            {
                "ticker": "SOXL",
                "display": "SOXL",
                "name": "SOXL",
                "currency": "USD",
                "price": 10.0,
                "prev_close": 9.0,
                "chg_amount": 1.0,
                "chg_pct": 11.11,
                "shares": 2,
                "provider": "KIS",
            }
        ]

        telegram, _markdown = briefing.build_content([], quotes, [])

        self.assertIn("+$2.00", telegram)
        self.assertNotIn("+2원", telegram)


if __name__ == "__main__":
    unittest.main()
