"""Unit tests for ledger-derived operator views (positions / balances / PnL totals).

Covers ``client_reporting_api.core.ledger_views.compute_ledger_views`` +
``read_ledger_rows`` — the REAL ledger-derived surface that replaces the mock
positions feed and the hardcoded ``realized_pnl="0.00"`` (P3.4 + P5.1).

Fixtures are ``LedgerRow``s (UAC): two TRADE rows opening + partially closing a
position, plus a PASSIVE funding-accrual row. Assertions are EXACT Decimal
values (string-serialised) — never ``"0.00"`` placeholders.

SSOT: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from unified_api_contracts import (
    EventOrigin,
    EventType,
    LedgerAssetClass,
    LedgerRow,
)

from client_reporting_api.core.ledger_views import compute_ledger_views, read_ledger_rows

# ---------------------------------------------------------------------------
# Fixtures — a small BTC-PERP tape: BUY 1.0 @ 60000, SELL 0.4 @ 65000, funding accrual
# ---------------------------------------------------------------------------

_AS_OF = datetime(2026, 5, 2, 0, 0, 0, tzinfo=UTC)


def _trade_row(
    *,
    row_id: str,
    side_delta: Decimal,
    price: Decimal,
    fees: Decimal,
    ts: datetime,
    venue: str = "hyperliquid",
    asset_canonical_id: str = "btc",
    asset_symbol: str = "BTC-PERP",
) -> LedgerRow:
    return LedgerRow(
        event_id=f"evt-{row_id}",
        row_id=row_id,
        event_origin=EventOrigin.INSTRUCTION,
        event_type=EventType.TRADE,
        timestamp_utc=ts,
        asset_group="cefi",
        venue=venue,
        account_id="acct-1",
        client_id="client-A",
        asset_symbol=asset_symbol,
        asset_canonical_id=asset_canonical_id,
        asset_class=LedgerAssetClass.PERP,
        delta=side_delta,
        price=price,
        quote_currency="USDT",
        fees_in_quote=fees,
    )


def _funding_row(*, ts: datetime, accrued: Decimal) -> LedgerRow:
    return LedgerRow(
        event_id="evt-funding-1",
        row_id="funding-1",
        event_origin=EventOrigin.PASSIVE,
        event_type=EventType.FUNDING_ACCRUAL,
        timestamp_utc=ts,
        asset_group="cefi",
        venue="hyperliquid",
        account_id="acct-1",
        client_id="client-A",
        asset_symbol="BTC-PERP",
        asset_canonical_id="btc",
        asset_class=LedgerAssetClass.PERP,
        delta=accrued,
        quote_currency="USDT",
        funding_rate=Decimal("0.0001"),
        accrual_period_start_utc=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
        accrual_period_end_utc=ts,
    )


def _tape() -> list[LedgerRow]:
    # BUY 1.0 @ 60000 (fee 6), then SELL 0.4 @ 65000 (fee 2.6), then funding +5.
    return [
        _trade_row(
            row_id="t1",
            side_delta=Decimal("1.0"),
            price=Decimal("60000"),
            fees=Decimal("6"),
            ts=datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC),
        ),
        _trade_row(
            row_id="t2",
            side_delta=Decimal("-0.4"),
            price=Decimal("65000"),
            fees=Decimal("2.6"),
            ts=datetime(2026, 5, 1, 14, 0, 0, tzinfo=UTC),
        ),
        _funding_row(ts=datetime(2026, 5, 1, 16, 0, 0, tzinfo=UTC), accrued=Decimal("5")),
    ]


# ---------------------------------------------------------------------------
# read_ledger_rows seam
# ---------------------------------------------------------------------------


class TestReadLedgerRows:
    def test_returns_empty_until_engine_wiring(self) -> None:
        assert read_ledger_rows("client-A") == ([], {})
        assert read_ledger_rows("client-A", as_of_date=_AS_OF.date()) == ([], {})


# ---------------------------------------------------------------------------
# compute_ledger_views — empty ledger → honest zero/empty response
# ---------------------------------------------------------------------------


class TestEmptyLedger:
    def test_empty_rows_is_honest_zero(self) -> None:
        views = compute_ledger_views([], marks={}, as_of=_AS_OF, share_class_of={})
        assert views["positions"] == []
        balances = views["balances"]
        assert isinstance(balances, dict)
        assert balances["by_venue"] == []
        assert balances["by_instrument"] == []
        assert balances["by_share_class"] == []
        totals = views["totals"]
        assert isinstance(totals, dict)
        # Honest zero — NOT a "0.00" placeholder, a real Decimal sum over zero rows.
        assert totals["realized_pnl"] == "0"
        assert totals["unrealized_pnl"] == "0"
        assert totals["total_pnl"] == "0"


# ---------------------------------------------------------------------------
# compute_ledger_views — real tape → exact Decimal positions / balances / totals
# ---------------------------------------------------------------------------


class TestPopulatedLedger:
    def test_position_net_qty_and_avg_cost(self) -> None:
        views = compute_ledger_views(
            _tape(),
            marks={"hyperliquid:btc": Decimal("66000")},
            as_of=_AS_OF,
            share_class_of={"btc": "USDT"},
        )
        positions = views["positions"]
        assert isinstance(positions, list)
        assert len(positions) == 1
        pos = positions[0]
        # 1.0 - 0.4 = 0.6 net; avg_cost stays at the open price (60000) on a reduce.
        assert pos["net_qty"] == "0.6"
        assert pos["avg_cost"] == "60000"
        assert pos["venue"] == "hyperliquid"
        assert pos["instrument_key"] == "hyperliquid:btc"
        assert pos["share_class"] == "USDT"

    def test_realized_pnl_exact_value(self) -> None:
        views = compute_ledger_views(
            _tape(),
            marks={"hyperliquid:btc": Decimal("66000")},
            as_of=_AS_OF,
            share_class_of={"btc": "USDT"},
        )
        # Realised on the 0.4 close: 0.4 * (65000 - 60000) = 2000; less fees 6 + 2.6 = 8.6
        # → 1991.4 from the TRADE leg. The PASSIVE funding accrual (+5) does NOT
        # move BTC net_qty but adds to realised PnL (carry IS the P&L): 1991.4 + 5 = 1996.4.
        totals = views["totals"]
        assert isinstance(totals, dict)
        assert Decimal(str(totals["realized_pnl"])) == Decimal("1996.4")
        assert totals["realized_pnl"] != "0.00"

    def test_funding_accrual_does_not_move_net_qty(self) -> None:
        views = compute_ledger_views(
            _tape(),
            marks={"hyperliquid:btc": Decimal("66000")},
            as_of=_AS_OF,
            share_class_of={"btc": "USDT"},
        )
        positions = views["positions"]
        assert isinstance(positions, list)
        # PASSIVE funding delta (+5 USDT) must NOT inflate the BTC position to 5.6.
        assert positions[0]["net_qty"] == "0.6"

    def test_unrealized_pnl_exact_value(self) -> None:
        views = compute_ledger_views(
            _tape(),
            marks={"hyperliquid:btc": Decimal("66000")},
            as_of=_AS_OF,
            share_class_of={"btc": "USDT"},
        )
        # Unrealised: net_qty 0.6 * (mark 66000 - avg_cost 60000) = 3600.
        totals = views["totals"]
        assert isinstance(totals, dict)
        assert Decimal(str(totals["unrealized_pnl"])) == Decimal("3600")
        assert Decimal(str(totals["total_pnl"])) == Decimal("1996.4") + Decimal("3600")

    def test_no_mark_yields_zero_unrealized(self) -> None:
        views = compute_ledger_views(
            _tape(),
            marks={},
            as_of=_AS_OF,
            share_class_of={"btc": "USDT"},
        )
        totals = views["totals"]
        assert isinstance(totals, dict)
        assert Decimal(str(totals["unrealized_pnl"])) == Decimal("0")
        # Realised is independent of marks (trade 1991.4 + funding 5).
        assert Decimal(str(totals["realized_pnl"])) == Decimal("1996.4")

    def test_balance_rollups_by_venue_instrument_share_class(self) -> None:
        # Two venues + two assets so the rollups are non-trivial.
        rows = [
            _trade_row(
                row_id="a1",
                side_delta=Decimal("2"),
                price=Decimal("100"),
                fees=Decimal("0"),
                ts=datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC),
                venue="hyperliquid",
                asset_canonical_id="btc",
                asset_symbol="BTC-PERP",
            ),
            _trade_row(
                row_id="a2",
                side_delta=Decimal("3"),
                price=Decimal("50"),
                fees=Decimal("0"),
                ts=datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC),
                venue="binance",
                asset_canonical_id="eth",
                asset_symbol="ETH-PERP",
            ),
        ]
        views = compute_ledger_views(
            rows,
            marks={"hyperliquid:btc": Decimal("110"), "binance:eth": Decimal("55")},
            as_of=_AS_OF,
            share_class_of={"btc": "USDT", "eth": "USDT"},
        )
        balances = views["balances"]
        assert isinstance(balances, dict)

        by_venue = {b["venue"]: b for b in balances["by_venue"]}
        assert by_venue["hyperliquid"]["net_qty"] == "2"
        assert by_venue["binance"]["net_qty"] == "3"
        # btc unrealised: 2 * (110-100) = 20; eth: 3 * (55-50) = 15.
        assert by_venue["hyperliquid"]["unrealized_pnl"] == "20"
        assert by_venue["binance"]["unrealized_pnl"] == "15"

        by_instrument = {b["instrument_key"]: b for b in balances["by_instrument"]}
        assert set(by_instrument) == {"hyperliquid:btc", "binance:eth"}

        # Both assets share USDT share_class → one rollup bucket summing both.
        by_sc = {b["share_class"]: b for b in balances["by_share_class"]}
        assert set(by_sc) == {"USDT"}
        assert by_sc["USDT"]["net_qty"] == "5"
        assert by_sc["USDT"]["unrealized_pnl"] == "35"


# ---------------------------------------------------------------------------
# read_ledger_rows — JSONL round-trip from the canonical client ledger_root
# ---------------------------------------------------------------------------


class TestReadLedgerRowsJsonl:
    """The monitoring chain: the engine writes JSONL via UTL ``write_run_ledger`` to the
    canonical ``client_ledger_root``; ``read_ledger_rows`` reads the SAME prefix back as
    ``LedgerRow``s (stripping the recon-only keys ``LedgerRow`` forbids)."""

    def test_round_trip_from_client_ledger_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unified_api_contracts.internal import FillModel, TradeFillRecord, make_trade_key
        from unified_trading_library.ledger import client_ledger_root, write_run_ledger  # noqa: qg-deep-import

        import client_reporting_api.core.ledger_views as lv

        monkeypatch.setenv("GCP_PROJECT_ID", "test-proj")

        # An in-memory fake GCS the writer writes to + the reader reads from.
        store: dict[str, str] = {}

        class _Blob:
            def __init__(self, key: str) -> None:
                self._key = key

            def upload_from_string(self, data: str, content_type: str = "") -> None:
                store[self._key] = data

        class _WBucket:
            def blob(self, gcs_path: str) -> _Blob:
                return _Blob(gcs_path)

        class _WClient:
            def bucket(self, name: str) -> _WBucket:
                return _WBucket()

        ik = "hyperliquid:PERPETUAL:BTC-PERP"
        ts = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
        fill = TradeFillRecord(
            trade_key=make_trade_key(ik, "i1", ts),
            instrument_key=ik,
            strategy_instruction_id="i1",
            tick_timestamp=ts,
            venue="hyperliquid",
            side="LONG",
            qty=Decimal("1.0"),
            fill_price=Decimal("60000"),
            fees_in_quote=Decimal("6"),
            fill_model=FillModel.BENCHMARK,
        )
        root = client_ledger_root("client-A", "run-2026-05-01", cloud="gcp")
        write_run_ledger(
            [fill],
            ledger_root=root,
            run_id="run-2026-05-01",
            account_id="acct-1",
            client_id="client-A",
            asset_group="cefi",
            quote_currency="USDT",
            storage_client=_WClient(),
        )

        # Reader uses get_storage_client() — patch it to read the same fake store.
        class _RStorage:
            def list_blobs(self, bucket: str, prefix: str = "") -> list[object]:
                return [type("B", (), {"name": k})() for k in sorted(store) if k.startswith(prefix)]

            def download_bytes(self, bucket: str, path: str) -> bytes:
                return store[path].encode("utf-8")

        monkeypatch.setattr(lv, "get_storage_client", lambda: _RStorage())  # type: ignore[attr-defined]

        rows, instrument_key_by_row_id = lv.read_ledger_rows("client-A")
        assert len(rows) == 1
        assert rows[0].event_type == EventType.TRADE
        assert rows[0].trade_id == fill.trade_key
        assert rows[0].delta == Decimal("1.0")  # LONG → positive
        assert rows[0].price == Decimal("60000")
        # The stamped canonical instrument_key is captured for the materialiser.
        assert instrument_key_by_row_id[rows[0].row_id] == ik


# ---------------------------------------------------------------------------
# attribution_breakdown — per-venue / per-instrument / per-factor / per-layer (P2.5.1)
# ---------------------------------------------------------------------------


class TestAttributionBreakdown:
    def test_groups_by_venue_instrument_factor_layer(self) -> None:
        from client_reporting_api.core.ledger_views import attribution_breakdown

        rows = [
            {
                "venue": "hyperliquid",
                "instrument_id": "BTC-PERP",
                "factor": "CARRY",
                "layer": "STRATEGY",
                "amount": "300",
            },
            {
                "venue": "hyperliquid",
                "instrument_id": "BTC-PERP",
                "factor": "SLIPPAGE",
                "layer": "EXECUTION",
                "amount": "-50",
            },
            {"venue": "binance", "instrument_id": "ETH-PERP", "factor": "CARRY", "layer": "STRATEGY", "amount": "120"},
        ]
        out = attribution_breakdown(rows)
        by_venue = {r["venue"]: r["amount"] for r in out["by_venue"]}
        assert by_venue["hyperliquid"] == "250"
        assert by_venue["binance"] == "120"
        by_factor = {r["factor"]: r["amount"] for r in out["by_factor"]}
        assert by_factor["CARRY"] == "420"
        assert by_factor["SLIPPAGE"] == "-50"
        by_layer = {r["layer"]: r["amount"] for r in out["by_layer"]}
        assert by_layer["STRATEGY"] == "420"
        assert by_layer["EXECUTION"] == "-50"
        assert out["total_amount"] == "370"

    def test_empty_is_honest_zero(self) -> None:
        from client_reporting_api.core.ledger_views import attribution_breakdown

        out = attribution_breakdown([])
        assert out["by_venue"] == []
        assert out["total_amount"] == "0"


# ---------------------------------------------------------------------------
# Canonical-run resolution + run-scoped reads + ledger-derived PnL (fix 2026-06-20)
# ---------------------------------------------------------------------------


class _FakeBlob:
    def __init__(self, name: str) -> None:
        self.name = name


def _fake_storage(store: dict[str, str]) -> object:
    """A fake storage client (the get_storage_client return) backed by an in-memory store."""

    class _Storage:
        def list_blobs(self, bucket: str, prefix: str = "") -> list[object]:
            return [_FakeBlob(k) for k in sorted(store) if k.startswith(prefix)]

        def download_bytes(self, bucket: str, path: str) -> bytes:
            return store[path].encode("utf-8")

    return _Storage()


def _write_run(
    store: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    client_id: str,
    run_id: str,
    fills: list[object],
    as_batch: bool = False,
) -> None:
    """Write a run's instruction ledger into the fake store via UTL write_run_ledger."""
    from unified_trading_library.ledger import client_ledger_root, write_run_ledger  # noqa: qg-deep-import

    monkeypatch.setenv("GCP_PROJECT_ID", "test-proj")
    root = client_ledger_root(client_id, run_id, cloud="gcp")
    if as_batch:
        root = f"{root.rstrip('/')}/__batch__/batch-{run_id}/ledger/"

    class _Blob:
        def __init__(self, key: str) -> None:
            self._key = key

        def upload_from_string(self, data: str, content_type: str = "") -> None:
            store[self._key] = data

    class _WBucket:
        def blob(self, gcs_path: str) -> _Blob:
            return _Blob(gcs_path)

    class _WClient:
        def bucket(self, name: str) -> _WBucket:
            return _WBucket()

    write_run_ledger(
        fills,
        ledger_root=root,
        run_id=run_id,
        account_id="acct-1",
        client_id=client_id,
        asset_group="defi",
        quote_currency="USDC",
        storage_client=_WClient(),
    )


def _eth_fill(*, ik: str, instr: str, side: str, qty: str, price: str, ts: datetime) -> object:
    from unified_api_contracts.internal import FillModel, TradeFillRecord, make_trade_key

    return TradeFillRecord(
        trade_key=make_trade_key(ik, instr, ts),
        instrument_key=ik,
        strategy_instruction_id=instr,
        tick_timestamp=ts,
        venue=ik.split(":")[0],
        side=side,
        qty=Decimal(qty),
        fill_price=Decimal(price),
        fees_in_quote=Decimal("0"),
        fill_model=FillModel.BENCHMARK,
    )


class TestCanonicalRunResolution:
    """Every per-client view must resolve THE SAME canonical run — the newest paper
    run — NOT read all runs concatenated (which doubled positions/PnL)."""

    def test_newest_of_two_runs_is_canonical_and_no_doubling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.ledger_views as lv

        store: dict[str, str] = {}
        ts = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
        ik = "LIDO:STAKING:ETH"
        # Two paper runs, each with ONE identical SUPPLY fill of 33.0 ETH.
        _write_run(
            store,
            monkeypatch,
            client_id="firm",
            run_id="paper-20260620002237-aaaa",
            fills=[_eth_fill(ik=ik, instr="i1", side="SUPPLY", qty="33.0", price="1", ts=ts)],
        )
        _write_run(
            store,
            monkeypatch,
            client_id="firm",
            run_id="paper-20260620004135-bbbb",
            fills=[_eth_fill(ik=ik, instr="i1", side="SUPPLY", qty="33.0", price="1", ts=ts)],
        )
        monkeypatch.setattr(lv, "get_storage_client", lambda: _fake_storage(store))  # type: ignore[attr-defined]

        # Canonical = lexicographically newest timestamped run.
        assert lv.resolve_canonical_run("firm") == "paper-20260620004135-bbbb"
        # read_ledger_rows reads ONLY that run — exactly ONE row, not two (no doubling).
        rows, _instrument_keys = lv.read_ledger_rows("firm")
        assert len(rows) == 1
        assert rows[0].delta == Decimal("33.0")

    def test_batch_rerun_objects_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.ledger_views as lv

        store: dict[str, str] = {}
        ts = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
        ik = "UNISWAP_V3:DEX_POOL:ETH"
        _write_run(
            store,
            monkeypatch,
            client_id="firm",
            run_id="paper-20260620004135-bbbb",
            fills=[_eth_fill(ik=ik, instr="i1", side="BUY", qty="2", price="3000", ts=ts)],
        )
        # A batch rerun copy under __batch__/ — must NOT count as a run nor be folded.
        _write_run(
            store,
            monkeypatch,
            client_id="firm",
            run_id="paper-20260620004135-bbbb",
            fills=[_eth_fill(ik=ik, instr="i1", side="BUY", qty="2", price="3000", ts=ts)],
            as_batch=True,
        )
        monkeypatch.setattr(lv, "get_storage_client", lambda: _fake_storage(store))  # type: ignore[attr-defined]

        assert lv.resolve_canonical_run("firm") == "paper-20260620004135-bbbb"
        rows, _instrument_keys = lv.read_ledger_rows("firm")
        assert len(rows) == 1  # batch copy excluded → no doubling

    def test_no_run_is_honest_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.ledger_views as lv

        monkeypatch.setattr(lv, "get_storage_client", lambda: _fake_storage({}))  # type: ignore[attr-defined]
        assert lv.resolve_canonical_run("nobody") is None
        assert lv.read_ledger_rows("nobody") == ([], {})
        run_id, fills = lv.read_canonical_run_fills("nobody")
        assert run_id is None
        assert fills == []


class TestReadCanonicalRunFills:
    def test_returns_keyed_fills_for_canonical_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.ledger_views as lv

        store: dict[str, str] = {}
        ts = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
        ik = "DERIBIT:PERPETUAL:ETH-PERP"
        _write_run(
            store,
            monkeypatch,
            client_id="firm",
            run_id="paper-20260620004135-bbbb",
            fills=[_eth_fill(ik=ik, instr="i1", side="LONG", qty="30.8", price="3000", ts=ts)],
        )
        monkeypatch.setattr(lv, "get_storage_client", lambda: _fake_storage(store))  # type: ignore[attr-defined]
        # load_instruction_ledger_fills (UTL) uses UTL's own get_storage_client —
        # patch it at the run_writer module so the fills reader hits the same store.
        import unified_trading_library.ledger.run_writer as _rw  # noqa: qg-deep-import

        monkeypatch.setattr(_rw, "get_storage_client", lambda: _fake_storage(store))  # type: ignore[attr-defined]

        run_id, fills = lv.read_canonical_run_fills("firm")
        assert run_id == "paper-20260620004135-bbbb"
        assert len(fills) == 1
        assert fills[0].instrument_key == ik
        assert fills[0].qty == Decimal("30.8")
        assert fills[0].fill_price == Decimal("3000")


class TestComputePnlEntries:
    def test_all_open_legs_zero_realized_one_entry_each(self) -> None:
        from client_reporting_api.core.ledger_views import compute_pnl_entries

        rows = [
            _trade_row(
                row_id="a",
                side_delta=Decimal("2"),
                price=Decimal("3000"),
                fees=Decimal("0"),
                ts=datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC),
                venue="UNISWAP_V3",
                asset_canonical_id="eth",
            ),
            _trade_row(
                row_id="b",
                side_delta=Decimal("33"),
                price=Decimal("1"),
                fees=Decimal("0"),
                ts=datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC),
                venue="LIDO",
                asset_canonical_id="eth",
            ),
        ]
        out = compute_pnl_entries(rows, marks={}, as_of=_AS_OF, share_class_of={})
        # Two distinct (venue, asset) groups → two entries; all-open → realized 0.
        assert len(out["entries"]) == 2
        assert out["realized_pnl_total"] == "0"
        for e in out["entries"]:
            assert e["realized_pnl"] == "0"

    def test_unrealized_from_mark(self) -> None:
        from client_reporting_api.core.ledger_views import compute_pnl_entries

        rows = [
            _trade_row(
                row_id="a",
                side_delta=Decimal("2"),
                price=Decimal("3000"),
                fees=Decimal("0"),
                ts=datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC),
                venue="UNISWAP_V3",
                asset_canonical_id="eth",
            ),
        ]
        # Rows built directly (no instrument_key_by_row_id) → legacy {venue}:{asset_canonical_id} key.
        out = compute_pnl_entries(rows, marks={"UNISWAP_V3:eth": Decimal("3100")}, as_of=_AS_OF, share_class_of={})
        assert out["unrealized_pnl_total"] == "200"  # 2 * (3100 - 3000)

    def test_empty_is_honest_zero(self) -> None:
        from client_reporting_api.core.ledger_views import compute_pnl_entries

        out = compute_pnl_entries([], marks={}, as_of=_AS_OF, share_class_of={})
        assert out["entries"] == []
        assert out["total_pnl"] == "0"


# ---------------------------------------------------------------------------
# read_marks — PricingLedger marks → {asset_canonical_id -> Decimal} (fix 2026-06-20)
# ---------------------------------------------------------------------------


def _write_pricing(
    store: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    client_id: str,
    run_id: str,
    marks: dict[str, str],
    object_name: str = "marks.jsonl",
) -> None:
    """Write a PricingLedger JSONL (event_type=mark_update rows) into the fake store.

    Lands under the SAME canonical client_ledger_root the instruction tape lives
    under, at ``ledger_type=pricing/<object_name>`` — exactly the prefix
    ``read_marks`` lists. One mark_update LedgerRow per (instrument_key, mark);
    ``marks`` is now keyed by the canonical ``VENUE:INSTRUMENT_TYPE:SYMBOL`` key
    (the per-leg key the materialiser joins marks on), and each row stamps that
    ``instrument_key`` (as the real writer does) so ``read_marks`` keys by it.
    """
    import json

    from unified_trading_library.ledger import client_ledger_root  # noqa: qg-deep-import

    monkeypatch.setenv("GCP_PROJECT_ID", "test-proj")
    root = client_ledger_root(client_id, run_id, cloud="gcp")
    # root is gs://bucket/ledger/client_id=.../run_id=.../ — strip the gs://bucket/ prefix to a key.
    key_prefix = root.split("/", 3)[3]
    key = f"{key_prefix}{_LEDGER_TYPE_PRICING_KEY}/{object_name}"
    lines = []
    for instrument_key, mark in marks.items():
        venue, _itype, symbol = instrument_key.split(":", 2)
        lines.append(
            json.dumps(
                {
                    "event_type": "mark_update",
                    "instrument_key": instrument_key,
                    "asset_canonical_id": symbol,
                    "price": mark,
                    "venue": venue,
                    "asset_symbol": symbol,
                }
            )
        )
    store[key] = "\n".join(lines)


_LEDGER_TYPE_PRICING_KEY = "ledger_type=pricing"


class TestReadMarks:
    """read_marks reads the canonical run's PricingLedger into a marks map, keyed
    by the canonical instrument_key (the key materialize_position_ledger joins
    marks on — so same-asset legs on different venues never collide)."""

    def test_reads_marks_for_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.ledger_views as lv

        store: dict[str, str] = {}
        _write_pricing(
            store,
            monkeypatch,
            client_id="firm",
            run_id="paper-20260620004135-bbbb",
            marks={"UNISWAP_V3:DEX_POOL:ETH": "3100", "DERIBIT:PERPETUAL:BTC-PERP": "66000"},
        )
        monkeypatch.setattr(lv, "get_storage_client", lambda: _fake_storage(store))  # type: ignore[attr-defined]

        marks = lv.read_marks("firm", "paper-20260620004135-bbbb")
        assert marks == {
            "UNISWAP_V3:DEX_POOL:ETH": Decimal("3100"),
            "DERIBIT:PERPETUAL:BTC-PERP": Decimal("66000"),
        }

    def test_no_pricing_ledger_is_honest_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.ledger_views as lv

        monkeypatch.setattr(lv, "get_storage_client", lambda: _fake_storage({}))  # type: ignore[attr-defined]
        assert lv.read_marks("firm", "paper-20260620004135-bbbb") == {}

    def test_empty_run_id_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.ledger_views as lv

        monkeypatch.setattr(lv, "get_storage_client", lambda: _fake_storage({}))  # type: ignore[attr-defined]
        assert lv.read_marks("firm", "") == {}

    def test_batch_pricing_copy_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.ledger_views as lv

        store: dict[str, str] = {}
        _write_pricing(
            store,
            monkeypatch,
            client_id="firm",
            run_id="paper-20260620004135-bbbb",
            marks={"UNISWAP_V3:DEX_POOL:ETH": "3100"},
        )
        # A __batch__ pricing copy must NOT be read into the paper view.
        import json as _json

        from unified_trading_library.ledger import client_ledger_root as _clr  # noqa: qg-deep-import

        root = _clr("firm", "paper-20260620004135-bbbb", cloud="gcp")
        key_prefix = root.split("/", 3)[3]
        batch_key = f"{key_prefix}__batch__/b/ledger/{_LEDGER_TYPE_PRICING_KEY}/marks.jsonl"
        store[batch_key] = _json.dumps(
            {
                "event_type": "mark_update",
                "instrument_key": "UNISWAP_V3:DEX_POOL:ETH",
                "asset_canonical_id": "ETH",
                "price": "9999",
                "venue": "UNISWAP_V3",
            }
        )
        monkeypatch.setattr(lv, "get_storage_client", lambda: _fake_storage(store))  # type: ignore[attr-defined]

        marks = lv.read_marks("firm", "paper-20260620004135-bbbb")
        assert marks == {"UNISWAP_V3:DEX_POOL:ETH": Decimal("3100")}  # batch copy's 9999 excluded

    def test_marks_drive_unrealized_pnl_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The full reader path: an opening fill + a PricingLedger mark → non-zero
        unrealized in compute_pnl_entries fed by read_ledger_rows + read_marks."""
        import client_reporting_api.core.ledger_views as lv

        store: dict[str, str] = {}
        ts = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
        ik = "UNISWAP_V3:DEX_POOL:ETH"
        _write_run(
            store,
            monkeypatch,
            client_id="firm",
            run_id="paper-20260620004135-bbbb",
            fills=[_eth_fill(ik=ik, instr="i1", side="BUY", qty="2", price="3000", ts=ts)],
        )
        # The mark is keyed by the SAME canonical instrument_key the fill carries
        # (UNISWAP_V3:DEX_POOL:ETH), so it joins the materialised position leg.
        _write_pricing(
            store,
            monkeypatch,
            client_id="firm",
            run_id="paper-20260620004135-bbbb",
            marks={"UNISWAP_V3:DEX_POOL:ETH": "3100"},
        )
        monkeypatch.setattr(lv, "get_storage_client", lambda: _fake_storage(store))  # type: ignore[attr-defined]

        run_id = lv.resolve_canonical_run("firm")
        assert run_id == "paper-20260620004135-bbbb"
        rows, instrument_key_by_row_id = lv.read_ledger_rows("firm")
        marks = lv.read_marks("firm", run_id)
        assert marks == {"UNISWAP_V3:DEX_POOL:ETH": Decimal("3100")}
        out = lv.compute_pnl_entries(
            rows, marks=marks, as_of=_AS_OF, share_class_of={}, instrument_key_by_row_id=instrument_key_by_row_id
        )
        # 2 ETH * (3100 - 3000) = 200 unrealized, marks-driven (was 0 with marks={}).
        assert out["unrealized_pnl_total"] == "200"
