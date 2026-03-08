import logging

import uvicorn
from unified_events_interface import setup_events
from unified_trading_library import PubSubEventSink, setup_tracing

from client_reporting_api.auth import _auth_cfg

logger = logging.getLogger(__name__)


def main() -> None:
    config = _auth_cfg
    sink = PubSubEventSink(
        project_id=config.gcp_project_id,
        topic="client-reporting-api-events",
        service_name="client-reporting-api",
    )
    setup_events(service_name="client-reporting-api", mode="live", sink=sink)
    setup_tracing("client-reporting-api")

    uvicorn.run(
        "client_reporting_api.api.main:app",
        host="0.0.0.0",  # nosec B104
        port=8080,
        reload=False,
    )


if __name__ == "__main__":
    main()
