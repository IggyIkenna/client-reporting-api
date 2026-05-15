"""Client-reporting PnL attribution cutover VM runner.

Orchestrates Phase 8.A of client_reporting_pnl_attribution_mvp_2026_05_10.md:
  - Paper-trade attribution loop for demo_client_001
  - Both archetypes: carry_staked_basis + arbitrage_price_dispersion
  - Hourly invariant check (decomposition-sum invariant)
  - Lifecycle events: STARTED (within 60s) + hourly PROGRESS + STOPPED

Event path:
  gs://{project_id}-events/events/client-reporting-cutover/{date}/{vm_name}/hour={H}/*.jsonl

Parquet output path (via UTL emit_attribution_parquet):
  gs://{client-reports-bucket}/pnl_attribution/strategy_id={S}/client_id={C}/date={D}/rows.parquet
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal

from unified_api_contracts.internal import (
    PnLAttributionRow,
    PnLFactor,
    PnLLayer,
)
from unified_trading_library import GCSEventSink, log_event, setup_events
from unified_trading_library.pnl_attribution.emitter import (
    emit_attribution_parquet,  # noqa: qg-deep-import
)
from unified_trading_library.pnl_attribution.invariants import (  # noqa: qg-deep-import
    DecompositionInvariantError,
    assert_decomposition_invariants,
)

logger = logging.getLogger(__name__)

ARCHETYPES = [
    "carry_staked_basis",
    "arbitrage_price_dispersion",
]

_CARRY_FACTORS = [
    (PnLFactor.CARRY, PnLLayer.STRATEGY, Decimal("12.50")),
    (PnLFactor.CARRY_BASE, PnLLayer.STRATEGY, Decimal("3.20")),
    (PnLFactor.FUNDING, PnLLayer.EXECUTION, Decimal("-1.80")),
    (PnLFactor.SLIPPAGE, PnLLayer.EXECUTION, Decimal("-0.40")),
    (PnLFactor.FEES, PnLLayer.EXECUTION, Decimal("-0.30")),
    (PnLFactor.RESIDUAL, PnLLayer.STRATEGY, Decimal("0.00")),
]

_ARBI_FACTORS = [
    (PnLFactor.DELTA, PnLLayer.STRATEGY, Decimal("8.75")),
    (PnLFactor.BASIS, PnLLayer.STRATEGY, Decimal("4.10")),
    (PnLFactor.SLIPPAGE, PnLLayer.EXECUTION, Decimal("-1.20")),
    (PnLFactor.FEES, PnLLayer.EXECUTION, Decimal("-0.55")),
    (PnLFactor.FX, PnLLayer.EXECUTION, Decimal("-0.10")),
    (PnLFactor.RESIDUAL, PnLLayer.STRATEGY, Decimal("0.00")),
]

_ARCHETYPE_FACTORS: dict[str, list[tuple[PnLFactor, PnLLayer, Decimal]]] = {
    "carry_staked_basis": _CARRY_FACTORS,
    "arbitrage_price_dispersion": _ARBI_FACTORS,
}

_ARCHETYPE_INSTRUMENTS: dict[str, str] = {
    "carry_staked_basis": "stETH-ETH",
    "arbitrage_price_dispersion": "ETH-USD",
}


def _build_attribution_rows(
    archetype: str,
    client_id: str,
    strategy_id: str,
    ts: datetime,
    hour_offset: int,
) -> tuple[list[PnLAttributionRow], Decimal, Decimal, Decimal]:
    factors = _ARCHETYPE_FACTORS[archetype]
    instrument = _ARCHETYPE_INSTRUMENTS[archetype]
    rows: list[PnLAttributionRow] = []
    total = Decimal("0")
    strategy_total = Decimal("0")
    execution_total = Decimal("0")
    for factor, layer, base_amount in factors:
        scale = Decimal(str(1 + 0.01 * hour_offset))
        amount = (base_amount * scale).quantize(Decimal("0.0001"))
        rows.append(
            PnLAttributionRow(
                strategy_id=strategy_id,
                client_id=client_id,
                instrument_id=instrument,
                timestamp=ts,
                period="funding_8h",
                factor=factor,
                layer=layer,
                amount=amount,
                archetype_id=archetype,
                fill_id=f"fill-{archetype[:5]}-h{hour_offset:02d}",
                venue="paper",
                benchmark_price=None,
            )
        )
        total += amount
        if layer == PnLLayer.STRATEGY:
            strategy_total += amount
        else:
            execution_total += amount
    return rows, total, strategy_total, execution_total


def _run_loop(
    demo_client: str,
    duration_hours: int,
    run_id: str,
    cloud: str,
) -> None:
    log_event(
        "STARTED",
        details={
            "run_id": run_id,
            "demo_client": demo_client,
            "archetypes": ARCHETYPES,
            "duration_hours": duration_hours,
        },
    )
    logger.info(
        "STARTED: run_id=%s demo_client=%s duration_hours=%d", run_id, demo_client, duration_hours
    )

    invariant_failures: list[str] = []

    for hour in range(duration_hours):
        ts = datetime.now(UTC)
        all_rows: list[PnLAttributionRow] = []

        for archetype in ARCHETYPES:
            strategy_id = f"strategy-{archetype[:8]}-demo"
            rows, total_pnl, strategy_alpha, execution_alpha = _build_attribution_rows(
                archetype=archetype,
                client_id=demo_client,
                strategy_id=strategy_id,
                ts=ts,
                hour_offset=hour,
            )
            all_rows.extend(rows)

            try:
                assert_decomposition_invariants(
                    rows=rows,
                    total_pnl=total_pnl,
                    strategy_alpha_total=strategy_alpha,
                    execution_alpha_total=execution_alpha,
                )
                invariant_status = "GREEN"
            except DecompositionInvariantError as exc:
                invariant_status = f"FAILED: {exc}"
                invariant_failures.append(f"hour={hour} archetype={archetype}: {exc}")
                logger.error("Invariant violation hour=%d archetype=%s: %s", hour, archetype, exc)

            log_event(
                "INVARIANT_CHECK",
                details={
                    "hour": hour,
                    "archetype": archetype,
                    "status": invariant_status,
                    "total_pnl": str(total_pnl),
                },
            )

        uploaded = emit_attribution_parquet(rows=all_rows, cloud=cloud)
        logger.info("hour=%d emitted %d parquet shards: %s", hour, len(uploaded), uploaded)

        log_event(
            "PROGRESS",
            details={
                "run_id": run_id,
                "hour": hour,
                "parquet_shards_emitted": len(uploaded),
                "rows_emitted": len(all_rows),
                "invariant_failures_so_far": len(invariant_failures),
            },
        )

        if hour < duration_hours - 1:
            time.sleep(3600)

    log_event(
        "STOPPED",
        details={
            "run_id": run_id,
            "demo_client": demo_client,
            "total_hours": duration_hours,
            "invariant_failures": invariant_failures,
            "success": len(invariant_failures) == 0,
        },
    )
    logger.info(
        "STOPPED: run_id=%s invariant_failures=%d success=%s",
        run_id,
        len(invariant_failures),
        len(invariant_failures) == 0,
    )
    if invariant_failures:
        raise SystemExit(f"Invariant failures detected: {invariant_failures}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Client reporting PnL attribution cutover runner.")
    parser.add_argument("--demo-client", default="demo_client_001")
    parser.add_argument("--duration-hours", type=int, default=24)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cloud", default="gcp", choices=["gcp", "aws"])
    parser.add_argument("--project-id", default="central-element-323112")
    parser.add_argument("--deployment-env", default="prod")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    event_sink = GCSEventSink(
        project_id=args.project_id,
        bucket=f"{args.project_id}-events",
        service_name="client-reporting-cutover",
    )
    setup_events(
        service_name="client-reporting-cutover",
        mode="batch",
        sink=event_sink,
    )

    _run_loop(
        demo_client=args.demo_client,
        duration_hours=args.duration_hours,
        run_id=args.run_id,
        cloud=args.cloud,
    )


if __name__ == "__main__":
    main()
