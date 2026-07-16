import unittest
from unittest.mock import patch
import tempfile
import json

import portfolio_briefing as briefing
import trade_automation as trading


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

    def test_news_optional_suppresses_missing_news_alert(self):
        quotes = [{"ticker": "SCHD", "chg_pct": 0.5, "news_optional": True}]

        self.assertEqual(briefing.build_alert_lines(quotes, [], {"SCHD": []}), ["특이사항 없음"])


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

class KisBalanceTests(unittest.TestCase):
    def test_paper_balance_uses_virtual_tr_id_and_unpr_dvsn(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"rt_cd": "0", "output": [], "output2": [{}]}

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
                briefing.fetch_kis_balance()

        self.assertEqual(session.kwargs["headers"]["tr_id"], "VTTC8434R")
        self.assertEqual(session.kwargs["params"]["UNPR_DVSN"], "01")

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

    def test_fetch_kis_news_returns_titles(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "rt_cd": "0",
                    "output": [
                        {"hts_pbnt_titl_cntt": "ETF 관련 공시", "dorg": "한국경제"},
                        {"hts_pbnt_titl_cntt": "두 번째 뉴스", "dorg": ""},
                    ],
                }

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        with patch.dict(briefing.os.environ, {"KIS_APP_KEY": "key", "KIS_APP_SECRET": "secret"}):
            with patch.object(briefing, "get_http_session", return_value=FakeSession()):
                titles = briefing.fetch_kis_news({"symbol": "486290.KS"}, "token")

        self.assertEqual(titles, ["ETF 관련 공시 - 한국경제", "두 번째 뉴스"])

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
        self.assertTrue(assets[0]["news_optional"])

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
            "daily_buy_limit_krw": 500000,
            "daily_sell_limit_per_asset_krw": 1000000,
            "sell_trigger_weight_pct": 30,
            "sell_target_weight_pct": 27,
            "target_weights": {"A": 25, "B": 25, "C": 25, "D": 25},
        }
        self.prices = {"A": 10000, "B": 10000, "C": 10000, "D": 10000}

    def test_buy_plan_limits_initial_cash_to_daily_buy_limit(self):
        positions = {code: {"quantity": 0, "price": 0} for code in self.prices}

        plan = trading.plan_orders(self.config, positions, self.prices, 22000000)

        self.assertEqual(plan["daily_buy_limit"], 500000)
        self.assertEqual(sum(order["value"] for order in plan["buys"]), 500000)

    def test_sell_plan_triggers_above_thirty_percent_and_targets_twenty_seven(self):
        positions = {
            "A": {"quantity": 682, "price": 10000},
            "B": {"quantity": 506, "price": 10000},
            "C": {"quantity": 506, "price": 10000},
            "D": {"quantity": 506, "price": 10000},
        }

        plan = trading.plan_orders(self.config, positions, self.prices, 0)

        self.assertEqual(plan["sells"], [{"code": "A", "quantity": 88, "price": 10000, "value": 880000}])

    def test_sell_plan_does_not_trigger_at_exactly_thirty_percent(self):
        positions = {
            "A": {"quantity": 600, "price": 10000},
            "B": {"quantity": 467, "price": 10000},
            "C": {"quantity": 467, "price": 10000},
            "D": {"quantity": 466, "price": 10000},
        }

        plan = trading.plan_orders(self.config, positions, self.prices, 0)

        self.assertEqual(plan["sells"], [])

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

    def test_confirmed_test_buys_require_cash_before_submitting_orders(self):
        context = {"account_no": "12345678", "product_code": "01"}
        with patch.dict(trading.os.environ, {"CONFIRM_LIVE_TEST_BUY": "CONFIRM"}):
            with patch.object(trading, "fetch_kis_best_ask", side_effect=[21000, 11000]):
                with patch.object(trading, "fetch_kis_orderable_cash", return_value=40000):
                    with patch.object(
                        trading,
                        "submit_cash_buy",
                        side_effect=[{"ODNO": "1"}, {"ODNO": "2"}],
                    ) as submit:
                        results = trading.execute_confirmed_test_buys(["0015B0", "486290"], context)

        self.assertEqual([result["order_no"] for result in results], ["1", "2"])
        self.assertEqual(submit.call_count, 2)

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

    def test_reprice_orders_never_exceeds_live_cash_limit(self):
        orders = [
            {"code": "A", "quantity": 3, "price": 10000, "value": 30000},
            {"code": "B", "quantity": 3, "price": 10000, "value": 30000},
        ]

        repriced = trading.reprice_orders(orders, {"A": 12000, "B": 11000}, 35000)

        self.assertEqual(repriced, [{"code": "A", "quantity": 2, "price": 12000, "value": 24000}, {"code": "B", "quantity": 1, "price": 11000, "value": 11000}])

    def test_live_rebalance_skips_when_target_order_exists_today(self):
        config = {
            "target_weights": {"A": 50, "B": 50},
            "daily_buy_limit_krw": 500000,
            "daily_sell_limit_per_asset_krw": 1000000,
            "sell_trigger_weight_pct": 30,
            "sell_target_weight_pct": 27,
        }
        with patch.object(trading, "fetch_today_orders", return_value=[{"pdno": "A"}]):
            result = trading.execute_live_rebalance(config, [], {}, {})

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["orders"], [])

    def test_kis_request_spacing_waits_until_the_next_slot(self):
        context = {"next_kis_request_at": 11.1}
        with patch.object(trading.time, "monotonic", side_effect=[10.0, 11.1]):
            with patch.object(trading.time, "sleep") as sleep:
                trading.wait_for_kis_request_slot(context)

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 1.1)
        self.assertAlmostEqual(context["next_kis_request_at"], 12.2)


class ContentTests(unittest.TestCase):
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

        telegram, markdown = briefing.build_content([], quotes, {"GROWTH": [], "INCOME": []}, [])

        self.assertIn("성장  목표 70% / 현재 66.6%  → 신규 매수 우선", telegram)
        self.assertIn("인컴  목표 30% / 현재 33.4%  → 신규 매수 보류", telegram)
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

        telegram, markdown = briefing.build_content([], quotes, {"SPMO": []}, [])

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

        telegram, markdown = briefing.build_content([], quotes, {"QLD": []}, [])

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
            [], quotes, {"ETF": []}, [],
            account_summary={"tot_evlu_amt": "220000", "prvs_rcdl_excc_amt": "50000"},
        )

        self.assertIn("계좌: 평가금액 ₩220,000 · 출금가능 ₩50,000", telegram)
        self.assertIn("평단 ₩25,000 · 평가손익 -30,000원", telegram)
        self.assertIn("## 💳 계좌 요약", markdown)

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

        telegram, _markdown = briefing.build_content([], quotes, {"SOXL": []}, [])

        self.assertIn("+$2.00", telegram)
        self.assertNotIn("+2원", telegram)


if __name__ == "__main__":
    unittest.main()
