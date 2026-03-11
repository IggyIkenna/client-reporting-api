from decimal import Decimal

from unified_internal_contracts import FeeStructure


class FeeCalculator:
    def calculate_period_fees(
        self,
        client_id: str,
        opening_aum: Decimal,
        closing_aum: Decimal,
        trader_hwm: Decimal,
        odum_hwm: Decimal,
        fee_structure: FeeStructure,
        is_underwater: bool,
        server_cost_usd: Decimal = Decimal("50"),
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        pnl_above_trader_hwm = max(Decimal("0"), closing_aum - trader_hwm)
        trader_fee = pnl_above_trader_hwm * fee_structure.trader_fee_pct

        pnl_above_odum_hwm = max(Decimal("0"), closing_aum - odum_hwm)
        odum_fee = pnl_above_odum_hwm * fee_structure.odum_fee_pct

        introducer_fee = Decimal("0")
        if fee_structure.introducer_fee_pct and fee_structure.introducer_id:
            introducer_fee = odum_fee * fee_structure.introducer_fee_pct

        server_cost = server_cost_usd if is_underwater else Decimal("0")

        return trader_fee, odum_fee, introducer_fee, server_cost
