"""Unit tests for client_reporting_api.core.report_generator."""

from __future__ import annotations

import base64

from client_reporting_api.core.report_generator import (
    generate_chart_base64,
    render_btc_investor_note,
    render_executive_summary,
)


def test_generate_chart_base64_returns_non_empty_string() -> None:
    """generate_chart_base64 returns a non-empty base64-encoded PNG string."""
    monthly_returns = [1.5, -0.8, 2.3, 0.5]
    labels = ["Jan", "Feb", "Mar", "Apr"]
    result = generate_chart_base64(monthly_returns, labels)
    assert isinstance(result, str)
    assert len(result) > 0
    # Must be valid base64
    decoded = base64.b64decode(result)
    # PNG magic bytes
    assert decoded[:4] == b"\x89PNG"


def test_generate_chart_base64_all_positive() -> None:
    """generate_chart_base64 works when all returns are positive."""
    result = generate_chart_base64([1.0, 2.0, 3.0], ["A", "B", "C"])
    assert len(result) > 0


def test_generate_chart_base64_all_negative() -> None:
    """generate_chart_base64 works when all returns are negative."""
    result = generate_chart_base64([-1.0, -2.0], ["X", "Y"])
    assert len(result) > 0


def test_generate_chart_base64_empty_data() -> None:
    """generate_chart_base64 handles empty lists."""
    result = generate_chart_base64([], [])
    assert isinstance(result, str)


def test_render_executive_summary_returns_html() -> None:
    """render_executive_summary returns a non-empty HTML string."""
    chart = generate_chart_base64([1.0, -0.5, 2.0], ["Jan", "Feb", "Mar"])
    html = render_executive_summary(
        client_id="TEST-001",
        period_month="2025-01",
        closing_aum=1_000_000.0,
        return_pct=1.5,
        currency="USD",
        chart_base64=chart,
        ai_summary="Strong performance driven by BTC allocation.",
    )
    assert isinstance(html, str)
    assert "TEST-001" in html
    assert "2025-01" in html


def test_render_executive_summary_without_ai_summary() -> None:
    """render_executive_summary works without an AI summary."""
    chart = generate_chart_base64([1.0], ["Jan"])
    html = render_executive_summary(
        client_id="TEST-002",
        period_month="2025-02",
        closing_aum=500_000.0,
        return_pct=-0.5,
        currency="EUR",
        chart_base64=chart,
        ai_summary=None,
    )
    assert isinstance(html, str)
    assert "TEST-002" in html


def test_render_btc_investor_note_returns_html() -> None:
    """render_btc_investor_note returns HTML containing client context."""
    html = render_btc_investor_note(
        client_id="BTC-001",
        period_month="2025-01",
        monthly_return=3.5,
        inception_return=42.0,
        start_balance=10_000.0,
        end_balance=10_350.0,
        currency="USD",
        inception_date="2023-01-01",
        monthly_returns=[{"period": "Jan 2025", "return": "+3.50%"}],
    )
    assert isinstance(html, str)
    assert "BTC-001" in html
    assert "2025-01" in html


def test_render_btc_investor_note_positive_return_prefix() -> None:
    """Positive monthly return gets a '+' prefix in the rendered output."""
    html = render_btc_investor_note(
        client_id="BTC-002",
        period_month="2025-02",
        monthly_return=1.25,
        inception_return=10.0,
        start_balance=50_000.0,
        end_balance=50_625.0,
        currency="USD",
    )
    assert "+1.25%" in html


def test_render_btc_investor_note_negative_return_no_prefix() -> None:
    """Negative monthly return does not get a '+' prefix."""
    html = render_btc_investor_note(
        client_id="BTC-003",
        period_month="2025-03",
        monthly_return=-2.0,
        inception_return=8.0,
        start_balance=50_000.0,
        end_balance=49_000.0,
        currency="USD",
    )
    assert "-2.00%" in html
    # Ensure no erroneous '+' prefix on a negative number
    assert "+-" not in html
