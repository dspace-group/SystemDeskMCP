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

import pythoncom

from systemdesk_mcp import __version__
from systemdesk_mcp.common import ErrorCode, PermanentError, error_response

_application = None  # pylint: disable=invalid-name


def get_app():
    """Return the active SystemDesk COM connection or raise a permanent error."""
    if _application is None:
        raise PermanentError(
            "SystemDesk is not running. Call start_systemdesk first.",
            error_code=ErrorCode.SYSTEMDESK_NOT_RUNNING,
        )
    return _application


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
            global _application  # pylint: disable=global-statement
            _application = None
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
                + " SystemDesk may still be processing the request.",
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
