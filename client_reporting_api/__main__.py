"""Dev entry point: python -m client_reporting_api."""

from __future__ import annotations

import uvicorn
from unified_config_interface import UnifiedCloudConfig

_cfg = UnifiedCloudConfig()
_port: int = getattr(_cfg, "port", 8014)  # config-bootstrap: PORT for uvicorn dev server
_reload = _cfg.runtime_mode == "local"

uvicorn.run(
    "client_reporting_api.api.main:app",
    host="0.0.0.0",  # nosec B104 — container runtime binds to all interfaces
    port=_port,
    reload=_reload,
)
