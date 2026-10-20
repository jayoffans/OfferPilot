"""Free credential-input checks; only fixed synthetic secrets are used."""

import getpass
import os
import subprocess
import sys
import traceback
import unittest
import warnings
from contextlib import ExitStack
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.evaluations import secure_key_input as secure


KEY = "synthetic-evaluation-key-123456"


class SecureKeyInputTests(unittest.TestCase):
    def terminal(self, *, platform="nt", stdin_tty=True, stderr_tty=True):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stdin = Mock(isatty=Mock(return_value=stdin_tty), fileno=Mock(return_value=0))
        stderr = Mock(isatty=Mock(return_value=stderr_tty), fileno=Mock(return_value=2))
        for name, value in (("stdin", stdin), ("__stdin__", stdin),
                            ("stderr", stderr), ("__stderr__", stderr)):
            stack.enter_context(patch.object(secure.sys, name, value))
        stack.enter_context(patch.object(secure.os, "name", platform))
        stack.enter_context(patch.object(secure.os, "isatty", return_value=True))
        windows = stack.enter_context(patch.object(secure, "_windows_console_is_secure", return_value=True))
        posix = stack.enter_context(patch.object(secure, "_posix_terminal_is_secure", return_value=True))
        return stdin, stderr, windows, posix

    def assert_safe_error(self, code, getter):
        with self.assertRaises(secure.UnsafeKeyInputError) as caught:
            secure.read_api_key()
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        public_text = "".join(traceback.format_exception(error)) + repr(error)
        self.assertNotIn(KEY, public_text)
        return error

    def test_valid_hidden_input_windows_and_posix(self):
        for platform in ("nt", "posix"):
            with self.subTest(platform=platform):
                _, stderr, windows, posix = self.terminal(platform=platform)
                with patch.object(secure.getpass, "getpass", return_value=KEY) as getter:
                    self.assertEqual(secure.read_api_key(), KEY)
                getter.assert_called_once_with(secure._PROMPT, stream=stderr)
                (windows if platform == "nt" else posix).assert_called_once_with(0, 2)

    def test_no_tty_never_invokes_getpass(self):
        for stdin_tty, stderr_tty in ((False, True), (True, False), (False, False)):
            with self.subTest(stdin_tty=stdin_tty, stderr_tty=stderr_tty):
                self.terminal(stdin_tty=stdin_tty, stderr_tty=stderr_tty)
                with patch.object(secure.getpass, "getpass") as getter:
                    self.assert_safe_error("terminal_unavailable", getter)
                getter.assert_not_called()

    def test_intercepted_stream_refused_even_when_isatty_returns_true(self):
        for name in ("stdin", "stderr"):
            with self.subTest(name=name):
                self.terminal()
                intercepted = Mock(isatty=Mock(return_value=True), fileno=Mock(return_value=0 if name == "stdin" else 2))
                with patch.object(secure.sys, name, intercepted), patch.object(secure.getpass, "getpass") as getter:
                    self.assert_safe_error("terminal_unavailable", getter)
                getter.assert_not_called()

    def test_redirected_descriptor_or_os_tty_refused(self):
        for reason in ("wrong_fd", "os_tty", "missing_stream"):
            with self.subTest(reason=reason):
                stdin, _, _, _ = self.terminal()
                if reason == "wrong_fd":
                    stdin.fileno.return_value = 7
                elif reason == "os_tty":
                    secure.os.isatty.return_value = False
                else:
                    sys.stdin = None
                with patch.object(secure.getpass, "getpass") as getter:
                    self.assert_safe_error("terminal_unavailable", getter)
                getter.assert_not_called()

    def test_console_verification_failure_or_error_refused(self):
        for failure in (False, OSError(KEY), KeyboardInterrupt(KEY)):
            with self.subTest(failure_type=type(failure).__name__):
                _, _, windows, _ = self.terminal()
                if failure is False:
                    windows.return_value = False
                else:
                    windows.side_effect = failure
                with patch.object(secure.getpass, "getpass") as getter:
                    self.assert_safe_error("terminal_unavailable", getter)
                getter.assert_not_called()

    def test_terminal_method_error_is_safe_and_does_not_prompt(self):
        stdin, _, _, _ = self.terminal()
        stdin.isatty.side_effect = OSError(KEY)
        with patch.object(secure.getpass, "getpass") as getter:
            self.assert_safe_error("terminal_unavailable", getter)
        getter.assert_not_called()

    def test_unknown_platform_refused(self):
        self.terminal(platform="unsupported")
        with patch.object(secure.getpass, "getpass") as getter:
            self.assert_safe_error("terminal_unavailable", getter)
        getter.assert_not_called()

    def test_getpass_warning_stops_before_plaintext_fallback_read(self):
        self.terminal()
        # Exercise the stdlib fallback itself. Its warning must stop execution
        # before it prints a warning or reads one byte from ordinary stdin.
        output = StringIO()
        with patch.object(secure.sys, "stderr", output), patch.object(secure, "_terminal_is_secure", return_value=True):
            with patch.object(secure.getpass, "getpass", side_effect=getpass.fallback_getpass) as getter:
                with patch.object(getpass, "_raw_input") as ordinary_input:
                    with warnings.catch_warnings(record=True) as emitted:
                        warnings.simplefilter("ignore", getpass.GetPassWarning)
                        self.assert_safe_error("secure_input_failed", getter)
                    self.assertEqual(emitted, [])
        ordinary_input.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    def test_eof_cancel_and_getpass_errors_have_no_secret_context_or_output(self):
        for failure in (EOFError(KEY), KeyboardInterrupt(KEY), OSError(KEY), ValueError(KEY)):
            with self.subTest(failure_type=type(failure).__name__):
                _, stderr, _, _ = self.terminal()
                with patch.object(secure.getpass, "getpass", side_effect=failure) as getter:
                    self.assert_safe_error("secure_input_failed", getter)
                stderr.write.assert_not_called()

    def test_invalid_secret_not_echoed_or_trimmed(self):
        for key in ("", "short", " " + KEY, KEY + " ", KEY + "\n", KEY + "@", KEY * 20, 42):
            with self.subTest(kind=type(key).__name__, length=len(key) if isinstance(key, str) else None):
                _, stderr, _, _ = self.terminal()
                with patch.object(secure.getpass, "getpass", return_value=key) as getter:
                    self.assert_safe_error("invalid_key", getter)
                stderr.write.assert_not_called()

    def test_environment_credentials_are_never_a_fallback(self):
        self.terminal(stdin_tty=False)
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": KEY, "OPENAI_API_KEY": KEY}):
            with patch.object(secure.getpass, "getpass") as getter:
                self.assert_safe_error("terminal_unavailable", getter)
        getter.assert_not_called()

    def test_actual_non_tty_subprocess_refuses_before_any_secret_read(self):
        process = subprocess.run(
            [sys.executable, "-B", "-c",
             "from backend.evaluations.secure_key_input import read_api_key, UnsafeKeyInputError\n"
             "try:\n    read_api_key()\n"
             "except UnsafeKeyInputError as error:\n    print(error.code)\n"
             "else:\n    raise AssertionError('Non-TTY input must be refused')\n"],
            input="", capture_output=True, text=True, timeout=10,
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DEEPSEEK_API_KEY": KEY, "OPENAI_API_KEY": KEY},
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stdout.strip(), "terminal_unavailable")
        self.assertEqual(process.stderr, "")
        self.assertNotIn(KEY, process.stdout + process.stderr)

    def test_windows_console_checks_native_handle_identity_and_mode(self):
        import ctypes

        native = secure._windows_console_is_secure
        for failure in (None, "input_mismatch", "stderr_mismatch", "input_not_console", "stderr_not_console", "invalid_handle"):
            with self.subTest(failure=failure):
                kernel = SimpleNamespace(GetStdHandle=Mock(), GetConsoleMode=Mock())
                handles = [100, 200]
                if failure == "invalid_handle":
                    handles[0] = ctypes.c_void_p(-1).value
                kernel.GetStdHandle.side_effect = handles
                kernel.GetConsoleMode.side_effect = [0 if failure == "input_not_console" else 1,
                                                      0 if failure == "stderr_not_console" else 1]
                crt = SimpleNamespace(get_osfhandle=Mock(side_effect=[101 if failure == "input_mismatch" else 100,
                                                                   201 if failure == "stderr_mismatch" else 200]))
                with patch.dict(sys.modules, {"msvcrt": crt}), patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
                    self.assertEqual(native(0, 2), failure is None)

    def test_posix_console_requires_working_termios_control(self):
        native = secure._posix_terminal_is_secure
        for reason in (None, "unsupported", "bad_attributes", "error"):
            with self.subTest(reason=reason):
                termios = SimpleNamespace(tcgetattr=Mock(return_value=[0, 0, 0, 8, 0, 0, []]),
                                          tcsetattr=Mock(), ECHO=8, TCSAFLUSH=2)
                if reason == "unsupported":
                    termios.tcsetattr = None
                elif reason == "bad_attributes":
                    termios.tcgetattr.return_value = []
                elif reason == "error":
                    termios.tcgetattr.side_effect = OSError(KEY)
                with patch.dict(sys.modules, {"termios": termios}):
                    if reason == "error":
                        with self.assertRaises(OSError):
                            native(0, 2)
                    else:
                        self.assertEqual(native(0, 2), reason is None)


if __name__ == "__main__":
    unittest.main()
