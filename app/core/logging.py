"""
Structured logging configuration with request tracing.

Uses structlog to provide:
  - Request ID tracing across all logs in a request
  - JSON output for production (CloudWatch-friendly)
  - Pretty console output for local development
  - Automatic context binding (request_id, user, etc.)

Usage in code:
    import structlog
    log = structlog.get_logger()

    # Basic logging
    log.info("Something happened")

    # With context (these fields appear in the JSON)
    log.info("Checking in guest", guest_id=12345, meetup_id="abc-123")

    # Bind context for all subsequent logs in this scope
    log = log.bind(user="carlos_staff")
    log.info("This log includes user field automatically")
"""

import logging
import sys

import structlog


def _drop_color_message(
    logger: logging.Logger, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """
    Remove uvicorn's color_message field - it contains ANSI escape codes
    meant for terminal rendering, useless noise in JSON logs.
    """
    event_dict.pop("color_message", None)
    return event_dict


def _rename_uvicorn_loggers(
    logger: logging.Logger, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """
    Rename uvicorn's confusingly-named loggers to something clearer.

    uvicorn.error   -> uvicorn (it's not just errors, it's all server events)
    uvicorn.access  -> access
    """
    logger_name = event_dict.get("logger")
    if logger_name == "uvicorn.error":
        event_dict["logger"] = "uvicorn"
    elif logger_name == "uvicorn.access":
        event_dict["logger"] = "access"
    return event_dict


def setup_logging(*, json_logs: bool = False, log_level: str = "INFO") -> None:
    """
    Configure structlog and stdlib logging.

    Args:
        json_logs: If True, output JSON (for production/CloudWatch).
                   If False, output pretty console logs (for local dev).
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
    """
    # Shared processors for both structlog and stdlib
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,  # Merge in request context
        structlog.stdlib.add_log_level,  # Add level field
        structlog.stdlib.add_logger_name,  # Add logger name
        structlog.stdlib.PositionalArgumentsFormatter(),  # Handle %s style logging
        structlog.stdlib.ExtraAdder(),  # Add extra dict from log records
        _drop_color_message,  # Remove uvicorn's ANSI color_message field
        _rename_uvicorn_loggers,  # Rename confusing uvicorn logger names
        structlog.processors.TimeStamper(fmt="iso"),  # ISO8601 timestamps
        structlog.processors.StackInfoRenderer(),  # Add stack info if present
        structlog.processors.UnicodeDecoder(),  # Decode bytes to str
    ]

    if json_logs:
        # Production: JSON output for CloudWatch
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        # Development: Pretty console output with colors
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to use structlog formatter
    # This ensures third-party libraries (httpx, sqlalchemy, etc.) also get formatted
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    # Also configure uvicorn's loggers to use our handler
    # Uvicorn creates these before importing the app, so we need to reconfigure them
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(handler)
        uvicorn_logger.propagate = False

    # Quiet noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a logger instance.

    Args:
        name: Logger name (usually __name__). If None, uses the caller's module.

    Returns:
        A bound structlog logger.
    """
    return structlog.get_logger(name)
