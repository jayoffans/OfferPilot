"""Private local resume objects with bounded IO and no caller-controlled paths."""

import hashlib
import hmac
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

MAX_OBJECT_BYTES = 10 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_OBJECT_KEY = re.compile(r"[a-f0-9]{32}\.pdf\Z")
_CLAIM_TOKEN = re.compile(r"[a-f0-9]{32}\Z")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class StorageError(Exception):
    """Safe error categories; messages never contain object keys or local paths."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StoredObject:
    byte_size: int
    sha256: str


@runtime_checkable
class ResumeStorage(Protocol):
    def save(
        self, object_key: str, source: BinaryIO, *, claim_token: str | None = None
    ) -> StoredObject: ...

    def read(self, object_key: str, *, claim_token: str | None = None) -> bytes: ...

    def delete(self, object_key: str, *, claim_token: str | None = None) -> None: ...


def _unsafe_path() -> StorageError:
    return StorageError("unsafe_path", "Object storage path is not permitted")


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


class LocalResumeStorage:
    """Files remain beneath one private directory; failed writes retain .part.

    The caller must persist its cleanup journal before starting IO, serialize
    operations for an object, and retry delete after IO failures. Collision
    errors must not trigger deletion of another, pre-existing object.
    """

    def __init__(self, root: Path, max_bytes: int = MAX_OBJECT_BYTES) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("Storage size limit must be a positive integer")
        self._max_bytes = max_bytes
        try:
            self._root = Path(root).absolute()
            self._check_directory_chain(self._root)
            self._root = self._root.resolve(strict=False)
        except StorageError:
            raise
        except Exception:
            raise StorageError("storage_unavailable", "Object storage is unavailable") from None

    @staticmethod
    def _validate_key(object_key: str) -> None:
        if not isinstance(object_key, str) or not _OBJECT_KEY.fullmatch(object_key):
            raise StorageError("invalid_object_key", "Object key is invalid")

    @staticmethod
    def _validate_claim_token(claim_token: str | None) -> None:
        if claim_token is not None and (
            not isinstance(claim_token, str) or not _CLAIM_TOKEN.fullmatch(claim_token)
        ):
            raise StorageError("invalid_claim_token", "Object ownership claim is invalid")

    @staticmethod
    def _check_directory_chain(directory: Path) -> None:
        for candidate in (*reversed(directory.parents), directory):
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise _unsafe_path()

    def _check_root(self, *, create: bool = False) -> None:
        self._check_directory_chain(self._root)
        if self._root.resolve(strict=False) != self._root:
            raise _unsafe_path()
        if create:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._check_directory_chain(self._root)
            if self._root.resolve(strict=True) != self._root:
                raise _unsafe_path()

    def _checked_stat(self, path: Path) -> os.stat_result | None:
        self._check_root()
        if path.parent != self._root:
            raise _unsafe_path()
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if _is_reparse(info) or not stat.S_ISREG(info.st_mode):
            raise _unsafe_path()
        if path.resolve(strict=True).parent != self._root:
            raise _unsafe_path()
        return info

    def _open_file(
        self, path: Path, flags: int, mode: str, expected: os.stat_result | None = None
    ) -> BinaryIO:
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            actual = os.fstat(descriptor)
            current = self._checked_stat(path)
            if (
                current is None
                or _is_reparse(actual)
                or not stat.S_ISREG(actual.st_mode)
                or not os.path.samestat(current, actual)
                or (expected is not None and not os.path.samestat(expected, actual))
            ):
                raise _unsafe_path()
            return os.fdopen(descriptor, mode)
        except Exception:
            os.close(descriptor)
            raise

    def _sync_directory(self) -> None:
        # POSIX needs directory fsync for durable publication/unlink metadata.
        # Windows does not expose this directory operation through os.open.
        if os.name != "posix":
            return
        self._check_root()
        descriptor = os.open(
            self._root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _verify_claim(self, object_key: str, claim_token: str | None) -> bool:
        marker = self._root / f"{object_key}.claim"
        info = self._checked_stat(marker)
        if info is None:
            if claim_token is not None and any(
                self._checked_stat(self._root / name) is not None
                for name in (object_key, f"{object_key}.part")
            ):
                raise StorageError("ownership_mismatch", "Object ownership could not be verified")
            return False
        if claim_token is None or info.st_size != 32:
            raise StorageError("ownership_mismatch", "Object ownership could not be verified")
        with self._open_file(marker, os.O_RDONLY, "rb", expected=info) as source:
            stored_token = source.read(33)
        if not hmac.compare_digest(stored_token, claim_token.encode("ascii")):
            raise StorageError("ownership_mismatch", "Object ownership could not be verified")
        return True

    def _require_claim(self, object_key: str, claim_token: str | None) -> None:
        marker_exists = self._verify_claim(object_key, claim_token)
        if claim_token is not None and not marker_exists:
            raise StorageError("ownership_mismatch", "Object ownership could not be verified")

    def save(
        self, object_key: str, source: BinaryIO, *, claim_token: str | None = None
    ) -> StoredObject:
        self._validate_key(object_key)
        self._validate_claim_token(claim_token)
        try:
            self._check_root(create=True)
            target = self._root / object_key
            partial = self._root / f"{object_key}.part"
            marker = self._root / f"{object_key}.claim"
            if any(self._checked_stat(path) is not None for path in (target, partial, marker)):
                raise StorageError("object_exists", "Object already exists")
            if claim_token is not None:
                # Persist creation ownership before opening .part. A failed
                # database update must never make a colliding journal owner.
                with self._open_file(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, "wb") as claim:
                    claim.write(claim_token.encode("ascii"))
                    claim.flush()
                    os.fsync(claim.fileno())
                self._sync_directory()
            self._require_claim(object_key, claim_token)
            digest = hashlib.sha256()
            byte_size = 0
            with self._open_file(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, "wb") as output:
                while True:
                    chunk = source.read(min(_CHUNK_BYTES, self._max_bytes - byte_size + 1))
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise StorageError("storage_unavailable", "Object storage is unavailable")
                    if not chunk:
                        break
                    byte_size += chunk.nbytes if isinstance(chunk, memoryview) else len(chunk)
                    if byte_size > self._max_bytes:
                        raise StorageError("size_limit", "Object exceeds the storage size limit")
                    output.write(chunk)
                    digest.update(chunk)
                if byte_size == 0:
                    raise StorageError("empty_file", "Object content is empty")
                output.flush()
                os.fsync(output.fileno())
                written = os.fstat(output.fileno())
            current = self._checked_stat(partial)
            if current is None or not os.path.samestat(current, written):
                raise _unsafe_path()
            self._require_claim(object_key, claim_token)
            if self._checked_stat(target) is not None:
                raise StorageError("object_exists", "Object already exists")
            # A hard link publishes the complete file atomically and fails if
            # the destination exists. rename/replace may overwrite on POSIX.
            if os.link in os.supports_follow_symlinks:
                os.link(partial, target, follow_symlinks=False)
            else:
                os.link(partial, target)
            self._checked_stat(target)
            self._require_claim(object_key, claim_token)
            os.unlink(partial)
            self._sync_directory()
            return StoredObject(byte_size=byte_size, sha256=digest.hexdigest())
        except StorageError:
            raise
        except FileExistsError:
            raise StorageError("object_exists", "Object already exists") from None
        except Exception:
            raise StorageError("storage_unavailable", "Object storage is unavailable") from None

    def read(self, object_key: str, *, claim_token: str | None = None) -> bytes:
        self._validate_key(object_key)
        self._validate_claim_token(claim_token)
        try:
            self._verify_claim(object_key, claim_token)
            target = self._root / object_key
            info = self._checked_stat(target)
            if info is None:
                raise StorageError("file_missing", "Object content is unavailable")
            if info.st_size > self._max_bytes:
                raise StorageError("size_limit", "Object exceeds the storage size limit")
            self._require_claim(object_key, claim_token)
            result = bytearray()
            with self._open_file(target, os.O_RDONLY, "rb", expected=info) as source:
                while True:
                    chunk = source.read(min(_CHUNK_BYTES, self._max_bytes - len(result) + 1))
                    if not chunk:
                        break
                    result.extend(chunk)
                    if len(result) > self._max_bytes:
                        raise StorageError("size_limit", "Object exceeds the storage size limit")
            self._require_claim(object_key, claim_token)
            return bytes(result)
        except StorageError:
            raise
        except FileNotFoundError:
            raise StorageError("file_missing", "Object content is unavailable") from None
        except Exception:
            raise StorageError("storage_unavailable", "Object storage is unavailable") from None

    def delete(self, object_key: str, *, claim_token: str | None = None) -> None:
        self._validate_key(object_key)
        self._validate_claim_token(claim_token)
        try:
            marker_exists = self._verify_claim(object_key, claim_token)
            if claim_token is not None and not marker_exists:
                return
            changed = False
            for name in (object_key, f"{object_key}.part"):
                self._verify_claim(object_key, claim_token)
                target = self._root / name
                if self._checked_stat(target) is None:
                    continue
                try:
                    os.unlink(target)
                    changed = True
                except FileNotFoundError:
                    continue
            if marker_exists:
                self._verify_claim(object_key, claim_token)
                try:
                    os.unlink(self._root / f"{object_key}.claim")
                    changed = True
                except FileNotFoundError:
                    pass
            if changed:
                self._sync_directory()
        except StorageError:
            raise
        except Exception:
            raise StorageError("storage_unavailable", "Object storage is unavailable") from None

    def resolve_legacy_key(self, path: str) -> str:
        """Map only a direct, non-reparse historical object path; do not move IO."""
        try:
            self._check_root()
            if not isinstance(path, str) or not path or "\x00" in path:
                raise _unsafe_path()
            supplied = Path(path)
            if ".." in supplied.parts:
                raise _unsafe_path()
            self._validate_key(supplied.name)
            candidates = (
                (supplied,)
                if supplied.is_absolute()
                else (self._root / supplied, Path.cwd() / supplied, self._root.parent / supplied)
            )
            for candidate in candidates:
                candidate = candidate.absolute()
                if candidate.parent != self._root:
                    continue
                self._checked_stat(candidate)
                return candidate.name
            raise _unsafe_path()
        except StorageError:
            raise
        except Exception:
            raise StorageError("storage_unavailable", "Object storage is unavailable") from None
