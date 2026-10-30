"""Shared responses, errors, and input constraints for MCP tools."""

from enum import Enum
from typing import Annotated, Any, Optional

from pydantic import Field


class ErrorCode(str, Enum):
    """Stable, machine-parseable error codes returned in the 'error_code' field."""

    SYSTEMDESK_NOT_RUNNING = "SYSTEMDESK_NOT_RUNNING"
    TIMEOUT = "TIMEOUT"
    WIN32COM_NOT_INSTALLED = "WIN32COM_NOT_INSTALLED"
    PROGID_NOT_REGISTERED = "PROGID_NOT_REGISTERED"
    ACCESS_DENIED = "ACCESS_DENIED"
    BLOCKED_BY_DIALOG = "BLOCKED_BY_DIALOG"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    NO_PROJECT_OPEN = "NO_PROJECT_OPEN"
    IMPORT_REJECTED = "IMPORT_REJECTED"
    AUTOSAR_ELEMENT_NOT_FOUND = "AUTOSAR_ELEMENT_NOT_FOUND"
    AMBIGUOUS_AUTOSAR_PATH = "AMBIGUOUS_AUTOSAR_PATH"
    INVALID_AUTOSAR_ELEMENT_TYPE = "INVALID_AUTOSAR_ELEMENT_TYPE"
    AUTOSAR_ELEMENT_ALREADY_EXISTS = "AUTOSAR_ELEMENT_ALREADY_EXISTS"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    COMPONENT_NOT_FOUND = "COMPONENT_NOT_FOUND"
    VECU_CREATION_FAILED = "VECU_CREATION_FAILED"
    VECU_NOT_FOUND = "VECU_NOT_FOUND"
    BSW_GENERATION_FAILED = "BSW_GENERATION_FAILED"
    FMU_EXPORT_FAILED = "FMU_EXPORT_FAILED"


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


_ABSOLUTE_PATH_PREFIX = r"^(?:[a-zA-Z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+)"
ABSOLUTE_PATH_PATTERN = _ABSOLUTE_PATH_PREFIX + r".*$"
ARXML_PATTERN = _ABSOLUTE_PATH_PREFIX + r".*\.[aA][rR][xX][mM][lL]$"
SDP_PATTERN = _ABSOLUTE_PATH_PREFIX + r".*\.[sS][dD][pP]$"
FILE_NAME_PATTERN = r'^[^<>:"/\\|?*\x00-\x1F]+$'

AUTOSAR_ELEMENT_PATH_PATTERN = r"^(/[a-zA-Z][a-zA-Z0-9_]{0,127}){2,}$"
AutosarElementPath = Annotated[str, Field(pattern=AUTOSAR_ELEMENT_PATH_PATTERN)]
AUTOSAR_SHORT_NAME_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$"
AutosarShortName = Annotated[str, Field(pattern=AUTOSAR_SHORT_NAME_PATTERN)]

MAX_TIMEOUT_SECONDS = 3600
TimeoutSeconds = Annotated[int, Field(ge=1, le=MAX_TIMEOUT_SECONDS)]
