"""Logging configuration — console + file output."""

import logging
import sys
from datetime import datetime, timezone


class _MigrationFormatter(logging.Formatter):
    """
    INFO records use the bare format so structured migration lines match the spec:
        [TIMESTAMP] [STATUS] object=... sf_attachment_id=... ...
    WARNING/ERROR/etc. prefix with [LEVELNAME] for diagnostics.
    """

    def __init__(self, datefmt: str = None) -> None:
        super().__init__(datefmt=datefmt)
        self._info = logging.Formatter("%(asctime)s %(message)s", datefmt=datefmt)
        self._other = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt=datefmt
        )

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno == logging.INFO:
            return self._info.format(record)
        return self._other.format(record)


def setup_logging(log_file: str = None) -> str:
    if log_file is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_file = f"migration_{ts}.log"

    datefmt = "%Y-%m-%dT%H:%M:%SZ"
    formatter = _MigrationFormatter(datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    root.addHandler(fh)

    return log_file
