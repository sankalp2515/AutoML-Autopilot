import logging
import sys

try:
    import structlog
    _HAS_STRUCTLOG = True
except ImportError:
    _HAS_STRUCTLOG = False


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        level=level,
    )

    if not _HAS_STRUCTLOG:
        return

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer = (
        structlog.dev.ConsoleRenderer(colors=True)
        if sys.stdout.isatty()
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=shared_processors + [structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


class _StdlibLogger:
    """Minimal structlog-compatible wrapper over stdlib logging."""
    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(name)

    def info(self, event: str, **kw) -> None:
        self._log.info(f"{event} {kw}")

    def debug(self, event: str, **kw) -> None:
        self._log.debug(f"{event} {kw}")

    def warning(self, event: str, **kw) -> None:
        self._log.warning(f"{event} {kw}")

    def error(self, event: str, **kw) -> None:
        self._log.error(f"{event} {kw}")


def get_logger(name: str):
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return _StdlibLogger(name)
