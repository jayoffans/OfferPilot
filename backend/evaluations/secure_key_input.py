"""Fail-closed terminal credential input for an explicitly authorized evaluation.

This helper never reads environment variables or command-line credentials. The
caller must still obtain authorization before invoking it and hand the result
to the existing session Key management path. No credential is logged or stored.
"""

import getpass
import os
import re
import sys
import warnings


_PROMPT = "DeepSeek API Key（隐藏输入，仅本次会话，不保存）："
_ERROR_MESSAGES = {
    "terminal_unavailable": "无法验证安全的交互终端，拒绝输入 Key；请使用 Web 会话 BYOK 或原生终端。",
    "secure_input_failed": "隐藏 Key 输入失败或已取消，未继续运行。",
    "invalid_key": "Key 格式无效，未继续运行。",
}


class UnsafeKeyInputError(ValueError):
    """Only fixed, credential-free messages may cross the CLI boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(_ERROR_MESSAGES[code])


def _windows_console_is_secure(stdin_fd: int, stderr_fd: int) -> bool:
    """Reject pipes, redirected CRT descriptors and replaced console handles.

    isatty alone is insufficient on Windows. getpass uses the process console
    through msvcrt rather than the Python stream, so both must identify the same
    native console. GetConsoleMode verifies console handles without changing
    settings; msvcrt.getwch performs the actual non-echoing read.
    """
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    invalid_handle = ctypes.c_void_p(-1).value
    for descriptor, std_handle in ((stdin_fd, -10), (stderr_fd, -12)):
        handle = kernel32.GetStdHandle(ctypes.c_uint32(std_handle).value)
        if handle in (None, 0, -1, invalid_handle):
            return False
        if handle != msvcrt.get_osfhandle(descriptor):
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
    return True


def _posix_terminal_is_secure(stdin_fd: int, stderr_fd: int) -> bool:
    """Check native terminal control availability before asking for a secret.

    getpass still performs and restores the echo transition. Any failure in
    that transition becomes GetPassWarning and is rejected before fallback IO.
    """
    import termios

    if not callable(termios.tcsetattr):
        return False
    for descriptor in (stdin_fd, stderr_fd):
        attributes = termios.tcgetattr(descriptor)
        if len(attributes) != 7 or not isinstance(attributes[3], int):
            return False
    return isinstance(termios.ECHO, int) and isinstance(termios.TCSAFLUSH, int)


def _terminal_is_secure() -> bool:
    """Require unwrapped original streams, native standard fds and a TTY."""
    try:
        if (sys.stdin is None or sys.stderr is None or
                sys.stdin is not sys.__stdin__ or sys.stderr is not sys.__stderr__):
            return False
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            return False
        stdin_fd, stderr_fd = sys.stdin.fileno(), sys.stderr.fileno()
        if (stdin_fd, stderr_fd) != (0, 2):
            return False
        if not os.isatty(stdin_fd) or not os.isatty(stderr_fd):
            return False
        if os.name == "nt":
            return _windows_console_is_secure(stdin_fd, stderr_fd)
        if os.name == "posix":
            return _posix_terminal_is_secure(stdin_fd, stderr_fd)
        return False
    except (Exception, KeyboardInterrupt):
        # Console/library exceptions may contain private data. No exception
        # object or text is propagated, printed, chained or retained.
        return False


def read_api_key() -> str:
    """Return a validated Key, or fail without plaintext fallback or echo.

    Non-TTY CI, redirected/intercepted streams and unsupported consoles are
    refused before getpass is called. GetPassWarning is promoted to an error;
    Python's fallback_getpass emits it *before* its ordinary input read.
    """
    if not _terminal_is_secure():
        raise UnsafeKeyInputError("terminal_unavailable")
    key = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass(_PROMPT, stream=sys.stderr)
    except (Exception, KeyboardInterrupt):
        # Raise outside the handler so the safe exception has no underlying
        # credential-bearing __context__, even for EOF/cancellation/warnings.
        pass
    if key is None:
        raise UnsafeKeyInputError("secure_input_failed")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{12,256}", key):
        key = None
        raise UnsafeKeyInputError("invalid_key")
    return key
