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

```bash
cd client-reporting-api
uv pip install -e ".[dev]"

# All tests
pytest tests/ -v

# Unit tests only
pytest tests/unit/ -v

# With coverage (must reach 70%)
pytest tests/ --cov=client_reporting_api --cov-report=term-missing

# Parallel
pytest tests/ -n auto
```

## Quality Gates

```bash
bash scripts/quality-gates.sh
```

Runs ruff, basedpyright, pytest, bandit, pip-audit.

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

```bash
run_timeout 120 basedpyright client_reporting_api/
```
