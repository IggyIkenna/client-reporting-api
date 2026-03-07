# AGENTS.md

## Setup

```bash
uv sync --extra dev
source .venv/bin/activate
```

## Quality Gates

```bash
bash scripts/quality-gates.sh
```

## Type Checking

```bash
timeout 120 basedpyright client_reporting_api/
```

## Key Entry Points

- `client_reporting_api/main.py` — FastAPI application entry point
- `client_reporting_api/api/` — route definitions

## Notes

- Initialize events with `from unified_events_interface import setup_events`
- FastAPI service exposing client reporting data and statement generation
- Auth handled in `client_reporting_api/auth.py`
- Contains HTML templates in `client_reporting_api/templates/`
