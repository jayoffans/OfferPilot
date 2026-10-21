"""Free checks for launcher isolation and process ownership; no product data."""

from contextlib import redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.local import manage


class LocalLauncherTests(unittest.TestCase):
    def test_busy_port_is_refused_without_stopping_its_owner(self):
        with socket.socket() as owner:
            owner.bind(("127.0.0.1", 0))
            owner.listen()
            port = owner.getsockname()[1]
            with self.assertRaisesRegex(ValueError, f"Port {port} is already in use"):
                manage.require_free_ports((port,))
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                accepted, _ = owner.accept()
                accepted.close()

    def test_runtime_never_inherits_provider_or_developer_settings(self):
        with patch.dict(os.environ, {"OFFERPILOT_DATABASE_URL": "developer-db",
                                     "OFFERPILOT_AUTH_LOGIN_ATTEMPT_LIMIT": "invalid",
                                     "OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED": "true",
                                     "DEEPSEEK_API_KEY": "synthetic-secret",
                                     "OPENAI_API_KEY": "synthetic-secret",
                                     "NODE_OPTIONS": "--inspect=0.0.0.0",
                                     "PYTHONPATH": "developer-hooks"}):
            env = manage.demo_environment()
            self.assertNotIn("OFFERPILOT_DATABASE_URL", env)
            self.assertNotIn("DEEPSEEK_API_KEY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("NODE_OPTIONS", env)
            self.assertNotIn("PYTHONPATH", env)
            runtime = manage.runtime_environment({"database_url": "sqlite:///isolated.db",
                                                 "upload_dir": "uploads", "export_dir": "exports",
                                                 "deletion_ledger_dir": "ledger"}, base_env=env)
            self.assertEqual(runtime["OFFERPILOT_DATABASE_URL"], "sqlite:///isolated.db")
            self.assertEqual(runtime["OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED"], "false")
            self.assertNotIn("OFFERPILOT_AUTH_LOGIN_ATTEMPT_LIMIT", runtime)

    def test_profile_pointing_outside_demo_is_refused_without_writes(self):
        with tempfile.TemporaryDirectory(prefix="OfferPilot 中文 空格 ") as temporary:
            directory = Path(temporary)
            path = directory / "config.json"
            original = json.dumps({"database": "sqlite", "database_url": "sqlite:///developer.db"})
            path.write_text(original, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "choose a new --profile"):
                manage.validate_demo_profile(directory)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_existing_init_default_profile_is_preserved(self):
        with patch.object(sys, "argv", ["manage.py", "init"]), patch.object(manage, "initialize") as initialize:
            self.assertEqual(manage.main(), 0)
            initialize.assert_called_once_with((manage.ROOT / ".local/demo").resolve(), "sqlite", isolated=False)

    def test_demo_defaults_to_separate_profile(self):
        with patch.object(sys, "argv", ["manage.py", "demo", "--no-browser", "--skip-install"]), \
                patch.object(manage, "demo") as demo:
            self.assertEqual(manage.main(), 0)
            demo.assert_called_once_with((manage.ROOT / ".local/windows-demo").resolve(), "windows-demo",
                                         install=False, browser=False, timeout=90)

    def test_traversal_profile_is_refused_before_any_setup(self):
        with patch.object(sys, "argv", ["manage.py", "demo", "--profile", "../developer"]), \
                patch.object(manage, "demo") as demo, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as caught:
                manage.main()
            self.assertEqual(caught.exception.code, 2)
            demo.assert_not_called()

    def test_failed_health_check_is_bounded_and_detects_process_exit(self):
        class Stopped:
            def poll(self):
                return 1

        with self.assertRaisesRegex(ValueError, "exited before becoming ready"):
            manage.wait_for_service(Stopped(), "http://127.0.0.1:1/health", 10)

    def test_health_timeout_does_not_consult_environment_proxy(self):
        class Alive:
            def poll(self):
                return None

        class Offline:
            def open(self, *args, **kwargs):
                raise OSError("offline")

        with patch.object(manage, "build_opener", return_value=Offline()), \
                patch.object(manage.time, "monotonic", side_effect=[0, 0.2, 2]), \
                patch.object(manage.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "timed out after 1s"):
                manage.wait_for_service(Alive(), "http://127.0.0.1:1/health", 1)

    def demo_profile(self, root):
        directory = root / ".local/windows-demo"
        directory.mkdir(parents=True)
        config = {"version": 1, "database": "sqlite", "initialized": True,
                  "database_url": "sqlite:///" + (directory / "demo.db").as_posix(),
                  "migration_url": "sqlite:///" + (directory / "demo.db").as_posix(),
                  "upload_dir": str(directory / "uploads"), "export_dir": str(directory / "exports"),
                  "deletion_ledger_dir": str(directory / "deletion-ledger")}
        (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
        next_binary = root / "frontend/node_modules/next/dist/bin/next"
        next_binary.parent.mkdir(parents=True)
        next_binary.touch()
        return directory

    def test_existing_demo_does_not_read_credentials_or_reseed_user_edits(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory(prefix="OfferPilot 中文 空格 ") as temporary:
            root = Path(temporary)
            directory = self.demo_profile(root)
            (directory / "seed.json").write_text("{}", encoding="utf-8")
            preserved = directory / "demo.db"
            preserved.write_bytes(b"synthetic user edits")
            processes = Mock()
            processes.run.return_value = b"v22.16.0"
            with patch.object(manage, "ROOT", root), patch.object(manage, "require_free_ports"), \
                    patch.object(manage.shutil, "which", return_value="installed-tool"), \
                    patch.object(manage, "LocalProcesses", return_value=processes), \
                    patch.object(manage, "wait_for_service", side_effect=ValueError("stop-test")), \
                    patch("builtins.print"):
                with self.assertRaisesRegex(ValueError, "stop-test"):
                    manage.demo(directory, "windows-demo", install=False, browser=False, timeout=10)
            self.assertEqual(preserved.read_bytes(), b"synthetic user edits")
            self.assertFalse((directory / "demo-credentials.json").exists())
            self.assertFalse(any("seed" in call.args[0] for call in processes.run.call_args_list))
            processes.close.assert_called_once()

    def test_failed_initialization_never_seeds_or_starts_services(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory(prefix="OfferPilot 中文 空格 ") as temporary:
            root = Path(temporary)
            directory = self.demo_profile(root)
            original = (directory / "config.json").read_bytes()
            processes = Mock()
            processes.run.side_effect = [b"v22.16.0", ValueError("initialization interrupted")]
            with patch.object(manage, "ROOT", root), patch.object(manage, "require_free_ports"), \
                    patch.object(manage.shutil, "which", return_value="installed-tool"), \
                    patch.object(manage, "LocalProcesses", return_value=processes):
                with self.assertRaisesRegex(ValueError, "initialization interrupted"):
                    manage.demo(directory, "windows-demo", install=False, browser=False, timeout=10)
            self.assertEqual((directory / "config.json").read_bytes(), original)
            processes.start.assert_not_called()
            processes.close.assert_called_once()

    def test_cleanup_kills_owned_descendants_and_preserves_unrelated_process(self):
        with tempfile.TemporaryDirectory(prefix="OfferPilot 中文 空格 ") as temporary:
            directory = Path(temporary)
            ready = directory / "port.txt"
            server = ("import pathlib,socket,sys,time; s=socket.socket(); "
                      "s.bind(('127.0.0.1',0)); s.listen(); "
                      "pathlib.Path(sys.argv[1]).write_text(str(s.getsockname()[1])); time.sleep(60)")
            parent = ("import subprocess,sys,time; "
                      "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); time.sleep(60)")
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes = manage.LocalProcesses()
            try:
                child = processes.start([sys.executable, "-c", parent, server, str(ready)],
                                        env=manage.demo_environment(), cwd=directory)
                deadline = time.monotonic() + 10
                while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), "owned grandchild did not start")
                port = int(ready.read_text())
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass
                processes.close()
                self.assertIsNotNone(child.poll())
                self.assertIsNone(unrelated.poll())
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", port), timeout=1)
            finally:
                processes.close()
                unrelated.terminate()
                unrelated.wait(timeout=5)

    @unittest.skipUnless(os.name == "nt", "Windows batch entry point")
    def test_batch_entry_point_works_from_another_directory(self):
        with tempfile.TemporaryDirectory(prefix="OfferPilot 中文 空格 ") as temporary:
            env = dict(os.environ, OFFERPILOT_NO_PAUSE="1")
            command = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /d /s /c ""{manage.ROOT / "start-demo.cmd"}" --help"'
            result = subprocess.run(command,
                                    cwd=temporary, env=env, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(b"windows-demo", result.stdout)
            self.assertIn(b"--skip-install", result.stdout)


if __name__ == "__main__":
    unittest.main()
