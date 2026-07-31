# client-reporting-api — Testing Guide

## Test Structure

```
tests/
├── __init__.py
├── conftest.py          — Shared fixtures (test client, mock data)
└── unit/
    └── (service and route unit tests)
```

## Running Tests

Run the repo quality gate — it drives tests through the correct `.venv` and enforces the coding-standard checks.
**Never run `pytest` directly** (wrong venv, and it bypasses the enforced gates).

```bash
cd client-reporting-api
bash scripts/quality-gates.sh            # ship mode (autofix + check)
bash scripts/quality-gates.sh --no-fix   # diagnostic / check only
```

The gate runs ruff, basedpyright, pytest (70%+ coverage), bandit, and pip-audit through the pinned `.venv`.
SSOT: `/codex/06-coding-standards/quality-gates.md`.

## Testing Fee Calculator

```python
from decimal import Decimal
from client_reporting_api.core.fee_calculator import FeeCalculator
from unified_internal_contracts import FeeStructure

fee_calc = FeeCalculator()
trader_fee, odum_fee, introducer_fee, server_cost = fee_calc.calculate_period_fees(
    client_id="test-client",
    opening_aum=Decimal("100000"),
    closing_aum=Decimal("110000"),
    trader_hwm=Decimal("105000"),
    odum_hwm=Decimal("100000"),
    fee_structure=FeeStructure(
        trader_fee_pct=0.20,
        odum_fee_pct=0.02,
        introducer_id=None,
        introducer_fee_pct=None,
    ),
    is_underwater=False,
)
assert trader_fee == Decimal("1000")  # 20% of 5000 above trader HWM
assert odum_fee == Decimal("200")     # 2% of 10000 above Odum HWM
```

## Testing Report Generator

```python
from client_reporting_api.core.report_generator import (
    generate_chart_base64,
    render_executive_summary,
)

chart = generate_chart_base64(
    monthly_returns=[1.2, -0.5, 2.1, 0.8],
    labels=["Jan", "Feb", "Mar", "Apr"],
)
assert isinstance(chart, str)  # base64 PNG

html = render_executive_summary(
    client_id="test-client",
    period_month="2026-02",
    closing_aum=110000.0,
    return_pct=5.2,
    currency="USD",
    chart_base64=chart,
)
assert "test-client" in html
```

## Testing Auth

```python
from fastapi.testclient import TestClient
from client_reporting_api.api.main import app

client = TestClient(app)

# Missing key
resp = client.get("/health")
# Health is unauthenticated — should return 200
assert resp.status_code == 200
```

## Type Checking

Type checking (`basedpyright`, strict) runs as part of the quality gate — invoke it via `bash scripts/quality-gates.sh`
rather than calling the checker standalone (the gate wires the correct `.venv` and config).
