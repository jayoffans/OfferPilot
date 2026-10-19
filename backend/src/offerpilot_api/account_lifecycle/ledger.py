"""Independent, append-only deletion registry. Never restored from a DB backup.

Only pseudonymous UUIDs and a UTC intent time are retained. A missing/corrupt
registry fails closed; initialize is an explicit new-environment operation.
"""
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


class LedgerError(Exception):
    pass


class DeletionLedger:
    def __init__(self, root: Path):
        self.root = Path(root)

    def initialize(self):
        """Trusted new-environment setup; never called by a request or restore.

        Publish a complete marker without replacing an existing one. A sibling
        staging directory keeps temporary files out of deletion replay, and the
        hard link is atomic on both NTFS and Linux filesystems. Unsupported file
        systems fail closed; no partial-marker or overwrite fallback is used.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._check_directory()
            if any(self.root.iterdir()):
                self._check()
                return
            marker = self.root / "registry-v1"
            with tempfile.TemporaryDirectory(prefix=".registry-init-", dir=self.root.parent) as staging:
                staged = Path(staging) / "registry-v1"
                with staged.open("x", encoding="ascii") as stream:
                    stream.write("offerpilot-deletions/1\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(staged, marker)
                except FileExistsError:
                    # Another trusted initializer won. Validate, never replace.
                    self._check()
            self._sync_directory()
        except OSError:
            raise LedgerError("Deletion registry unavailable") from None

    def _check_directory(self):
        for ancestor in (self.root, *self.root.parents):
            if ancestor.is_symlink() or getattr(ancestor.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise LedgerError("Deletion registry unavailable")

    def _check(self):
        try:
            self._check_directory()
            marker = self.root / "registry-v1"
            if marker.is_symlink() or marker.read_text(encoding="ascii") != "offerpilot-deletions/1\n":
                raise LedgerError("Deletion registry unavailable")
        except (OSError, UnicodeError):
            raise LedgerError("Deletion registry unavailable") from None

    def _sync_directory(self):
        if os.name != "nt":
            descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _read(self, path):
        try:
            info = path.lstat()
            if path.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400) or info.st_size > 1024:
                raise LedgerError("Deletion registry invalid")
            value = json.loads(path.read_text(encoding="utf-8"))
            if set(value) != {"user_id", "deletion_id", "created_at"}:
                raise ValueError()
            if UUID(value["user_id"]).hex != path.stem:
                raise ValueError()
            UUID(value["deletion_id"])
            timestamp = datetime.fromisoformat(value["created_at"])
            if timestamp.tzinfo is None:
                raise ValueError()
            return value
        except (OSError, ValueError, TypeError, KeyError):
            raise LedgerError("Deletion registry invalid") from None

    def contains(self, user_id):
        self._check()
        path = self.root / f"{UUID(str(user_id)).hex}.json"
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise LedgerError("Deletion registry unavailable") from None
        self._read(path)
        return True

    def entries(self):
        self._check()
        # The iterator is consumed in bounded batches by the recovery service.
        for path in self.root.iterdir():
            if path.name == "registry-v1":
                continue
            if path.suffix != ".json":
                raise LedgerError("Deletion registry invalid")
            yield self._read(path)

    def record(self, user_id, deletion_id, created_at=None):
        self._check()
        path = self.root / f"{UUID(str(user_id)).hex}.json"
        value = {"user_id": str(user_id), "deletion_id": str(deletion_id),
                 "created_at": (created_at or datetime.now(timezone.utc)).isoformat()}
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            self._sync_directory()
        except FileExistsError:
            # The first intent wins. An interrupted partial write fails closed
            # for this account and needs a trusted operator; never overwrite it.
            return self._read(path)
        except OSError:
            raise LedgerError("Deletion registry unavailable") from None
        return value
