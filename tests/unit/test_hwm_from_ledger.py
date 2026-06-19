"""Unit tests for the ledger-NAV-driven HWM (P3.5).

Covers ``client_reporting_api.core.hwm_from_ledger`` — the HWM materialised off
the ledger NAV (seed + cumulative ``totals.total_pnl``), NEVER ``max(equities)``.

Cases:
  - rising NAV → HWM advances every step, delta > 0;
  - dip then recovery → HWM FLAT through the dip (delta == 0), advances only
    above the prior peak;
  - HWM invariants hold (monotonic non-decreasing peak, delta >= 0, period
    ordering);
  - PnL-recovery seed: NAV below the recovery-seed peak → no new peak (delta 0).

Assertions are EXACT Decimals, all datetimes tz-aware UTC.

SSOT: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P3.5).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from client_reporting_api.core.hwm_from_ledger import (
    LedgerHwmInvariantError,
    hwm_from_ledger,
    ledger_nav_series,
)

_PERIOD_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_PERIOD_END = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
_DAY0 = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)


def _days(navs: list[Decimal]) -> list[tuple[datetime, Decimal]]:
    """Build a ``(as_of, nav)`` series, one T+1 day apart."""
    return [(_DAY0 + timedelta(days=i), nav) for i, nav in enumerate(navs)]


def _hwm(
    nav_series: list[tuple[datetime, Decimal]],
    *,
    prior_peak_nav: Decimal,
) -> list:
    return hwm_from_ledger(
        nav_series,
        prior_peak_nav=prior_peak_nav,
        client_id="client-A",
        share_class_id="USDT",
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
    )


# ---------------------------------------------------------------------------
# ledger_nav_series — NAV = seed + cumulative total_pnl
# ---------------------------------------------------------------------------


def test_ledger_nav_series_seed_plus_total_pnl() -> None:
    """NAV at each point = seed + that snapshot's totals.total_pnl (Decimal-exact)."""
    daily = [
        (_DAY0, {"totals": {"realized_pnl": "0", "unrealized_pnl": "100", "total_pnl": "100"}}),
        (_DAY0 + timedelta(days=1), {"totals": {"total_pnl": "250.50"}}),
        (_DAY0 + timedelta(days=2), {"totals": {"total_pnl": "-30"}}),
    ]
    series = ledger_nav_series(daily, seed=Decimal("1000"))
    assert series == [
        (_DAY0, Decimal("1100")),
        (_DAY0 + timedelta(days=1), Decimal("1250.50")),
        (_DAY0 + timedelta(days=2), Decimal("970")),
    ]


def test_ledger_nav_series_zero_seed() -> None:
    """A from-scratch account (seed 0) → NAV is exactly the cumulative total P&L."""
    daily = [(_DAY0, {"totals": {"total_pnl": "42.75"}})]
    assert ledger_nav_series(daily, seed=Decimal("0")) == [(_DAY0, Decimal("42.75"))]


def test_ledger_nav_series_missing_totals_raises() -> None:
    with pytest.raises(LedgerHwmInvariantError, match="missing 'totals'"):
        ledger_nav_series([(_DAY0, {"positions": []})], seed=Decimal("0"))


def test_ledger_nav_series_non_string_total_pnl_raises() -> None:
    with pytest.raises(LedgerHwmInvariantError, match="total_pnl must be a Decimal-string"):
        ledger_nav_series([(_DAY0, {"totals": {"total_pnl": 100}})], seed=Decimal("0"))


# ---------------------------------------------------------------------------
# hwm_from_ledger — rising NAV: HWM advances every step
# ---------------------------------------------------------------------------


def test_rising_nav_advances_every_step() -> None:
    series = _days([Decimal("1100"), Decimal("1200"), Decimal("1350")])
    rows = _hwm(series, prior_peak_nav=Decimal("1000"))

    assert [r.nav for r in rows] == [Decimal("1100"), Decimal("1200"), Decimal("1350")]
    # prior_peak carried forward = running max BEFORE each advance.
    assert [r.prior_peak_nav for r in rows] == [Decimal("1000"), Decimal("1100"), Decimal("1200")]
    # Every step is a new high → delta > 0.
    assert [r.delta for r in rows] == [Decimal("100"), Decimal("100"), Decimal("150")]
    assert all(r.delta > Decimal(0) for r in rows)
    # post-advance peak (prior_peak + delta) climbs monotonically.
    peaks = [r.prior_peak_nav + r.delta for r in rows]
    assert peaks == [Decimal("1100"), Decimal("1200"), Decimal("1350")]


def test_first_point_below_prior_peak_no_advance() -> None:
    """If the opening NAV is below the carried-in peak, delta is 0 (HWM holds)."""
    series = _days([Decimal("950")])
    rows = _hwm(series, prior_peak_nav=Decimal("1000"))
    assert rows[0].prior_peak_nav == Decimal("1000")
    assert rows[0].delta == Decimal("0")
    assert rows[0].nav == Decimal("950")


# ---------------------------------------------------------------------------
# hwm_from_ledger — dip then recovery: FLAT through the dip
# ---------------------------------------------------------------------------


def test_dip_then_recovery_hwm_flat_through_dip() -> None:
    # peak at 1300, drawdown to 1050/1150, recover to 1250 (still below 1300),
    # then break to a new high 1400.
    series = _days(
        [
            Decimal("1300"),  # new high
            Decimal("1050"),  # drawdown — HWM flat
            Decimal("1150"),  # still under peak — flat
            Decimal("1250"),  # recovering but below 1300 — flat
            Decimal("1400"),  # NEW all-time high
        ]
    )
    rows = _hwm(series, prior_peak_nav=Decimal("1000"))

    assert [r.delta for r in rows] == [
        Decimal("300"),  # 1300 - 1000
        Decimal("0"),  # under peak
        Decimal("0"),  # under peak
        Decimal("0"),  # under peak (recovery NOT a new high)
        Decimal("100"),  # 1400 - 1300
    ]
    # The HWM peak holds at 1300 through the entire drawdown, advances only at 1400.
    peaks = [r.prior_peak_nav + r.delta for r in rows]
    assert peaks == [
        Decimal("1300"),
        Decimal("1300"),
        Decimal("1300"),
        Decimal("1300"),
        Decimal("1400"),
    ]
    # prior_peak_nav stays pinned at 1300 once reached.
    assert [r.prior_peak_nav for r in rows] == [
        Decimal("1000"),
        Decimal("1300"),
        Decimal("1300"),
        Decimal("1300"),
        Decimal("1300"),
    ]


# ---------------------------------------------------------------------------
# Invariants hold on the output
# ---------------------------------------------------------------------------


def test_invariants_hold_on_output() -> None:
    series = _days([Decimal("1100"), Decimal("900"), Decimal("1500")])
    rows = _hwm(series, prior_peak_nav=Decimal("1000"))

    # delta >= 0 always; peak non-decreasing; delta == max(0, nav - prior_peak).
    prev_peak = None
    for r in rows:
        assert r.delta >= Decimal(0)
        assert r.delta == max(Decimal(0), r.nav - r.prior_peak_nav)
        peak = r.prior_peak_nav + r.delta
        if prev_peak is not None:
            assert peak >= prev_peak
        prev_peak = peak
        assert r.period_end > r.period_start
        assert r.as_of.tzinfo is not None  # tz-aware
        assert r.crystallization_due is False
        assert r.client_id == "client-A"
        assert r.share_class_id == "USDT"


def test_period_end_not_after_start_raises() -> None:
    series = _days([Decimal("1100")])
    with pytest.raises(LedgerHwmInvariantError, match="period_end"):
        hwm_from_ledger(
            series,
            prior_peak_nav=Decimal("1000"),
            client_id="client-A",
            share_class_id="USDT",
            period_start=_PERIOD_END,  # start == end → invalid
            period_end=_PERIOD_END,
        )


def test_empty_series_yields_no_rows() -> None:
    assert _hwm([], prior_peak_nav=Decimal("1000")) == []


# ---------------------------------------------------------------------------
# PnL-recovery seed case: NAV below the recovery seed → no new peak
# ---------------------------------------------------------------------------


def test_pnl_recovery_seed_nav_below_seed_no_new_peak() -> None:
    """A pnl_based account seeded with a recovery peak (e.g. GP's $75K seed).

    NAV recovering from a loss but still under the seed peak → delta stays 0
    (fees only resume once NAV climbs back above the recovery seed). Crossing the
    seed is the first real advance.
    """
    recovery_seed = Decimal("75000")
    series = _days(
        [
            Decimal("60000"),  # deep below seed
            Decimal("70000"),  # climbing, still under seed
            Decimal("74999.99"),  # one cent under seed — STILL no advance
            Decimal("80000"),  # crosses the seed → first advance
        ]
    )
    rows = _hwm(series, prior_peak_nav=recovery_seed)

    assert [r.delta for r in rows] == [
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("5000"),  # 80000 - 75000
    ]
    # peak pinned at the recovery seed until NAV exceeds it.
    peaks = [r.prior_peak_nav + r.delta for r in rows]
    assert peaks == [recovery_seed, recovery_seed, recovery_seed, Decimal("80000")]


def test_recovery_seed_via_nav_series_integration() -> None:
    """End-to-end: ledger_nav_series → hwm_from_ledger with a recovery seed.

    seed 75000, total_pnl moves -15000 → -5000 → +6000 (NAV 60000/70000/81000).
    """
    daily = [
        (_DAY0, {"totals": {"total_pnl": "-15000"}}),
        (_DAY0 + timedelta(days=1), {"totals": {"total_pnl": "-5000"}}),
        (_DAY0 + timedelta(days=2), {"totals": {"total_pnl": "6000"}}),
    ]
    series = ledger_nav_series(daily, seed=Decimal("75000"))
    assert [nav for _, nav in series] == [Decimal("60000"), Decimal("70000"), Decimal("81000")]

    rows = _hwm(series, prior_peak_nav=Decimal("75000"))
    assert [r.delta for r in rows] == [Decimal("0"), Decimal("0"), Decimal("6000")]
