"""Single owner of the SystemDesk COM connection, its worker thread, and tool logging."""

import atexit
import concurrent.futures
import functools
import hashlib
import inspect
import json
import logging
import time
import uuid
from typing import Optional

import pythoncom

from systemdesk_mcp import __version__
from systemdesk_mcp.common import ErrorCode, PermanentError, error_response

_application = None  # pylint: disable=invalid-name
_last_vecu = None  # pylint: disable=invalid-name


def get_app():
    """Return the active SystemDesk COM connection or raise a permanent error."""
    if _application is None:
        raise PermanentError(
            "SystemDesk is not running. Call start_systemdesk first.",
            error_code=ErrorCode.SYSTEMDESK_NOT_RUNNING,
        )
    return _application


def get_active_project():
    """Return the currently open SystemDesk project or raise a permanent error."""
    project = get_app().ActiveProject
    if project is None:
        raise PermanentError(
            "No project is currently open. Call create_project or open_project first.",
            error_code=ErrorCode.NO_PROJECT_OPEN,
        )
    return project


def get_vecu(vecu_name: Optional[str]):
    """Return the V-ECU named vecu_name, else the only V-ECU of the active project.

    Raises VECU_SELECTION_REQUIRED, listing the available V-ECUs, if vecu_name is omitted and the
    project contains several V-ECUs, so that the client asks the user which one to use.
    """
    vecus = get_active_project().VEcus
    if vecu_name is None:
        elements = list(vecus.Elements)
        if not elements:
            raise PermanentError(
                "The open project does not contain a V-ECU. Call get_or_create_vecu first.",
                error_code=ErrorCode.VECU_NOT_FOUND,
            )
        if len(elements) > 1:
            available = ", ".join(v.Name for v in elements)
            raise PermanentError(
                f"The open project contains several V-ECUs: {available}. Ask the user which V-ECU to use "
                "and call the tool again with vecu_name set to the chosen name.",
                error_code=ErrorCode.VECU_SELECTION_REQUIRED,
            )
        return elements[0]
    try:
        v_ecu = vecus.Item(vecu_name)
    except Exception:  # pylint: disable=broad-exception-caught
        v_ecu = None
    if v_ecu is None:
        available = ", ".join(v.Name for v in vecus.Elements) or "none"
        raise PermanentError(
            f"No V-ECU named '{vecu_name}' exists in the open project. Available V-ECUs: {available}. "
            "Call get_or_create_vecu to create it.",
            error_code=ErrorCode.VECU_NOT_FOUND,
        )
    return v_ecu


def _com_thread_init():
    pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)


_com_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="systemdesk-com",
    initializer=_com_thread_init,
)


@atexit.register
def _shutdown_com_executor():
    try:
        def _cleanup():
            global _application, _last_vecu  # pylint: disable=global-statement
            _application = None
            _last_vecu = None
            pythoncom.CoUninitialize()
        _com_executor.submit(_cleanup).result(timeout=5)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    _com_executor.shutdown(wait=False)


def on_com_thread(fn):
    """Run COM work on one worker; a timeout does not cancel the queued call."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        timeout = bound.arguments.get("timeout")
        future = _com_executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return error_response(
                f"Operation '{fn.__name__}' timed out"
                + (f" after {timeout}s." if timeout else ".")
                + " The operation continues in SystemDesk."
                + " Call get_systemdesk_status before retrying;"
                + " it waits until the pending operation has finished.",
                error_type="transient",
                error_code=ErrorCode.TIMEOUT,
            )

    return wrapper


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
