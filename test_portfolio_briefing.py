import unittest
from unittest.mock import mock_open, patch

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
            {"ticker": "AMD", "chg_pct": 2.95},
        ]

        self.assertEqual(
            briefing.market_snapshot(quotes),
            "3개 중 상승 1개, 하락 2개 / 상대강세 AMD +2.95% / 최대약세 QLD -3.77%",
        )


class FundamentalScreeningTests(unittest.TestCase):
    def test_load_screener_config_returns_none_when_disabled(self):
        with patch.object(briefing.os.path, "exists", return_value=True):
            with patch(
                "builtins.open",
                mock_open(read_data='{"enabled": false, "symbols": ["AAPL"]}'),
            ):
                self.assertIsNone(briefing.load_screener_config())

    def test_fetch_yahoo_fundamentals_parses_quote_summary(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "quoteSummary": {
                        "result": [
                            {
                                "price": {"shortName": "AAA Inc.", "currency": "USD"},
                                "summaryDetail": {
                                    "trailingPE": {"raw": 10.5},
                                    "priceToSalesTrailing12Months": {"raw": 2.1},
                                },
                                "defaultKeyStatistics": {"priceToBook": {"raw": 1.2}},
                                "financialData": {"returnOnEquity": {"raw": 0.18}},
                            }
                        ]
                    }
                }

        with patch.object(briefing.requests, "get", return_value=FakeResponse()):
            data = briefing.fetch_yahoo_fundamentals("AAA")

        self.assertEqual(data["symbol"], "AAA")
        self.assertEqual(data["name"], "AAA Inc.")
        self.assertEqual(data["currency"], "USD")
        self.assertEqual(data["roe"], 0.18)
        self.assertEqual(data["per"], 10.5)
        self.assertEqual(data["psr"], 2.1)
        self.assertEqual(data["pbr"], 1.2)

    def test_passes_requested_value_criteria(self):
        item = {"roe": 0.16, "per": 14.9, "psr": 2.99, "pbr": 1.5}
        criteria = {
            "min_roe": 0.15,
            "max_per": 15,
            "exclude_psr_gte": 3,
            "max_pbr": 1.5,
        }

        self.assertTrue(briefing.passes_fundamental_screen(item, criteria))
        self.assertFalse(briefing.passes_fundamental_screen({**item, "psr": 3}, criteria))
        self.assertFalse(briefing.passes_fundamental_screen({**item, "roe": 0.149}, criteria))

    def test_screen_candidates_sorts_passing_symbols(self):
        payloads = {
            "AAA": {"symbol": "AAA", "roe": 0.2, "per": 10, "psr": 2, "pbr": 1, "currency": "USD"},
            "BBB": {"symbol": "BBB", "roe": 0.16, "per": 8, "psr": 2, "pbr": 1, "currency": "USD"},
            "CCC": {"symbol": "CCC", "roe": 0.3, "per": 20, "psr": 2, "pbr": 1, "currency": "USD"},
        }

        with patch.object(briefing, "fetch_yahoo_fundamentals", side_effect=lambda symbol: payloads[symbol]):
            candidates, errors = briefing.screen_fundamental_candidates(
                {
                    "criteria": {
                        "min_roe": 0.15,
                        "max_per": 15,
                        "exclude_psr_gte": 3,
                        "max_pbr": 1.5,
                    },
                    "symbols": ["AAA", "BBB", "CCC"],
                }
            )

        self.assertEqual(errors, [])
        self.assertEqual([item["symbol"] for item in candidates], ["AAA", "BBB"])


class QuoteProviderTests(unittest.TestCase):
    def test_env_value_uses_default_for_empty_environment_value(self):
        with patch.dict(briefing.os.environ, {"EMPTY_SETTING": ""}):
            self.assertEqual(
                briefing.env_value("EMPTY_SETTING", "fallback"),
                "fallback",
            )

    def test_fetch_quote_uses_yahoo(self):
        asset = {"ticker": "QLD", "symbol": "QLD", "currency": "USD"}
        yahoo_quote = {
            **asset,
            "price": 88.77,
            "prev_close": 92.25,
            "chg_amount": -3.48,
            "chg_pct": -3.77,
            "provider": "Yahoo",
        }

        with patch.object(briefing, "fetch_yahoo_quote", return_value=yahoo_quote) as fetch_yahoo:
            quote = briefing.fetch_quote(asset)

        self.assertEqual(quote["provider"], "Yahoo")
        fetch_yahoo.assert_called_once_with(asset)


class ContentTests(unittest.TestCase):
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
                "provider": "Yahoo",
            }
        ]

        telegram, markdown = briefing.build_content([], quotes, {"QLD": []}, [])

        self.assertNotIn("계좌", telegram)
        self.assertNotIn("계좌", markdown)


if __name__ == "__main__":
    unittest.main()
