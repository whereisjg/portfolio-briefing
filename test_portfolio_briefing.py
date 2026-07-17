import unittest
from unittest.mock import patch
import tempfile
import json

import portfolio_briefing as briefing
import kis_client
import market_calendar
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


class ConfigurationTests(unittest.TestCase):
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
            "daily_turnover_limit": 100000,
            "daily_turnover_cap": None,
            "sells": [],
            "buys": [{"code": "0015B0", "quantity": 1, "price": 21000, "value": 21000}],
            "unallocated_cash": 79000,
            "trend": {"state": "neutral", "signal_code": "0015B0", "latest_date": "20260716", "latest_close": 21335, "short_average": 23750, "long_average": 22881},
        }

        report = strategy.format_plan(plan, asset_labels={"0015B0": "KoAct나스닥성장"})

        self.assertIn("🤖 자동매매 dry-run · 한도 10만", report)
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

    def test_turnover_limit_caps_combined_daily_buys_and_sells(self):
        config = {**self.config, "daily_turnover_limit_pct": 3}
        positions = {
            "A": {"quantity": 682, "price": 10000},
            "B": {"quantity": 506, "price": 10000},
            "C": {"quantity": 506, "price": 10000},
            "D": {"quantity": 506, "price": 10000},
        }

        plan = trading.plan_orders(config, positions, self.prices, 0)

        self.assertEqual(plan["daily_turnover_limit"], 660000)
        self.assertEqual(sum(order["value"] for order in plan["sells"]), 660000)
        self.assertEqual(plan["buys"], [])

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

    def test_execution_report_includes_fill_slippage_and_cancellation(self):
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

        self.assertEqual(report[0], "📋 체결 품질")
        self.assertIn("체결 2/2주 @ 9,900원 · 주문 대비 -100원 (-1.00%) · 전량 체결", report[1])
        self.assertIn("미체결 취소", report[2])

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

    def test_filled_turnover_counts_only_managed_etfs(self):
        orders = [
            {"pdno": "A", "tot_ccld_amt": "10000"},
            {"pdno": "B", "tot_ccld_qty": "2", "avg_prvs": "5000"},
            {"pdno": "OTHER", "tot_ccld_amt": "90000"},
        ]

        self.assertEqual(trading.filled_turnover_for_codes(orders, {"A", "B"}), 20000)

    def test_trend_state_requires_confirmed_moving_average_direction(self):
        rising = [(f"20260{index:03}", float(100 + index)) for index in range(65)]
        falling = [(f"20260{index:03}", float(200 - index)) for index in range(65)]

        rising_state = trading.calculate_trend_state(rising, 20, 60, 3)
        self.assertEqual(rising_state["state"], "risk_on")
        self.assertGreater(rising_state["short_average"], rising_state["long_average"])
        self.assertEqual(
            trading.calculate_trend_state(falling, 20, 60, 3)["state"], "risk_off"
        )

    def test_paper_account_uses_per_run_test_limit(self):
        cap, used, remaining = trading.daily_turnover_budget(
            {**self.config, "paper_test_order_limit_krw": 100000},
            10000000,
            [{"pdno": "A", "tot_ccld_amt": "300000"}],
            {"A"},
            {"is_paper": True},
        )

        self.assertIsNone(cap)
        self.assertEqual(used, 0)
        self.assertEqual(remaining, 100000)

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
        self.assertIn("₩22,000 · -30,000원", telegram)
        self.assertNotIn("평단 ₩25,000", telegram)
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
