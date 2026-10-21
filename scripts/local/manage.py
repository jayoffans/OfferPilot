"""Reproducible, loopback-only portfolio environment. No paid provider calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4
import webbrowser

ROOT = Path(__file__).resolve().parents[2]


def python_module(module: str, *arguments: str, isolated: bool = False) -> list[str]:
    if not isolated:
        return [sys.executable, "-m", module, *arguments]
    # Do this before importing any application singleton. The local demo must
    # not read a developer's backend/.env, even if it contains invalid settings.
    bootstrap = ("import runpy,sys; from offerpilot_api.core.config import Settings; "
                 "Settings.model_config['env_file'] = None; "
                 "runpy.run_module(sys.argv.pop(1), run_name='__main__', alter_sys=True)")
    return [sys.executable, "-c", bootstrap, module, *arguments]


def write_private(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    path.chmod(0o600)


def run(command: list[str], *, env: dict | None = None, cwd: Path = ROOT) -> None:
    # Never print command arguments: future commands may carry private URLs.
    subprocess.run(command, cwd=cwd, env=env, check=True)


def runtime_environment(config: dict, *, migration: bool = False,
                        base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.update({
        "OFFERPILOT_ENVIRONMENT": "development",
        "OFFERPILOT_SESSION_COOKIE_SECURE": "false",
        "OFFERPILOT_DATABASE_URL": config["migration_url" if migration else "database_url"],
        "OFFERPILOT_UPLOAD_DIR": config["upload_dir"],
        "OFFERPILOT_EXPORT_DIR": config["export_dir"],
        "OFFERPILOT_DELETION_LEDGER_DIR": config["deletion_ledger_dir"],
        "OFFERPILOT_TRUSTED_ORIGINS": json.dumps(["http://127.0.0.1:3000", "http://localhost:3000", "http://testserver"]),
        "OFFERPILOT_MOCK_EXTRACTION_ENABLED": "true",
        "OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED": "false",
        "OFFERPILOT_API_URL": "http://127.0.0.1:8000",
        "OFFERPILOT_WEB_ORIGINS": json.dumps(["http://127.0.0.1:3000", "http://localhost:3000"]),
        "OFFERPILOT_MOCK_EXTRACTION_UI": "true",
        "NEXT_TELEMETRY_DISABLED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def postgres_credentials() -> dict[str, str]:
    path = ROOT / ".env.s5-postgres"
    if not path.exists():
        # Compatible with the existing PowerShell starter; never rotate an
        # existing Docker volume's credentials or overwrite its private file.
        with path.open("x", encoding="ascii") as stream:
            for key in ("S5_POSTGRES_ADMIN_PASSWORD", "S5_MIGRATOR_PASSWORD", "S5_APP_PASSWORD"):
                stream.write(f"{key}={secrets.token_hex(24)}\n")
        path.chmod(0o600)
    values = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8-sig").splitlines()
                  if line and not line.startswith("#") and "=" in line)
    for key in ("S5_POSTGRES_ADMIN_PASSWORD", "S5_MIGRATOR_PASSWORD", "S5_APP_PASSWORD"):
        if not values.get(key):
            raise ValueError("Private PostgreSQL credentials are incomplete; preserve the existing volume and file")
    return values


def create_postgres(config: dict) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL
    values = postgres_credentials()
    run(["docker", "compose", "--env-file", str(ROOT / ".env.s5-postgres"),
         "-f", str(ROOT / "compose.postgres.yml"), "up", "-d", "--wait", "--wait-timeout", "90"])
    port = int(values.get("S5_POSTGRES_PORT", "55432"))
    name = "offerpilot_portfolio_" + uuid4().hex[:16]
    migrator = URL.create("postgresql+psycopg", username="s5_migrator", password=values["S5_MIGRATOR_PASSWORD"],
                          host="127.0.0.1", port=port, database="postgres")
    engine = create_engine(migrator, isolation_level="AUTOCOMMIT", hide_parameters=True)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            connection.execute(text(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC'))
            connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO s5_app'))
    finally:
        engine.dispose()
    config["migration_url"] = migrator.set(database=name).render_as_string(hide_password=False)
    config["database_url"] = migrator.set(database=name, username="s5_app", password=values["S5_APP_PASSWORD"]).render_as_string(hide_password=False)


def migrate(config: dict, *, isolated: bool = False) -> None:
    run(python_module("alembic", "-c", "backend/alembic.ini", "upgrade", "head", isolated=isolated),
        env=runtime_environment(config, migration=True))
    if config["database"] == "postgres":
        from sqlalchemy import create_engine, text
        engine = create_engine(config["migration_url"], hide_parameters=True)
        try:
            with engine.begin() as connection:
                for statement in (
                    "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
                    "GRANT USAGE ON SCHEMA public TO s5_app",
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO s5_app",
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO s5_app",
                    "REVOKE INSERT, UPDATE, DELETE ON TABLE alembic_version FROM s5_app",
                ):
                    connection.execute(text(statement))
        finally:
            engine.dispose()


def initialize(directory: Path, database: str, *, isolated: bool = False) -> dict:
    path = directory / "config.json"
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
        if config["database"] != database:
            raise ValueError("This profile uses a different database; choose a new --profile")
        if config.get("initialized"):
            print("Existing environment preserved. Use migrate for a later forward upgrade.")
            return config
    else:
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("Refusing to initialize a nonempty directory without its environment manifest")
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        config = {"version": 1, "database": database, "initialized": False,
                  "upload_dir": str(directory / "uploads"), "export_dir": str(directory / "exports"),
                  "deletion_ledger_dir": str(directory / "deletion-ledger")}
        if database == "postgres":
            create_postgres(config)
        else:
            config["database_url"] = config["migration_url"] = "sqlite:///" + (directory / "demo.db").as_posix()
        write_private(path, config)
    migrate(config, isolated=isolated)
    # Only this just-created environment may initialize an empty ledger.
    # Existing profiles never re-create a missing deletion registry.
    from offerpilot_api.account_lifecycle.ledger import DeletionLedger
    DeletionLedger(Path(config["deletion_ledger_dir"])).initialize()
    config["initialized"] = True
    write_private(path, config)
    print(f"Initialized {database} environment; private state: {directory}")
    return config


class LocalProcesses:
    """Own only this invocation's children, including Next's server child."""

    def __init__(self):
        self.children: list[subprocess.Popen] = []
        self.job = None
        if os.name == "nt":
            # A Windows job kills descendants on close (also on launcher crash),
            # without PID/name-based termination that could hit another service.
            import ctypes
            from ctypes import wintypes

            class BasicLimits(ctypes.Structure):
                _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                            ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                            ("maximum", ctypes.c_size_t), ("active", wintypes.DWORD),
                            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                            ("scheduling", wintypes.DWORD)]

            class ExtendedLimits(ctypes.Structure):
                _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                            ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                           ctypes.c_void_p, wintypes.DWORD]
            self.kernel.SetInformationJobObject.restype = wintypes.BOOL
            self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.job = self.kernel.CreateJobObjectW(None, None)
            if not self.job:
                raise ValueError("Cannot create an isolated Windows process job; no services started")
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self.close()
                raise ValueError("Cannot configure Windows process cleanup; no services started")

    def start(self, command: list[str], *, env: dict, cwd: Path = ROOT,
              capture: bool = False) -> subprocess.Popen:
        options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                   else {"start_new_session": True})
        child = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE if capture else None,
                                 stderr=subprocess.PIPE if capture else None, **options)
        self.children.append(child)
        if self.job and not self.kernel.AssignProcessToJobObject(self.job, int(child._handle)):
            child.terminate()
            child.wait(timeout=5)
            raise ValueError("Cannot isolate a child process; services stopped and existing data preserved")
        return child

    def run(self, command: list[str], *, env: dict, cwd: Path = ROOT,
            timeout: int = 600, capture: bool = False) -> bytes:
        child = self.start(command, env=env, cwd=cwd, capture=capture)
        try:
            output, _ = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ValueError("Local setup exceeded its time limit; existing data preserved") from None
        if child.returncode:
            raise ValueError("Local setup command failed; check the output above and docs/LOCAL_SETUP.md")
        return output or b""

    def close(self):
        # Close the Windows job before waiting: descendants are included even
        # when their direct parent has already exited. No external PID is used.
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
        elif os.name != "nt":
            for child in self.children:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for child in self.children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    child.kill()
                child.wait(timeout=5)


def require_free_ports(ports: tuple[int, ...] = (8000, 3000)) -> None:
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise ValueError(f"Port {port} is already in use; stop its owner first. No existing service was reused or stopped.") from None


def demo_environment() -> dict[str, str]:
    # Preserve normal OS/tool discovery, but never inherit developer runtime
    # settings, provider secrets, Node injection flags, or Python startup hooks.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("OFFERPILOT_", "NEXT_", "__NEXT_", "OPENAI_", "DEEPSEEK_"))
           and key.upper() not in {"NODE_OPTIONS", "PYTHONPATH", "PYTHONSTARTUP", "UV_ENV_FILE"}}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "NEXT_TELEMETRY_DISABLED": "1", "NODE_ENV": "development",
                "UV_NO_ENV_FILE": "true", "OFFERPILOT_DEEPSEEK_EXTRACTION_ENABLED": "false"})
    return env


def validate_demo_profile(directory: Path) -> None:
    path = directory / "config.json"
    if not path.exists():
        return  # initialize() refuses nonempty directories without a manifest.
    config = json.loads(path.read_text(encoding="utf-8"))
    expected_url = "sqlite:///" + (directory / "demo.db").as_posix()
    expected = {"version": 1, "database": "sqlite", "database_url": expected_url,
                "migration_url": expected_url, "upload_dir": str(directory / "uploads"),
                "export_dir": str(directory / "exports"),
                "deletion_ledger_dir": str(directory / "deletion-ledger")}
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("This profile is not an isolated SQLite demo; choose a new --profile. Existing data preserved.")


def wait_for_service(child: subprocess.Popen, url: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    opener = build_opener(ProxyHandler({}))  # Never send loopback health checks through a proxy.
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise ValueError("A local service exited before becoming ready; check its output above")
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200 and child.poll() is None:
                    return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.2)
    raise ValueError(f"Local service health check timed out after {timeout}s; existing data preserved")


def demo(directory: Path, profile: str, *, install: bool, browser: bool, timeout: int) -> None:
    require_free_ports()
    validate_demo_profile(directory)
    uv = shutil.which("uv")
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not uv or not node or not npm:
        raise ValueError("Install uv, Python 3.11 and supported Node.js/npm first; see docs/LOCAL_SETUP.md")
    if sys.version_info[:2] != (3, 11):
        raise ValueError("Use Python 3.11: uv run --project backend --locked python scripts/local/manage.py demo")
    if (ROOT / "frontend/.next/dev/lock").exists():
        raise ValueError("The frontend development lock exists; stop the existing frontend before starting the demo")
    env = demo_environment()
    processes = LocalProcesses()
    try:
        version = processes.run([node, "--version"], env=env, timeout=10, capture=True).decode().strip()
        match = re.fullmatch(r"v(\d+)\.(\d+)\.\d+", version)
        if not match or not (int(match[1]) >= 24 or int(match[1]) == 22 and int(match[2]) >= 12):
            raise ValueError("Supported Node.js: >=22.12.0 <23 or >=24; see frontend/package.json")
        if install:
            print("Installing the existing locked Python and npm dependencies (first run may take several minutes).", flush=True)
            processes.run([uv, "sync", "--project", "backend", "--locked"], env=env)
            processes.run([npm, "ci"], env=env, cwd=ROOT / "frontend")
        if not (ROOT / "frontend/node_modules/next/dist/bin/next").is_file():
            raise ValueError("Frontend dependencies are missing; rerun without --skip-install")
        command = [sys.executable, str(Path(__file__).resolve()), "--_isolated-demo", "--profile", profile]
        processes.run([*command, "init", "--database", "sqlite"], env=env, timeout=120)
        validate_demo_profile(directory)
        if not (directory / "seed.json").exists():
            processes.run([*command, "seed"], env=env, timeout=120)
        else:
            print("Existing demo preserved; seed data and your later edits were not changed.", flush=True)
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        runtime = runtime_environment(config, base_env=env)
        env.update({key: value for key, value in runtime.items()
                    if key.startswith("OFFERPILOT_") or key in {"NEXT_TELEMETRY_DISABLED", "PYTHONIOENCODING"}})
        # Next gives explicit process variables precedence over frontend/.env*.
        # Cover every runtime setting used by the current frontend.
        env["OFFERPILOT_EXPORT_MAX_BYTES"] = "67108864"
        require_free_ports()
        api = processes.start([*command, "api"], env=env)
        wait_for_service(api, "http://127.0.0.1:8000/health", timeout)
        web = processes.start([node, str(ROOT / "frontend/node_modules/next/dist/bin/next"),
                               "dev", "--hostname", "127.0.0.1", "--port", "3000"],
                              env=env, cwd=ROOT / "frontend")
        wait_for_service(web, "http://127.0.0.1:3000/login", timeout)
        print("Demo ready: http://127.0.0.1:3000 (synthetic data; Mock only; paid calls disabled).", flush=True)
        print(f"Open the private login file locally: {directory / 'demo-credentials.json'}", flush=True)
        print("Press Ctrl+C to stop only these services. Your demo data will be preserved.", flush=True)
        if browser:
            webbrowser.open("http://127.0.0.1:3000")
        while True:
            if api.poll() is not None or web.poll() is not None:
                raise ValueError("A local service stopped unexpectedly; the other service was stopped and data preserved")
            time.sleep(0.5)
    finally:
        processes.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="Local environment name; defaults to demo (windows-demo for demo command)")
    parser.add_argument("--_isolated-demo", action="store_true", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize_parser = commands.add_parser("init", help="Create a fresh local database and migrate it")
    initialize_parser.add_argument("--database", choices=("sqlite", "postgres"), default="sqlite")
    for command in ("migrate", "seed", "api", "web", "verify", "status"):
        commands.add_parser(command)
    demo_parser = commands.add_parser("demo", help="Install locked dependencies and run an isolated SQLite demo")
    demo_parser.add_argument("--profile", dest="demo_profile", help="Use a separate local demo name (default: windows-demo)")
    demo_parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    demo_parser.add_argument("--skip-install", action="store_true", help="Reuse dependencies already installed from the lockfiles")
    demo_parser.add_argument("--startup-timeout", type=int, choices=range(10, 181), metavar="10..180", default=90)
    args = parser.parse_args()
    args.profile = getattr(args, "demo_profile", None) or args.profile or ("windows-demo" if args.command == "demo" else "demo")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.profile):
        parser.error("Profile must be a lowercase local name (1-40 characters)")
    directory = (ROOT / ".local" / args.profile).resolve()
    if not directory.is_relative_to((ROOT / ".local").resolve()):
        parser.error("Invalid local profile path")
    try:
        if args._isolated_demo:
            from offerpilot_api.core.config import Settings
            Settings.model_config["env_file"] = None
        if args.command == "demo":
            demo(directory, args.profile, install=not args.skip_install,
                 browser=not args.no_browser, timeout=args.startup_timeout)
            return 0
        if args.command == "init":
            initialize(directory, args.database, isolated=args._isolated_demo)
            return 0
        path = directory / "config.json"
        if not path.exists():
            raise ValueError("Run init first; see docs/LOCAL_SETUP.md")
        config = json.loads(path.read_text(encoding="utf-8"))
        if not config.get("initialized"):
            raise ValueError("Initialization is incomplete; rerun init with the same database choice")
        env = runtime_environment(config)
        if args.command == "migrate":
            migrate(config, isolated=args._isolated_demo)
        elif args.command == "api":
            run(python_module("uvicorn", "offerpilot_api.main:app", "--host", "127.0.0.1",
                              "--port", "8000", "--workers", "1", "--no-access-log", isolated=args._isolated_demo), env=env)
        elif args.command == "web":
            npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
            if not npm:
                raise ValueError("Install the supported Node.js version and run npm ci in frontend first")
            run([npm, "run", "dev"], env=env, cwd=ROOT / "frontend")
        else:
            # Configure before importing application singletons. This process
            # uses only the chosen private profile, never backend/.env's DB.
            os.environ.update(env)
            from demo_seed import seed, verify
            if args.command == "seed":
                seed(directory)
            else:
                verify(directory)
                print(f"Local login details (private): {directory / 'demo-credentials.json'}")
        return 0
    except KeyboardInterrupt:
        return 130
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, subprocess.CalledProcessError):
        print("Local command failed. Check dependencies, selected profile and docs/LOCAL_SETUP.md; existing data preserved.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
