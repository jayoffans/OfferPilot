"""pg_dump/pg_restore rehearsal into a newly created isolated target database.

Called by quality.py --backup while its synthetic source database still exists.
Credentials are passed only through child-process environment, never argv/logs.
Only fresh databases from tests/postgres/support.py are restore targets.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "tests" / "postgres"))


def require(condition, check):
    if not condition:
        raise RuntimeError(f"PostgreSQL backup check failed: {check}")


def secure_runtime_role(database):
    """Restore --no-acl requires reapplying the reviewed application grants."""
    from offerpilot_api.db.session import build_engine

    runtime_role = make_url(database.database_url).username
    migration_engine = build_engine(database.migration_url)
    try:
        quoted = migration_engine.dialect.identifier_preparer.quote(runtime_role)
        with migration_engine.begin() as connection:
            connection.execute(text(f"REVOKE INSERT, UPDATE, DELETE ON TABLE alembic_version FROM {quoted}"))
    finally:
        migration_engine.dispose()
    with database.engine.connect() as connection:
        privileges = dict(connection.execute(text("""SELECT
            has_table_privilege(current_user, 'alembic_version', 'SELECT') AS migration_version_read,
            has_table_privilege(current_user, 'alembic_version', 'INSERT,UPDATE,DELETE') AS migration_version_write,
            has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
            rolsuper AS superuser, rolcreatedb AS create_database, rolcreaterole AS create_role
            FROM pg_roles WHERE rolname = current_user""")).mappings().one())
        require(privileges["migration_version_read"] and not any(value for key, value in privileges.items()
            if key != "migration_version_read"), "least-privilege runtime role metadata")
        connection.rollback()
        for label, statement in (
            ("migration_version_write_denied", "UPDATE alembic_version SET version_num = version_num WHERE false"),
            ("schema_create_denied", "CREATE TABLE s5_runtime_permission_probe (id integer)"),
        ):
            try:
                connection.execute(text(statement))
            except DBAPIError as error:
                connection.rollback()
                require(getattr(error.orig, "sqlstate", None) == "42501", "expected PostgreSQL insufficient_privilege")
                privileges[label] = True
            else:
                connection.rollback()
                raise RuntimeError("Runtime role unexpectedly gained DDL or migration-write permission")
        connection.execute(text("UPDATE users SET is_active = is_active WHERE false"))
        connection.rollback()
        privileges["business_dml_allowed"] = True
    return privileges


def fingerprint(engine):
    """Report counts/digests only. No row values leave the isolated process."""
    result = {}
    with engine.connect() as connection:
        for name in sorted(inspect(engine).get_table_names()):
            quoted = engine.dialect.identifier_preparer.quote(name)
            rows = connection.execute(text(f"SELECT to_jsonb(t)::text FROM {quoted} AS t")).scalars().all()
            require(len(rows) <= 100000, "bounded synthetic fingerprint size")
            canonical = sorted(json.dumps(json.loads(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows)
            digest = hashlib.sha256()
            for row in canonical:
                digest.update(row.encode("utf-8"))
                digest.update(b"\n")
            result[name] = {"count": len(rows), "sha256": digest.hexdigest()}
    return result


def schema_fingerprint(engine):
    inspector = inspect(engine)
    result = {}
    for table in sorted(inspector.get_table_names()):
        def normalized(values):
            return sorted(values, key=lambda value: json.dumps(value, sort_keys=True, default=str))
        checks = inspector.get_check_constraints(table)
        # PostgreSQL 18 pg_dump reparses an ARRAY[varchar literals]::text[]
        # expression as ARRAY[varchar literals individually cast to text].
        # Normalize only this exact equivalent literal-array cast expansion;
        # names, operators, literals, other casts and every other property stay
        # part of the strict comparison.
        pattern = r"ARRAY\[((?:'(?:[^']|'')*'::character varying(?:,\s*)?)+)\]::text\[\]"
        for check in checks:
            check["sqltext"] = re.sub(pattern, lambda match: "ARRAY[" +
                match.group(1).replace("::character varying", "::character varying::text") + "]", check["sqltext"])
        result[table] = {
            "columns": [(column["name"], str(column["type"]), column["nullable"], column.get("default")) for column in inspector.get_columns(table)],
            "foreign_keys": normalized(inspector.get_foreign_keys(table)),
            "unique_constraints": normalized(inspector.get_unique_constraints(table)),
            "check_constraints": normalized(checks),
            "indexes": normalized(inspector.get_indexes(table)),
            "primary_key": inspector.get_pk_constraint(table),
        }
    return result


def pg_command(tool, url, *, dump_path, restore=False):
    """Use pinned container tools by default, or explicit local PG bin directory."""
    parsed = make_url(url)
    require(parsed.get_backend_name() == "postgresql" and parsed.host in {"127.0.0.1", "localhost"}, "loopback PostgreSQL")
    require((parsed.database or "").startswith("offerpilot_s5_"), "isolated source/target database")
    environment = dict(os.environ)
    environment["PGPASSWORD"] = parsed.password or ""
    environment["PGCONNECT_TIMEOUT"] = "10"
    bin_dir = environment.get("OFFERPILOT_POSTGRES_BIN")
    if bin_dir:
        executable = Path(bin_dir) / (tool + (".exe" if os.name == "nt" else ""))
        require(executable.is_file(), "explicit local PostgreSQL client exists")
        command = [str(executable), "--host", parsed.host, "--port", str(parsed.port or 5432)]
    else:
        container = environment.get("OFFERPILOT_POSTGRES_CONTAINER", "offerpilot-s5-postgres")
        require(container == "offerpilot-s5-postgres", "fixed isolated Docker container")
        command = ["docker", "exec", "-i", "-e", "PGPASSWORD", "-e", "PGCONNECT_TIMEOUT", container, tool,
            "--host", "127.0.0.1", "--port", "5432"]
    command.extend(["--username", parsed.username, "--dbname", parsed.database, "--no-owner", "--no-acl"])
    if restore:
        command.extend(["--exit-on-error", "--single-transaction"])
        with dump_path.open("rb") as source:
            process = subprocess.run(command, env=environment, stdin=source, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        command.append("--format=custom")
        with dump_path.open("xb") as destination:
            process = subprocess.run(command, env=environment, stdout=destination, stderr=subprocess.PIPE,
                timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # Client stderr is intentionally not printed: it can include connection data.
    require(process.returncode == 0, f"{tool} exits successfully")


def rehearse(source_url, storage, output):
    from offerpilot_api.db.session import build_engine
    from support import isolated_database

    output.mkdir(parents=True, exist_ok=False)
    source = build_engine(source_url)
    try:
        expected_rows, expected_schema = fingerprint(source), schema_fingerprint(source)
        dump_path = output / "synthetic-database.dump"
        started = time.perf_counter()
        pg_command("pg_dump", source_url, dump_path=dump_path)
        dump_seconds = time.perf_counter() - started
        require(dump_path.stat().st_size > 0, "nonempty custom-format dump")
        # HTTP has stopped and no synthetic writer remains. This file snapshot
        # therefore shares a quiescent consistency point with pg_dump.
        restored_storage = output / "private-objects"
        shutil.copytree(storage, restored_storage)
        with isolated_database(name_prefix="restore", migrate_head=False) as target:
            require(not inspect(target.engine).get_table_names(), "restore target starts empty")
            started = time.perf_counter()
            pg_command("pg_restore", target.migration_url, dump_path=dump_path, restore=True)
            restore_seconds = time.perf_counter() - started
            privileges = secure_runtime_role(target)
            actual_rows, actual_schema = fingerprint(target.engine), schema_fingerprint(target.engine)
            (output / "schema-source.json").write_text(json.dumps(expected_schema, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            (output / "schema-restored.json").write_text(json.dumps(actual_schema, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            require(actual_rows == expected_rows, "all table row counts and canonical SHA-256 digests")
            require(actual_schema == expected_schema, "columns, constraints and indexes restored")
            with target.engine.connect() as connection:
                revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                objects = connection.execute(text("SELECT object_key, sha256, byte_size, id FROM resume_documents WHERE object_key IS NOT NULL")).all()
            verified_objects = 0
            for object_key, digest, size, resume_id in objects:
                require(isinstance(object_key, str) and Path(object_key).name == object_key, "opaque object key")
                restored = restored_storage / object_key
                require(restored.is_file() and restored.stat().st_size == size, "restored private PDF size")
                require(hashlib.sha256(restored.read_bytes()).hexdigest() == digest, "restored private PDF checksum")
                require(restored.with_name(restored.name + ".claim").read_bytes() == resume_id.hex.encode("ascii"), "restored object ownership claim")
                verified_objects += 1
            report = {"format": "PostgreSQL custom", "dump_bytes": dump_path.stat().st_size,
                "dump_sha256": hashlib.sha256(dump_path.read_bytes()).hexdigest(),
                "dump_seconds": round(dump_seconds, 3), "restore_seconds": round(restore_seconds, 3),
                "target": "fresh isolated database (removed by fixture after comparison)",
                "revision": revision, "tables_verified": len(actual_rows), "table_fingerprints": actual_rows,
                "schema_constraints_indexes_equal": True, "private_objects_verified": verified_objects,
                "runtime_privileges_after_no_acl_restore": privileges,
                "schema_normalization": "Only PostgreSQL's equivalent varchar-literal ARRAY to text[] cast expansion is normalized; remaining metadata compares exactly.",
                "consistency": "All HTTP writers stopped before dump and file copy; quiescent synthetic snapshot only.",
                "permissions": "Dump: CONNECT/USAGE/SELECT on all source tables. Restore: isolated database owner with CREATE on public schema.",
                "limits": "Production encryption, retention, off-site copies, PITR, RPO/RTO, concurrent file snapshots and deletion replay remain S5-3."}
            (output / "restore-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return report
    finally:
        source.dispose()
