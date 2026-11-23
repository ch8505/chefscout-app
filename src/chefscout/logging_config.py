"""Logging setup. Every module logs through logging.getLogger(__name__)."""

from __future__ import annotations

import logging

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format=FORMAT, datefmt="%H:%M:%S", force=True)
    # Third-party libraries are noisy at INFO.
    for name in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)
