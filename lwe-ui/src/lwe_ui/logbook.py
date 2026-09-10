"""The panel's log file: logs/panel/panel.log, written in real time by both panel processes.

Python logging and Qt messages land in the same file, one line each with a timestamp, the
process tag (window or tray) and the level; the file rotates by size so it can never grow
unbounded. Qt messages keep going to stderr as well, so the journal keeps its copy.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from .storage import paths

LOG_NAME = "panel.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 3

_installed: dict[str, logging.Logger] = {}


def log_file():
    return paths.log_dir("panel") / LOG_NAME


def install(process: str) -> logging.Logger:
    """Attach the file handler once per process and route Qt messages through it.

    `process` is the tag every line carries: "window" or "tray". Returns the root logger
    for the package. Safe to call twice; the second call returns the first logger.
    """
    if process in _installed:
        return _installed[process]
    logger = logging.getLogger("lwe_ui")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(f"%(asctime)s {process} %(levelname)s %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    try:
        paths.log_dir("panel").mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            str(log_file()), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    except OSError as exc:
        print(f"lwe-ui: panel log unavailable: {exc}", file=sys.stderr)
    _route_qt(logger)
    _route_exceptions(logger)
    _installed[process] = logger
    logger.info("panel %s started", process)
    return logger


def _route_qt(logger: logging.Logger) -> None:
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    qt = logger.getChild("qt")

    def handler(kind, context, message):
        qt.log(levels.get(kind, logging.INFO), "%s", message)
        try:
            print(message, file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass

    qInstallMessageHandler(handler)


def _route_exceptions(logger: logging.Logger) -> None:
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        logger.error("uncaught exception", exc_info=(exc_type, exc, tb))
        previous(exc_type, exc, tb)

    sys.excepthook = hook
