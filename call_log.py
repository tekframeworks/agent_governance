"""Shared call log: both apps append one line per event to logs/calls.log."""

import json
import logging
from pathlib import Path

LOG_FILE = Path(__file__).parent / "logs" / "calls.log"
LOG_FILE.parent.mkdir(exist_ok=True)


def get_logger(app_name: str) -> logging.Logger:
    logger = logging.getLogger(app_name)
    if not logger.handlers:
        handler = logging.FileHandler(LOG_FILE)  # append mode, shared by both apps
        handler.setFormatter(
            logging.Formatter(
                f"%(asctime)s.%(msecs)03d | {app_name} | %(message)s",
                "%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, event: str, **data) -> None:
    logger.info("%s | %s", event, json.dumps(data, default=str, ensure_ascii=False))
