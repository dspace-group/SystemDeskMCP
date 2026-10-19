"""Shared responses, errors, logging, and input constraints for MCP tools."""

import functools
import hashlib
import json
import logging
import time
import uuid
from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import Field

from systemdesk_mcp import __version__


class ErrorCode(str, Enum):
    """Stable, machine-parseable error codes returned in the 'error_code' field."""

    SYSTEMDESK_NOT_RUNNING = "SYSTEMDESK_NOT_RUNNING"
    TIMEOUT = "TIMEOUT"
    WIN32COM_NOT_INSTALLED = "WIN32COM_NOT_INSTALLED"
    PROGID_NOT_REGISTERED = "PROGID_NOT_REGISTERED"
    ACCESS_DENIED = "ACCESS_DENIED"
    BLOCKED_BY_DIALOG = "BLOCKED_BY_DIALOG"
    START_FAILED = "START_FAILED"
    PROJECT_SAVE_FAILED = "PROJECT_SAVE_FAILED"
    QUIT_FAILED = "QUIT_FAILED"
    PROJECT_CREATION_FAILED = "PROJECT_CREATION_FAILED"
    PROJECT_CLOSE_FAILED = "PROJECT_CLOSE_FAILED"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    NO_PROJECT_OPEN = "NO_PROJECT_OPEN"
    IMPORT_REJECTED = "IMPORT_REJECTED"
    IMPORT_FAILED = "IMPORT_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class PermanentError(Exception):
    """Signals a permanent (non-retryable) failure."""

    def __init__(self, message: str, error_code: ErrorCode):
        super().__init__(message)
        self.error_code = error_code


class TransientError(Exception):
    """Signals a transient (retryable) failure."""


def success_response(data: dict[str, Any]) -> dict[str, Any]:
    """Build a successful tool response with the supplied result fields."""
    return {"status": "success", **data}


def error_response(
    message: str,
    error_type: str,
    error_code: ErrorCode,
    details: Optional[str] = None,
) -> dict[str, Any]:
    """Build a tool error response with a stable code and optional details."""
    payload = {
        "status": "error",
        "error_type": error_type,
        "error_code": error_code.value,
        "message": message,
    }
    if details:
        payload["details"] = details
    return payload


_logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def logged(fn):
    """Emit a structured JSON log entry for every tool invocation."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        correlation_id = str(uuid.uuid4())
        input_hash = hashlib.sha256(
            json.dumps(kwargs, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        started_at = time.monotonic()
        result = fn(*args, **kwargs)
        duration_ms = round((time.monotonic() - started_at) * 1000)
        try:
            if isinstance(result, dict):
                outcome = result.get("status", "unknown")
            else:
                outcome = json.loads(result).get("status", "unknown")
        except Exception:  # pylint: disable=broad-exception-caught
            outcome = "unknown"
        _logger.info(json.dumps({
            "correlation_id": correlation_id,
            "tool": fn.__name__,
            "version": __version__,
            "input_hash": input_hash,
            "duration_ms": duration_ms,
            "outcome": outcome,
        }))
        return result
    return wrapper


_ABSOLUTE_PATH_PREFIX = r"^(?:[a-zA-Z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+)"
ABSOLUTE_PATH_PATTERN = _ABSOLUTE_PATH_PREFIX + r".*$"
ARXML_PATTERN = _ABSOLUTE_PATH_PREFIX + r".*\.[aA][rR][xX][mM][lL]$"
FILE_NAME_PATTERN = r'^[^<>:"/\\|?*\x00-\x1F]+$'
MAX_TIMEOUT_SECONDS = 3600
TimeoutSeconds = Annotated[int, Field(ge=1, le=MAX_TIMEOUT_SECONDS)]
