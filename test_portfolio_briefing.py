import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import portfolio_briefing as briefing


class NewsFilteringTests(unittest.TestCase):
    def test_excludes_raw_title_before_translation(self):
        asset = {
            "ticker": "USD",
            "news_query": "semiconductors",
            "news_exclude": ["US dollar"],
        }
        raw_titles = [
            "US dollar rises before Fed decision - Reuters",
            "Semiconductor ETF rebounds after chip rally - MarketWatch",
        ]

        with patch.object(briefing, "fetch_news_for_query", return_value=raw_titles):
            with patch.object(
                briefing,
                "translate_title_if_needed",
                side_effect=lambda title: f"KO: {title}",
            ) as translate:
                titles = briefing.fetch_news_for_asset(asset, limit=1)

        self.assertEqual(
            titles,
            ["KO: Semiconductor ETF rebounds after chip rally - MarketWatch"],
        )
        translate.assert_called_once_with(
            "Semiconductor ETF rebounds after chip rally - MarketWatch"
        )

    def test_excludes_translated_title_after_translation(self):
        asset = {
            "ticker": "USD",
            "news_query": "semiconductors",
            "news_exclude": ["currency"],
        }

        with patch.object(
            briefing,
            "fetch_news_for_query",
            return_value=["Dollar index rises - Reuters"],
        ):
            with patch.object(
                briefing,
                "translate_title_if_needed",
                return_value="US currency rises - Reuters",
            ):
                titles = briefing.fetch_news_for_asset(asset, limit=1)

        self.assertEqual(titles, [])

    def test_returns_empty_when_limit_is_zero(self):
        asset = {"ticker": "QLD", "news_query": "Nasdaq 100"}

        with patch.object(briefing, "fetch_news_for_query") as fetch_news:
            titles = briefing.fetch_news_for_asset(asset, limit=0)

        self.assertEqual(titles, [])
        fetch_news.assert_not_called()

    def test_requires_relevant_news_terms(self):
        asset = {
            "ticker": "AIPO",
            "news_query": "AIPO ETF",
            "news_include": ["AIPO", "power infrastructure", "data center"],
            "news_exclude": ["Bitcoin", "MARA"],
        }
        raw_titles = [
            "AI debate heats up again - KBS News",
            "Defiance AIPO ETF tracks AI power infrastructure - ETF.com",
            "MARA falls as Bitcoin mining revenue slows - MarketWatch",
        ]

        with patch.object(briefing, "fetch_news_for_query", return_value=raw_titles):
            with patch.object(
                briefing,
                "translate_title_if_needed",
                side_effect=lambda title: title,
            ):
                titles = briefing.fetch_news_for_asset(asset, limit=2)

        self.assertEqual(
            titles,
            ["Defiance AIPO ETF tracks AI power infrastructure - ETF.com"],
        )


class FormattingTests(unittest.TestCase):
    def test_formats_signed_usd_amount(self):
        self.assertEqual(briefing.format_signed_amount(12.3, "USD"), "+$12.30")
        self.assertEqual(briefing.format_signed_amount(-4.5, "USD"), "-$4.50")

    def test_market_snapshot_summarizes_direction(self):
        quotes = [
            {"ticker": "QLD", "chg_pct": -3.77},
            {"ticker": "SSO", "chg_pct": -2.03},
            {"ticker": "426030", "chg_pct": 2.95},
        ]

        self.assertEqual(
            briefing.market_snapshot(quotes),
            "3개 중 상승 1개, 하락 2개 / 상대강세 426030 +2.95% / 최대약세 QLD -3.77%",
        )


class QuoteProviderTests(unittest.TestCase):
    def test_toss_urls_are_derived_from_base_url(self):
        with patch.object(briefing, "TOSS_BASE_URL", "https://open-api.example.com"):
            with patch.object(briefing, "TOSS_TOKEN_URL", ""):
                with patch.object(briefing, "TOSS_QUOTE_URL_TEMPLATE", ""):
                    self.assertEqual(
                        briefing.toss_token_url(),
                        "https://open-api.example.com/oauth2/token",
                    )
                    self.assertEqual(
                        briefing.toss_quote_url(),
                        "https://open-api.example.com/api/v1/prices",
                    )
                    self.assertEqual(
                        briefing.toss_candle_url(),
                        "https://open-api.example.com/api/v1/candles",
                    )

    def test_default_toss_base_url_is_official_openapi_host(self):
        with patch.object(briefing, "TOSS_BASE_URL", "https://openapi.tossinvest.com"):
            with patch.object(briefing, "TOSS_TOKEN_URL", ""):
                with patch.object(briefing, "TOSS_QUOTE_URL_TEMPLATE", ""):
                    self.assertEqual(
                        briefing.toss_token_url(),
                        "https://openapi.tossinvest.com/oauth2/token",
                    )
                    self.assertEqual(
                        briefing.toss_quote_url(),
                        "https://openapi.tossinvest.com/api/v1/prices",
                    )

    def test_parse_toss_quote_accepts_common_price_fields(self):
        asset = {"ticker": "426030", "symbol": "426030.KS", "currency": "KRW"}
        price_data = {"result": [{"symbol": "426030", "lastPrice": "56135", "currency": "KRW"}]}
        candle_data = {
            "result": {
                "candles": [
                    {"timestamp": "2026-03-25T09:00:00+09:00", "closePrice": "56135"},
                    {"timestamp": "2026-03-24T09:00:00+09:00", "closePrice": "54525"},
                ]
            }
        }

        quote = briefing.parse_toss_quote(asset, price_data, candle_data)

        self.assertEqual(quote["provider"], "Toss")
        self.assertEqual(quote["price"], 56135.0)
        self.assertEqual(quote["prev_close"], 54525.0)
        self.assertAlmostEqual(quote["chg_pct"], 2.9527739569005045)

    def test_fetch_quote_falls_back_to_yahoo_when_toss_fails(self):
        asset = {"ticker": "QLD", "symbol": "QLD", "currency": "USD"}
        yahoo_quote = {
            **asset,
            "price": 88.77,
            "prev_close": 92.25,
            "chg_amount": -3.48,
            "chg_pct": -3.77,
            "provider": "Yahoo",
        }

        with patch.object(briefing, "toss_is_configured", return_value=True):
            with patch.object(briefing, "fetch_toss_quote", side_effect=RuntimeError("boom")):
                with patch.object(briefing, "fetch_yahoo_quote", return_value=yahoo_quote):
                    with redirect_stdout(io.StringIO()):
                        quote = briefing.fetch_quote(asset)

        self.assertEqual(quote["provider"], "Yahoo")

    def test_fetch_toss_quote_uses_prices_and_daily_candles(self):
        asset = {"ticker": "426030", "symbol": "426030.KS", "currency": "KRW"}
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload
                self.status_code = 200
                self.headers = {}
                self.text = ""

            def json(self):
                return self.payload

        def fake_toss_request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/api/v1/prices"):
                return FakeResponse({"result": [{"symbol": "426030", "lastPrice": "56135"}]})
            if url.endswith("/api/v1/candles"):
                return FakeResponse(
                    {
                        "result": {
                            "candles": [
                                {"closePrice": "56135"},
                                {"closePrice": "54525"},
                            ]
                        }
                    }
                )
            raise AssertionError(url)

        with patch.object(briefing, "TOSS_ACCESS_TOKEN", "token-123"):
            with patch.object(briefing, "TOSS_BASE_URL", "https://openapi.tossinvest.com"):
                with patch.object(briefing, "TOSS_QUOTE_URL_TEMPLATE", ""):
                    with patch.object(briefing, "TOSS_CANDLE_URL_TEMPLATE", ""):
                        with patch.object(briefing, "toss_request", side_effect=fake_toss_request):
                            quote = briefing.fetch_toss_quote(asset)

        self.assertEqual(quote["provider"], "Toss")
        self.assertEqual(calls[0][2]["params"], {"symbols": "426030"})
        self.assertEqual(
            calls[1][2]["params"],
            {"symbol": "426030", "interval": "1d", "count": 2, "adjusted": "true"},
        )

    def test_fetch_toss_access_token_uses_client_credentials_form(self):
        class FakeResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {"access_token": "token-123"}

        captured = {}

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeResponse()

        with patch.object(briefing, "_TOSS_ACCESS_TOKEN_CACHE", None):
            with patch.object(briefing, "TOSS_ACCESS_TOKEN", ""):
                with patch.object(briefing, "TOSS_CLIENT_ID", "client-id"):
                    with patch.object(briefing, "TOSS_CLIENT_SECRET", "client-secret"):
                        with patch.object(briefing, "TOSS_TOKEN_URL", "https://openapi.tossinvest.com/oauth2/token"):
                            with patch.object(briefing.requests, "request", side_effect=fake_request):
                                token = briefing.fetch_toss_access_token()

        self.assertEqual(token, "token-123")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"], "https://openapi.tossinvest.com/oauth2/token")
        self.assertEqual(
            captured["kwargs"]["data"],
            {
                "grant_type": "client_credentials",
                "client_id": "client-id",
                "client_secret": "client-secret",
            },
        )

    def test_toss_error_envelope_includes_code_message_and_request_id(self):
        class FakeResponse:
            status_code = 401
            headers = {"X-Request-Id": "req-header"}
            text = ""

            def json(self):
                return {
                    "error": {
                        "requestId": "req-body",
                        "code": "invalid-token",
                        "message": "토큰이 유효하지 않습니다.",
                    }
                }

        error = briefing.toss_error_from_response(FakeResponse())

        self.assertEqual(error.status_code, 401)
        self.assertEqual(error.code, "invalid-token")
        self.assertEqual(error.request_id, "req-body")
        self.assertIn("토큰이 유효하지 않습니다.", str(error))
        self.assertIn("requestId=req-body", str(error))

    def test_toss_request_retries_429_then_succeeds(self):
        class FakeResponse:
            def __init__(self, status_code):
                self.status_code = status_code
                self.headers = {"Retry-After": "0"}
                self.text = ""

            def json(self):
                return {}

        responses = [FakeResponse(429), FakeResponse(200)]

        with patch.object(briefing.requests, "request", side_effect=responses):
            with patch.object(briefing.time, "sleep") as sleep:
                response = briefing.toss_request("GET", "https://example.com")

        self.assertEqual(response.status_code, 200)
        sleep.assert_called_once()


class HoldingsTests(unittest.TestCase):
    def test_apply_toss_holdings_enriches_matching_assets(self):
        assets = [
            {"ticker": "426030", "symbol": "426030.KS", "currency": "KRW"},
            {"ticker": "QLD", "symbol": "QLD", "currency": "USD"},
        ]
        holdings = [
            {
                "symbol": "426030",
                "quantity": "12",
                "averagePurchasePrice": "50000",
                "dailyProfitLoss": {"amount": "12000", "rate": "0.02"},
                "profitLoss": {"amount": "72000", "rate": "0.12"},
                "marketValue": {"amount": "672000"},
            }
        ]

        with patch.object(briefing, "toss_account_is_configured", return_value=True):
            with patch.object(briefing, "fetch_toss_holdings", return_value=holdings):
                enriched_assets, errors = briefing.apply_toss_holdings_to_assets(assets)

        self.assertEqual(errors, [])
        self.assertEqual(enriched_assets[0]["shares"], "12")
        self.assertEqual(enriched_assets[0]["daily_profit_loss_amount"], "12000")
        self.assertNotIn("shares", enriched_assets[1])

    def test_format_position_effect_prefers_toss_daily_profit_loss(self):
        item = {
            "currency": "KRW",
            "shares": "12",
            "chg_amount": 100,
            "daily_profit_loss_amount": "12000",
        }

        self.assertEqual(briefing.format_position_effect(item), ", 당일손익 +12,000원")


class AccountBriefingTests(unittest.TestCase):
    def test_account_summary_uses_holdings_and_buying_power(self):
        quotes = [
            {
                "currency": "KRW",
                "shares": "10",
                "price": 70000,
                "holding_market_value": "700000",
                "daily_profit_loss_amount": "12000",
                "holding_profit_loss_amount": "50000",
            },
            {
                "currency": "USD",
                "shares": "2",
                "price": 100,
                "daily_profit_loss_amount": "-3.5",
            },
        ]
        snapshot = {
            "buying_power": {"KRW": "300000", "USD": "25.5"},
            "open_orders": [{"orderId": "ord-1"}],
        }

        lines = briefing.account_summary_lines(quotes, snapshot)

        self.assertIn("KRW: 보유 1개, 평가금액 700,000원, 당일손익 +12,000원, 누적손익 +50,000원", lines)
        self.assertIn("USD: 보유 1개, 평가금액 $200.00, 당일손익 -$3.50", lines)
        self.assertIn("KRW 매수가능금액: 300,000원", lines)
        self.assertIn("USD 매수가능금액: $25.50", lines)
        self.assertIn("대기 주문: 1건", lines)

    def test_build_content_includes_account_section(self):
        quotes = [
            {
                "ticker": "426030",
                "display": "426030",
                "name": "TIMEFOLIO",
                "currency": "KRW",
                "price": 70000,
                "prev_close": 69000,
                "chg_amount": 1000,
                "chg_pct": 1.45,
                "shares": "10",
                "holding_market_value": "700000",
                "daily_profit_loss_amount": "12000",
                "provider": "Toss",
            }
        ]

        telegram, markdown = briefing.build_content(
            [],
            quotes,
            {"426030": []},
            [],
            {"buying_power": {"KRW": "300000"}, "open_orders": []},
        )

        self.assertIn("📌 계좌 현황", telegram)
        self.assertIn("KRW 매수가능금액: 300,000원", telegram)
        self.assertIn("## 📌 계좌 현황", markdown)
        self.assertIn("대기 주문: 0건", markdown)


class TossOrderApiTests(unittest.TestCase):
    def test_fetch_toss_buying_power_uses_currency_and_account_header(self):
        captured = {}

        class FakeResponse:
            def json(self):
                return {"result": {"currency": "KRW", "cashBuyingPower": "100000"}}

        def fake_toss_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeResponse()

        with patch.object(briefing, "TOSS_ACCESS_TOKEN", "token-123"):
            with patch.object(briefing, "TOSS_ACCOUNT_SEQ", "1"):
                with patch.object(briefing, "toss_request", side_effect=fake_toss_request):
                    result = briefing.fetch_toss_buying_power("KRW")

        self.assertEqual(result["cashBuyingPower"], "100000")
        self.assertEqual(captured["method"], "GET")
        self.assertTrue(captured["url"].endswith("/api/v1/buying-power"))
        self.assertEqual(captured["kwargs"]["params"], {"currency": "KRW"})
        self.assertEqual(captured["kwargs"]["headers"]["X-Tossinvest-Account"], "1")

    def test_fetch_toss_sellable_quantity_uses_symbol(self):
        captured = {}

        class FakeResponse:
            def json(self):
                return {"result": {"sellableQuantity": "3"}}

        def fake_toss_request(method, url, **kwargs):
            captured["kwargs"] = kwargs
            return FakeResponse()

        with patch.object(briefing, "TOSS_ACCESS_TOKEN", "token-123"):
            with patch.object(briefing, "TOSS_ACCOUNT_SEQ", "1"):
                with patch.object(briefing, "toss_request", side_effect=fake_toss_request):
                    result = briefing.fetch_toss_sellable_quantity("005930")

        self.assertEqual(result["sellableQuantity"], "3")
        self.assertEqual(captured["kwargs"]["params"], {"symbol": "005930"})

    def test_build_toss_order_payload_validates_quantity_or_amount(self):
        payload = briefing.build_toss_order_payload(
            "005930",
            "BUY",
            "LIMIT",
            quantity=1,
            price=70000,
            client_order_id="client-1",
        )

        self.assertEqual(
            payload,
            {
                "symbol": "005930",
                "side": "BUY",
                "orderType": "LIMIT",
                "quantity": "1",
                "price": "70000",
                "clientOrderId": "client-1",
            },
        )
        with self.assertRaises(ValueError):
            briefing.build_toss_order_payload("005930", "BUY", "LIMIT", price=70000)
        with self.assertRaises(ValueError):
            briefing.build_toss_order_payload("005930", "BUY", "MARKET", quantity=1, price=70000)

    def test_submit_toss_order_defaults_to_dry_run_without_post(self):
        payload = briefing.build_toss_order_payload("005930", "BUY", "MARKET", quantity=1)

        with patch.object(briefing, "TOSS_ACCESS_TOKEN", "token-123"):
            with patch.object(briefing, "toss_request") as toss_request:
                result = briefing.submit_toss_order(payload)

        self.assertTrue(result["dryRun"])
        self.assertEqual(result["payload"], payload)
        toss_request.assert_not_called()

    def test_submit_toss_order_posts_only_when_live_lock_is_open(self):
        captured = {}

        class FakeResponse:
            def json(self):
                return {"result": {"orderId": "ord-1"}}

        def fake_toss_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeResponse()

        payload = briefing.build_toss_order_payload("005930", "BUY", "MARKET", quantity=1)

        with patch.object(briefing, "TOSS_ACCESS_TOKEN", "token-123"):
            with patch.object(briefing, "TOSS_ACCOUNT_SEQ", "1"):
                with patch.object(briefing, "TOSS_ENABLE_LIVE_ORDERS", "true"):
                    with patch.object(briefing, "TOSS_LIVE_ORDER_CONFIRM", briefing.TOSS_LIVE_ORDER_CONFIRM_PHRASE):
                        with patch.object(briefing, "toss_request", side_effect=fake_toss_request):
                            result = briefing.submit_toss_order(payload, dry_run=False)

        self.assertEqual(result["orderId"], "ord-1")
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["url"].endswith("/api/v1/orders"))
        self.assertEqual(captured["kwargs"]["json"], payload)
        self.assertEqual(captured["kwargs"]["headers"]["X-Tossinvest-Account"], "1")

    def test_modify_and_cancel_orders_are_guarded_by_dry_run(self):
        with patch.object(briefing, "TOSS_ACCESS_TOKEN", "token-123"):
            with patch.object(briefing, "toss_request") as toss_request:
                modify_result = briefing.modify_toss_order("ord-1", {"quantity": "2"})
                cancel_result = briefing.cancel_toss_order("ord-1")

        self.assertTrue(modify_result["dryRun"])
        self.assertTrue(cancel_result["dryRun"])
        self.assertTrue(modify_result["url"].endswith("/api/v1/orders/ord-1/modify"))
        self.assertTrue(cancel_result["url"].endswith("/api/v1/orders/ord-1/cancel"))
        toss_request.assert_not_called()

    def test_fetch_toss_management_snapshot_collects_account_data(self):
        with patch.object(briefing, "toss_account_is_configured", return_value=True):
            with patch.object(
                briefing,
                "fetch_toss_buying_power",
                side_effect=[
                    {"cashBuyingPower": "100000"},
                    {"cashBuyingPower": "12.5"},
                ],
            ):
                with patch.object(briefing, "fetch_toss_open_orders", return_value=[{"orderId": "ord-1"}]):
                    snapshot, errors = briefing.fetch_toss_management_snapshot()

        self.assertEqual(errors, [])
        self.assertEqual(snapshot["buying_power"], {"KRW": "100000", "USD": "12.5"})
        self.assertEqual(len(snapshot["open_orders"]), 1)


if __name__ == "__main__":
    unittest.main()
