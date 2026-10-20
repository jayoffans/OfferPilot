"""Reproducible, loopback-only portfolio environment. No paid provider calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def write_private(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    path.chmod(0o600)


def run(command: list[str], *, env: dict | None = None, cwd: Path = ROOT) -> None:
    # Never print command arguments: future commands may carry private URLs.
    subprocess.run(command, cwd=cwd, env=env, check=True)


def runtime_environment(config: dict, *, migration: bool = False) -> dict[str, str]:
    env = dict(os.environ)
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


def migrate(config: dict) -> None:
    run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
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


def initialize(directory: Path, database: str) -> dict:
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
    migrate(config)
    # Only this just-created environment may initialize an empty ledger.
    # Existing profiles never re-create a missing deletion registry.
    from offerpilot_api.account_lifecycle.ledger import DeletionLedger
    DeletionLedger(Path(config["deletion_ledger_dir"])).initialize()
    config["initialized"] = True
    write_private(path, config)
    print(f"Initialized {database} environment; private state: {directory}")
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="demo", help="Local environment name; defaults to demo")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize_parser = commands.add_parser("init", help="Create a fresh local database and migrate it")
    initialize_parser.add_argument("--database", choices=("sqlite", "postgres"), default="sqlite")
    for command in ("migrate", "seed", "api", "web", "verify", "status"):
        commands.add_parser(command)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.profile):
        parser.error("Profile must be a lowercase local name (1-40 characters)")
    directory = (ROOT / ".local" / args.profile).resolve()
    if not directory.is_relative_to((ROOT / ".local").resolve()):
        parser.error("Invalid local profile path")
    try:
        if args.command == "init":
            initialize(directory, args.database)
            return 0
        path = directory / "config.json"
        if not path.exists():
            raise ValueError("Run init first; see docs/LOCAL_SETUP.md")
        config = json.loads(path.read_text(encoding="utf-8"))
        if not config.get("initialized"):
            raise ValueError("Initialization is incomplete; rerun init with the same database choice")
        env = runtime_environment(config)
        if args.command == "migrate":
            migrate(config)
        elif args.command == "api":
            run([sys.executable, "-m", "uvicorn", "offerpilot_api.main:app", "--host", "127.0.0.1",
                 "--port", "8000", "--workers", "1", "--no-access-log"], env=env)
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
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("Local command failed. Check dependencies, selected profile and docs/LOCAL_SETUP.md; existing data preserved.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
