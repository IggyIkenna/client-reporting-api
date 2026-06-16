import logging

import uvicorn
from unified_trading_library import PubSubEventSink, run_lifecycle, setup_events, setup_tracing

from client_reporting_api.config import get_config as _get_config

logger = logging.getLogger(__name__)


def main() -> None:
    config = _get_config()
    sink = PubSubEventSink(
        project_id=config.gcp_project_id,
        topic="client-reporting-api-events",
        service_name="client-reporting-api",
    )
    setup_events(service_name="client-reporting-api", mode="live", sink=sink)
    setup_tracing("client-reporting-api")

    # Wrap the server run in run_lifecycle so the process emits
    # CLIENT_REPORTING_API_RUN_STARTED on entry and RUN_COMPLETED/RUN_FAILED on
    # exit (STARTED/STOPPED/FAILED lifecycle coverage for the uvicorn launcher).
    with run_lifecycle(service_name="client-reporting-api"):
        uvicorn.run(
            "client_reporting_api.api.main:app",
            host="0.0.0.0",  # nosec B104
            port=8080,
            reload=False,
        )


if __name__ == "__main__":
    main()
