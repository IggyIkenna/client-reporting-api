import logging

import uvicorn
from unified_events_interface import setup_events
from unified_trading_library import setup_tracing

logger = logging.getLogger(__name__)


def main() -> None:
    setup_events(service_name="client-reporting-api", mode="live", sink="cloud_logging")
    setup_tracing("client-reporting-api")

    uvicorn.run(
        "client_reporting_api.api.main:app",
        host="0.0.0.0",  # nosec B104
        port=8080,
        reload=False,
    )


if __name__ == "__main__":
    main()
