"""Single owner of the SystemDesk COM connection and its worker thread."""

import atexit
import concurrent.futures
import functools
import inspect

import pythoncom

from systemdesk_mcp.common import ErrorCode, _PermanentError, _error

_application = None  # pylint: disable=invalid-name


def _get_app():
    """Return the active SystemDesk COM connection or raise a permanent error."""
    if _application is None:
        raise _PermanentError(
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


def _on_com_thread(fn):
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
            return _error(
                f"Operation '{fn.__name__}' timed out"
                + (f" after {timeout}s." if timeout else ".")
                + " SystemDesk may still be processing the request.",
                error_type="transient",
                error_code=ErrorCode.TIMEOUT,
            )

    return wrapper
