"""Unit tests for client_reporting_api.core.fee_calculator."""

from __future__ import annotations

from decimal import Decimal

import pytest
from unified_internal_contracts import FeeStructure

from client_reporting_api.core.fee_calculator import FeeCalculator


@pytest.fixture()
def calculator() -> FeeCalculator:
    return FeeCalculator()


@pytest.fixture()
def basic_fee_structure() -> FeeStructure:
    return FeeStructure(trader_fee_pct=Decimal("0.20"), odum_fee_pct=Decimal("0.05"))


def test_no_pnl_above_hwm_zero_fees(
    calculator: FeeCalculator, basic_fee_structure: FeeStructure
) -> None:
    """When closing AUM equals HWMs, all fees are zero."""
    trader_fee, odum_fee, introducer_fee, server_cost = calculator.calculate_period_fees(
        client_id="client-1",
        opening_aum=Decimal("100000"),
        closing_aum=Decimal("100000"),
        trader_hwm=Decimal("100000"),
        odum_hwm=Decimal("100000"),
        fee_structure=basic_fee_structure,
        is_underwater=False,
    )
    assert trader_fee == Decimal("0")
    assert odum_fee == Decimal("0")
    assert introducer_fee == Decimal("0")
    assert server_cost == Decimal("0")


def test_pnl_above_trader_hwm_charges_trader_fee(
    calculator: FeeCalculator, basic_fee_structure: FeeStructure
) -> None:
    """Trader fee is 20% of PnL above trader HWM."""
    trader_fee, odum_fee, introducer_fee, server_cost = calculator.calculate_period_fees(
        client_id="client-1",
        opening_aum=Decimal("100000"),
        closing_aum=Decimal("110000"),
        trader_hwm=Decimal("100000"),
        odum_hwm=Decimal("110000"),  # No PnL above odum HWM
        fee_structure=basic_fee_structure,
        is_underwater=False,
    )
    assert trader_fee == Decimal("2000")  # 20% of 10000
    assert odum_fee == Decimal("0")
    assert introducer_fee == Decimal("0")
    assert server_cost == Decimal("0")


def test_pnl_above_odum_hwm_charges_odum_fee(
    calculator: FeeCalculator, basic_fee_structure: FeeStructure
) -> None:
    """Odum fee is 5% of PnL above odum HWM."""
    trader_fee, odum_fee, _introducer_fee, _server_cost = calculator.calculate_period_fees(
        client_id="client-1",
        opening_aum=Decimal("100000"),
        closing_aum=Decimal("110000"),
        trader_hwm=Decimal("110000"),  # No PnL above trader HWM
        odum_hwm=Decimal("100000"),
        fee_structure=basic_fee_structure,
        is_underwater=False,
    )
    assert trader_fee == Decimal("0")
    assert odum_fee == Decimal("500")  # 5% of 10000


def test_introducer_fee_calculated_when_configured(calculator: FeeCalculator) -> None:
    """Introducer fee is a fraction of odum fee when introducer is configured."""
    fee_structure = FeeStructure(
        trader_fee_pct=Decimal("0.20"),
        odum_fee_pct=Decimal("0.10"),
        introducer_fee_pct=Decimal("0.50"),
        introducer_id="intro-1",
    )
    _, odum_fee, introducer_fee, _ = calculator.calculate_period_fees(
        client_id="client-1",
        opening_aum=Decimal("100000"),
        closing_aum=Decimal("110000"),
        trader_hwm=Decimal("110000"),
        odum_hwm=Decimal("100000"),
        fee_structure=fee_structure,
        is_underwater=False,
    )
    assert odum_fee == Decimal("1000")  # 10% of 10000
    assert introducer_fee == Decimal("500")  # 50% of 1000


def test_no_introducer_fee_without_introducer_id(calculator: FeeCalculator) -> None:
    """Introducer fee is zero when introducer_id is not set."""
    fee_structure = FeeStructure(
        trader_fee_pct=Decimal("0.20"),
        odum_fee_pct=Decimal("0.10"),
        introducer_fee_pct=Decimal("0.50"),
        introducer_id=None,  # No introducer id
    )
    _, _, introducer_fee, _ = calculator.calculate_period_fees(
        client_id="client-1",
        opening_aum=Decimal("100000"),
        closing_aum=Decimal("110000"),
        trader_hwm=Decimal("110000"),
        odum_hwm=Decimal("100000"),
        fee_structure=fee_structure,
        is_underwater=False,
    )
    assert introducer_fee == Decimal("0")


def test_server_cost_charged_when_underwater(
    calculator: FeeCalculator, basic_fee_structure: FeeStructure
) -> None:
    """Server cost is charged when account is underwater."""
    _, _, _, server_cost = calculator.calculate_period_fees(
        client_id="client-1",
        opening_aum=Decimal("100000"),
        closing_aum=Decimal("90000"),
        trader_hwm=Decimal("100000"),
        odum_hwm=Decimal("100000"),
        fee_structure=basic_fee_structure,
        is_underwater=True,
    )
    assert server_cost == Decimal("50")


def test_custom_server_cost(calculator: FeeCalculator, basic_fee_structure: FeeStructure) -> None:
    """Custom server cost is used when provided."""
    _, _, _, server_cost = calculator.calculate_period_fees(
        client_id="client-1",
        opening_aum=Decimal("100000"),
        closing_aum=Decimal("90000"),
        trader_hwm=Decimal("100000"),
        odum_hwm=Decimal("100000"),
        fee_structure=basic_fee_structure,
        is_underwater=True,
        server_cost_usd=Decimal("75"),
    )
    assert server_cost == Decimal("75")


def test_closing_below_hwm_no_negative_fees(
    calculator: FeeCalculator, basic_fee_structure: FeeStructure
) -> None:
    """Fees are never negative when closing AUM is below HWMs."""
    trader_fee, odum_fee, introducer_fee, _ = calculator.calculate_period_fees(
        client_id="client-1",
        opening_aum=Decimal("100000"),
        closing_aum=Decimal("80000"),
        trader_hwm=Decimal("100000"),
        odum_hwm=Decimal("100000"),
        fee_structure=basic_fee_structure,
        is_underwater=False,
    )
    assert trader_fee == Decimal("0")
    assert odum_fee == Decimal("0")
    assert introducer_fee == Decimal("0")
