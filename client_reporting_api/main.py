import logging

import uvicorn
from unified_events_interface import setup_events

logger = logging.getLogger(__name__)


def main() -> None:
    setup_events(service_name="client-reporting-api", mode="live", sink="cloud_logging")

    uvicorn.run(
        "client_reporting_api.api.main:app",
        host="0.0.0.0",  # nosec B104
        port=8080,
        reload=False,
    )


if __name__ == "__main__":
    main()
