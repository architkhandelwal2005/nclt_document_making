"""Opt-in integration tests for the live NCLT e-filing Case History portal.

Run serially with ``NCLT_LIVE_TESTS=1``.  These tests are kept separate from
deterministic fixture tests because government-portal availability and current
proceeding counts are external state.
"""

import asyncio
import os

import pytest

from nclt_fetcher import NcltFetcherConfig, NcltPortalClient


pytestmark = pytest.mark.skipif(
    os.environ.get("NCLT_LIVE_TESTS") != "1",
    reason="set NCLT_LIVE_TESTS=1 to exercise the external NCLT portal",
)


CASES = (
    ("72", 2024, "MAHAKALI FOODS PRIVATE LIMITED"),
    ("72", 2025, "KSHIPRA MOTORS PRIVATE LIMITED"),
    ("60", 2024, "ORGANIC WORLD PRIVATE LIMITED"),
)


@pytest.mark.parametrize(("number", "year", "respondent"), CASES)
def test_live_case_history_and_order_links(number, year, respondent):
    async def check():
        config = NcltFetcherConfig(headless=True, navigation_timeout_ms=60_000)
        client = NcltPortalClient(config)
        request = {
            "case_number": number,
            "case_year": year,
            "case_type": "16",
            "case_type_label": "Company Petition IB (IBC)",
            "bench": "indore",
        }
        session = await client.prepare_search(request)
        try:
            result = await client.search(session, request)
            assert result["status"] == "PROCEEDINGS_FETCHED"
            assert respondent in result["case"]["title"].upper()
            assert result["proceedings"]
            order_rows = [row for row in result["proceedings"] if row["order_url"]]
            assert order_rows
            assert all("/ordersview.drt?path=" in row["order_url"] for row in order_rows)
            response = await session.context.request.get(order_rows[0]["order_url"], timeout=60_000)
            assert response.ok
            first_pdf = await response.body()
            assert first_pdf.lstrip().startswith(b"%PDF")
            return {
                "case": result["case"]["case_number"],
                "title": result["case"]["title"],
                "proceedings": len(result["proceedings"]),
                "orders": len(order_rows),
                "first_pdf_bytes": len(first_pdf),
            }
        finally:
            await session.context.close()
            await session.browser.close()

    summary = asyncio.run(check())
    print(summary)
