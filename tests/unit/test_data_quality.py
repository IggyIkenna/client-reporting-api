"""Unit tests for the run-level data-quality view (P11.19, CRA half).

Covers ``client_reporting_api.core.data_quality`` + the
``/api/v1/clients/{client_id}/data-quality`` route — the REAL honest-coverage
surface that shows the operator WHAT DATA IS MISSING / INCOMPLETE for a paper run,
merged with the live VM alert stream.

Fixtures mirror the REAL ``skipped_specs`` sidecar shape
(``{"archetype","slot_label","reason"}``) the engine pins beside the run ledgers
and the ``run_manifest.strategy_ids`` drivable book. Assertions are EXACT counts —
the coverage math is a fold of two real lists, never a re-derivation.

SSOT: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P11.19).
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

import client_reporting_api.core.data_quality as dq


def _skipped(archetype: str, slot_label: str, reason: str) -> dict[str, str]:
    return {"archetype": archetype, "slot_label": slot_label, "reason": reason}


# A small but representative book: 3 drivable strategies (2 carry, 1 arb) + 4
# skipped specs across 2 reasons + 2 archetypes (one of which is NOT in the
# drivable book — the disjoint broader universe, exactly like the real run).
_STRATEGY_IDS = (
    "CARRY_STAKED_BASIS@lido-uniswapv3-deribit-f100-usdc-1h-usdc-v2-prod",
    "CARRY_STAKED_BASIS@lido-curve-bybit-f100-usdt-1h-usdt-v2-prod",
    "ARBITRAGE_PRICE_DISPERSION@uniswapv3-curve-eth-1h-usdc-v1-prod",
)

_SKIPPED = [
    _skipped("CARRY_BASIS_PERP", "CARRY_BASIS_PERP@binance-btc-1h-usdt-v3-prod", "missing_funding_venue_or_coin"),
    _skipped("CARRY_BASIS_PERP", "CARRY_BASIS_PERP@okx-eth-1h-usdt-v3-prod", "missing_funding_venue_or_coin"),
    _skipped(
        "ARBITRAGE_PRICE_DISPERSION",
        "ARBITRAGE_PRICE_DISPERSION@kalshi-poly-1h-usdc-v1-prod",
        "non_dex_dispersion_config",
    ),
    _skipped(
        "CARRY_BASIS_PERP",
        "CARRY_BASIS_PERP@hyperliquid-sol-1h-usdt-v3-prod",
        "no_gcs_data_in_window:2026-05-16..2026-05-22",
    ),
]


class _FakeManifest:
    def __init__(self, strategy_ids: tuple[str, ...]) -> None:
        self.strategy_ids = strategy_ids


# ---------------------------------------------------------------------------
# core.compute_data_quality
# ---------------------------------------------------------------------------


class TestComputeDataQuality:
    def test_skipped_grouped_by_archetype_reason_and_coverage_math(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dq, "resolve_canonical_run", lambda *a, **k: "paper-20260621-test")
        monkeypatch.setattr(dq, "read_run_manifest", lambda *a, **k: _FakeManifest(_STRATEGY_IDS))
        monkeypatch.setattr(dq, "client_ledger_root", lambda *a, **k: "gs://b/ledger/client_id=c/run_id=r/")
        monkeypatch.setattr(dq, "read_skipped_specs", lambda *a, **k: list(_SKIPPED))

        out = dq.compute_data_quality("firm")

        assert out["run_id"] == "paper-20260621-test"

        cov = out["coverage"]
        assert cov["drivable"] == 3
        assert cov["skipped"] == 4
        assert cov["total_specs"] == 7  # drivable + skipped, disjoint sets

        by_arch = cov["by_archetype"]
        # by_archetype is the canonical LIST shape the UI's DataQualityCoverageRow[]
        # contract reads with .map/.reduce (a dict would crash the panel).
        assert isinstance(by_arch, list)
        rows = {r["archetype"]: r for r in by_arch}
        # drivable archetypes from strategy_ids
        assert rows["CARRY_STAKED_BASIS"] == {
            "archetype": "CARRY_STAKED_BASIS",
            "drivable": 2,
            "skipped": 0,
            "total": 2,
        }
        # ARB has 1 drivable + 1 skipped
        assert rows["ARBITRAGE_PRICE_DISPERSION"] == {
            "archetype": "ARBITRAGE_PRICE_DISPERSION",
            "drivable": 1,
            "skipped": 1,
            "total": 2,
        }
        # CARRY_BASIS_PERP is skipped-only (not in the drivable book)
        assert rows["CARRY_BASIS_PERP"] == {
            "archetype": "CARRY_BASIS_PERP",
            "drivable": 0,
            "skipped": 3,
            "total": 3,
        }
        # sorted by archetype name (the order the UI maps in)
        assert [r["archetype"] for r in by_arch] == [
            "ARBITRAGE_PRICE_DISPERSION",
            "CARRY_BASIS_PERP",
            "CARRY_STAKED_BASIS",
        ]

        skipped = out["skipped"]
        assert len(skipped) == 4
        # sorted by (reason, archetype, spec) → first reason alphabetically is
        # "missing_funding_venue_or_coin"
        assert skipped[0]["reason"] == "missing_funding_venue_or_coin"
        # verbatim spec + parsed venue/coin
        first = skipped[0]
        assert first["spec"] == "CARRY_BASIS_PERP@binance-btc-1h-usdt-v3-prod"
        assert first["archetype"] == "CARRY_BASIS_PERP"
        assert first["venue"] == "binance"
        assert first["coin"] == "btc"
        # every skipped row carries the required keys
        for row in skipped:
            assert set(row.keys()) == {"spec", "archetype", "venue", "coin", "reason"}

    def test_no_run_is_honest_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dq, "resolve_canonical_run", lambda *a, **k: None)
        out = dq.compute_data_quality("nobody")
        assert out["run_id"] is None
        assert out["skipped"] == []
        assert out["coverage"]["total_specs"] == 0
        assert "no paper run" in out["note"]

    def test_absent_skipped_sidecar_is_empty_not_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dq, "resolve_canonical_run", lambda *a, **k: "paper-r")
        monkeypatch.setattr(dq, "read_run_manifest", lambda *a, **k: _FakeManifest(_STRATEGY_IDS))
        monkeypatch.setattr(dq, "client_ledger_root", lambda *a, **k: "gs://b/x/")
        monkeypatch.setattr(dq, "read_skipped_specs", lambda *a, **k: [])

        out = dq.compute_data_quality("firm")
        assert out["skipped"] == []
        assert out["coverage"]["drivable"] == 3
        assert out["coverage"]["skipped"] == 0
        assert "ran every declared spec" in out["note"]


# ---------------------------------------------------------------------------
# read_skipped_specs — honest empty on a missing sidecar
# ---------------------------------------------------------------------------


class TestReadSkippedSpecs:
    def test_missing_sidecar_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Storage:
            def download_bytes(self, bucket: str, path: str) -> bytes:
                raise FileNotFoundError(path)

        monkeypatch.setattr(dq, "client_ledger_root", lambda *a, **k: "gs://b/ledger/client_id=c/run_id=r/")
        monkeypatch.setattr(dq, "get_storage_client", lambda: _Storage())
        assert dq.read_skipped_specs("firm", "r") == []

    def test_parses_real_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        payload = json.dumps({"run_id": "r", "skipped": _SKIPPED}).encode("utf-8")

        class _Storage:
            def download_bytes(self, bucket: str, path: str) -> bytes:
                assert path.endswith("skipped_specs/r.json")
                return payload

        monkeypatch.setattr(dq, "client_ledger_root", lambda *a, **k: "gs://b/ledger/client_id=c/run_id=r/")
        monkeypatch.setattr(dq, "get_storage_client", lambda: _Storage())
        out = dq.read_skipped_specs("firm", "r")
        assert len(out) == 4
        assert out[0]["slot_label"].startswith("CARRY_BASIS_PERP@")


# ---------------------------------------------------------------------------
# /api/v1/clients/{client_id}/data-quality route — alerts merge + degradation
# ---------------------------------------------------------------------------


def _patch_dq_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    import client_reporting_api.api.routes.data_quality as route

    monkeypatch.setattr(
        route,
        "compute_data_quality",
        lambda *a, **k: {
            "run_id": "paper-r",
            "coverage": {
                "total_specs": 7,
                "drivable": 3,
                "skipped": 4,
                "by_archetype": [
                    {"archetype": "ARBITRAGE_PRICE_DISPERSION", "drivable": 1, "skipped": 1, "total": 2},
                    {"archetype": "CARRY_BASIS_PERP", "drivable": 0, "skipped": 3, "total": 3},
                    {"archetype": "CARRY_STAKED_BASIS", "drivable": 2, "skipped": 0, "total": 2},
                ],
            },
            "skipped": [
                {"spec": s["slot_label"], "archetype": s["archetype"], "venue": "", "coin": "", "reason": s["reason"]}
                for s in _SKIPPED
            ],
            "note": "",
        },
    )
    monkeypatch.setattr(route, "enforce_entitlement", lambda auth, cid: None)


class _FakeResp:
    """Minimal httpx.Response stand-in carrying a deployment-api JSON payload."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


# ---------------------------------------------------------------------------
# core.deployment_api_client — the SSOT HTTP client (P11.20 alerts + P11.21 coverage)
# ---------------------------------------------------------------------------


class TestDeploymentApiClient:
    def test_alerts_ok_returns_list_and_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.deployment_api_client as dac

        payload: dict[str, object] = {"alerts": [{"kind": "vm_down"}], "streams": []}
        monkeypatch.setattr(dac.httpx, "get", lambda url, timeout=5.0: _FakeResp(payload))
        alerts, source = dac.get_unified_alerts()
        assert source == "deployment-api"
        assert alerts == [{"kind": "vm_down"}]

    def test_alerts_unreachable_degrades(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.deployment_api_client as dac

        def _boom(url: str, timeout: float = 5.0) -> _FakeResp:
            raise httpx.ConnectError("down")

        monkeypatch.setattr(dac.httpx, "get", _boom)
        alerts, source = dac.get_unified_alerts()
        assert (alerts, source) == ([], "unavailable")

    def test_coverage_ok_returns_by_asset_group(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.deployment_api_client as dac

        payload: dict[str, object] = {
            "by_asset_group": {
                "cefi": {"captured": 716159, "total": 35829048, "coverage_pct": 11.68},
            }
        }
        monkeypatch.setattr(dac.httpx, "get", lambda url, timeout=5.0: _FakeResp(payload))
        by_ag, source = dac.get_data_status_coverage()
        assert source == "deployment-api"
        assert by_ag["cefi"]["coverage_pct"] == 11.68

    def test_coverage_unreachable_degrades(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.core.deployment_api_client as dac

        def _boom(url: str, timeout: float = 5.0) -> _FakeResp:
            raise httpx.ConnectError("down")

        monkeypatch.setattr(dac.httpx, "get", _boom)
        by_ag, source = dac.get_data_status_coverage()
        assert (by_ag, source) == ({}, "unavailable")


class TestDataQualityRoute:
    def test_alerts_merged_when_deployment_api_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.api.routes.data_quality as route

        _patch_dq_payload(monkeypatch)
        # The route calls the deployment_api_client; patch the client functions.
        monkeypatch.setattr(
            route.deployment_api_client,
            "get_unified_alerts",
            lambda: (
                [
                    {
                        "kind": "vm_down",
                        "timestamp": "2026-06-22T00:00:00Z",
                        "repo": "mtds",
                        "workflow_name": "capture",
                        "severity": "CRITICAL",
                        "message": "VM down — capture halted",
                        "alert_class": "vm_down",
                    }
                ],
                "deployment-api",
            ),
        )
        monkeypatch.setattr(
            route.deployment_api_client,
            "get_data_status_coverage",
            lambda: ({"cefi": {"captured": 5, "total": 10, "coverage_pct": 50.0}}, "deployment-api"),
        )

        out = route.get_data_quality("firm", auth=object())  # type: ignore[arg-type]
        assert out["run_id"] == "paper-r"
        assert out["coverage"]["skipped"] == 4
        assert len(out["skipped"]) == 4
        assert out["alerts_source"] == "deployment-api"
        alerts = out["alerts"]
        assert isinstance(alerts, list) and len(alerts) == 1
        a = alerts[0]
        # mapped to the UI DataQualityAlert closed shape (never the raw ledger shape)
        assert set(a.keys()) == {"id", "severity", "title", "detail", "source", "timestamp"}
        assert a["severity"] == "critical"  # CRITICAL → critical (closed set)
        assert a["title"] == "capture"
        assert a["source"] == "vm_down"
        assert a["detail"] == "VM down — capture halted"
        # P11.21 — manifest_coverage from the deployment-api SSOT (a LIST, never a dict)
        mc = out["manifest_coverage"]
        assert out["manifest_source"] == "deployment-api"
        assert isinstance(mc, list) and len(mc) == 1
        assert mc[0]["asset_group"] == "cefi"
        assert mc[0]["coverage_pct"] == 50.0
        assert set(mc[0].keys()) == {
            "asset_group",
            "captured",
            "empty_confirmed",
            "attempted_failed",
            "expected_unattempted",
            "total",
            "coverage_pct",
        }
        # generated_utc is a parseable UTC ISO timestamp
        datetime.fromisoformat(str(out["generated_utc"]))

    def test_deployment_api_down_still_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import client_reporting_api.api.routes.data_quality as route

        _patch_dq_payload(monkeypatch)
        monkeypatch.setattr(route.deployment_api_client, "get_unified_alerts", lambda: ([], "unavailable"))
        monkeypatch.setattr(route.deployment_api_client, "get_data_status_coverage", lambda: ({}, "unavailable"))

        out = route.get_data_quality("firm", auth=object())  # type: ignore[arg-type]
        # the run data-quality survives — only the deployment-api lenses degrade
        assert out["coverage"]["skipped"] == 4
        assert out["alerts"] == []
        assert out["alerts_source"] == "unavailable"
        assert out["manifest_coverage"] == []
        assert out["manifest_source"] == "unavailable"
