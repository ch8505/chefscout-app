"""Start the server: python -m chefscout"""

from __future__ import annotations

import uvicorn

from chefscout.config import get_settings
from chefscout.logging_config import configure_logging
from chefscout.server.app import create_app


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    print(f"Dishly is running: http://{settings.host}:{settings.port}")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
