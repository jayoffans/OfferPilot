"""Private storage checks using only isolated, synthetic temporary objects."""

import base64
from array import array
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch

from offerpilot_api.resume_ingestion.storage import (
    LocalResumeStorage,
    ResumeStorage,
    StorageError,
    StoredObject,
)

KEY = "a" * 32 + ".pdf"
OTHER_KEY = "b" * 32 + ".pdf"
CLAIM = "c" * 32
OTHER_CLAIM = "d" * 32


class PrivateStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="offerpilot-storage-")
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.root = self.directory / "objects"
        self.storage = LocalResumeStorage(self.root)

    def assert_storage_error(self, code: str, action) -> StorageError:
        with self.assertRaises(StorageError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(str(self.directory), str(raised.exception))
        self.assertNotIn(KEY, str(raised.exception))
        return raised.exception

    def _symlink(self, link: Path, target: Path, *, directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError:
            self.skipTest("The current account cannot create symbolic links")

    def test_constructor_is_lazy_and_save_read_returns_only_content_and_metadata(self) -> None:
        self.assertFalse(self.root.exists())
        self.assertIsInstance(self.storage, ResumeStorage)
        content = b"%PDF-synthetic-private-content\n" * 100
        source = io.BytesIO(content)
        stored = self.storage.save(KEY, source)
        self.assertIsInstance(stored, StoredObject)
        self.assertEqual(stored.byte_size, len(content))
        self.assertEqual(stored.sha256, hashlib.sha256(content).hexdigest())
        self.assertFalse(source.closed)
        self.assertEqual(self.storage.read(KEY), content)
        self.assertEqual(list(self.root.iterdir()), [self.root / KEY])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((self.root / KEY).stat().st_mode), 0o600)

    def test_managed_save_persists_claim_before_content_and_requires_same_owner(self) -> None:
        storage = self.storage

        class ClaimAwareSource(io.BytesIO):
            def read(source, size=-1):
                self.assertEqual((self.root / f"{KEY}.claim").read_bytes(), CLAIM.encode("ascii"))
                return super().read(size)

        content = b"managed private content"
        stored = storage.save(KEY, ClaimAwareSource(content), claim_token=CLAIM)
        self.assertEqual(stored.byte_size, len(content))
        self.assertEqual(stored.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual((self.root / f"{KEY}.claim").read_bytes(), CLAIM.encode("ascii"))
        self.assertEqual(storage.read(KEY, claim_token=CLAIM), content)
        self.assert_storage_error("ownership_mismatch", lambda: storage.read(KEY))
        self.assert_storage_error("ownership_mismatch", lambda: storage.delete(KEY))
        self.assert_storage_error(
            "ownership_mismatch", lambda: storage.read(KEY, claim_token=OTHER_CLAIM)
        )
        self.assert_storage_error(
            "ownership_mismatch", lambda: storage.delete(KEY, claim_token=OTHER_CLAIM)
        )
        self.assertEqual(storage.read(KEY, claim_token=CLAIM), content)
        storage.delete(KEY, claim_token=CLAIM)
        self.assertEqual(list(self.root.iterdir()), [])
        storage.delete(KEY, claim_token=CLAIM)

    def test_invalid_claim_tokens_fail_before_creating_directory_or_reading_source(self) -> None:
        for token in ("", "C" * 32, "c" * 31, "c" * 33, "../" + CLAIM, "PRIVATE_TOKEN_VALUE"):
            with self.subTest(token=token):
                source = Mock()
                self.assert_storage_error(
                    "invalid_claim_token", lambda: self.storage.save(KEY, source, claim_token=token)
                )
                self.assert_storage_error(
                    "invalid_claim_token", lambda: self.storage.read(KEY, claim_token=token)
                )
                self.assert_storage_error(
                    "invalid_claim_token", lambda: self.storage.delete(KEY, claim_token=token)
                )
                source.read.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_managed_collision_without_marker_cannot_delete_existing_legacy_object(self) -> None:
        original = b"existing legacy private content"
        self.storage.save(KEY, io.BytesIO(original))
        self.assert_storage_error(
            "object_exists", lambda: self.storage.save(KEY, io.BytesIO(b"new"), claim_token=CLAIM)
        )
        self.assertFalse((self.root / f"{KEY}.claim").exists())
        # A stale uploading journal has only its own token, even if the DB
        # collision update failed. It cannot clean up this existing object.
        self.assert_storage_error(
            "ownership_mismatch", lambda: self.storage.delete(KEY, claim_token=CLAIM)
        )
        self.assert_storage_error(
            "ownership_mismatch", lambda: self.storage.read(KEY, claim_token=CLAIM)
        )
        self.assertEqual(self.storage.read(KEY), original)

    def test_managed_collision_with_other_marker_preserves_real_content_and_claim(self) -> None:
        self.storage.save(KEY, io.BytesIO(b"first owner's content"), claim_token=CLAIM)
        self.assert_storage_error(
            "object_exists",
            lambda: self.storage.save(KEY, io.BytesIO(b"second owner's content"), claim_token=OTHER_CLAIM),
        )
        for token in (OTHER_CLAIM, None):
            self.assert_storage_error(
                "ownership_mismatch", lambda: self.storage.delete(KEY, claim_token=token)
            )
        self.assertEqual(self.storage.read(KEY, claim_token=CLAIM), b"first owner's content")
        self.assertEqual((self.root / f"{KEY}.claim").read_bytes(), CLAIM.encode("ascii"))

    def test_concurrent_exclusive_claim_creation_has_one_owner_and_cannot_cleanup_winner(self) -> None:
        barrier = Barrier(2)
        real_open_file = self.storage._open_file

        def synchronize_claim_creation(path, flags, mode, expected=None):
            if Path(path).name == f"{KEY}.claim" and flags & os.O_CREAT:
                barrier.wait(timeout=5)
            return real_open_file(path, flags, mode, expected)

        def upload(token):
            try:
                self.storage.save(KEY, io.BytesIO(token.encode("ascii")), claim_token=token)
                return token, "saved"
            except StorageError as error:
                return token, error.code

        with (
            patch.object(self.storage, "_open_file", side_effect=synchronize_claim_creation),
            ThreadPoolExecutor(max_workers=2) as workers,
        ):
            results = list(workers.map(upload, (CLAIM, OTHER_CLAIM)))
        self.assertEqual(sorted(status for _, status in results), ["object_exists", "saved"])
        winner = next(token for token, status in results if status == "saved")
        loser = next(token for token, status in results if status != "saved")
        self.assert_storage_error(
            "ownership_mismatch", lambda: self.storage.delete(KEY, claim_token=loser)
        )
        self.assertEqual(self.storage.read(KEY, claim_token=winner), winner.encode("ascii"))
        self.storage.delete(KEY, claim_token=winner)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_wrong_claim_is_rejected_even_when_no_content_has_been_written(self) -> None:
        self.root.mkdir()
        marker = self.root / f"{KEY}.claim"
        marker.write_bytes(CLAIM.encode("ascii"))
        self.assert_storage_error(
            "ownership_mismatch", lambda: self.storage.delete(KEY, claim_token=OTHER_CLAIM)
        )
        self.assert_storage_error("ownership_mismatch", lambda: self.storage.delete(KEY))
        self.assertTrue(marker.exists())
        self.assert_storage_error(
            "file_missing", lambda: self.storage.read(KEY, claim_token=CLAIM)
        )
        self.storage.delete(KEY, claim_token=CLAIM)
        self.assertEqual(list(self.root.iterdir()), [])
        self.storage.delete(KEY, claim_token=CLAIM)

    def test_existing_partial_without_claim_cannot_be_cleaned_by_a_managed_journal(self) -> None:
        self.root.mkdir()
        partial = self.root / f"{KEY}.part"
        partial.write_bytes(b"another interrupted upload")
        self.assert_storage_error(
            "ownership_mismatch", lambda: self.storage.delete(KEY, claim_token=CLAIM)
        )
        self.assertEqual(partial.read_bytes(), b"another interrupted upload")
        self.storage.delete(KEY)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_managed_interrupted_write_retains_claim_for_matching_cleanup(self) -> None:
        source = Mock()
        source.read.side_effect = [b"partial owned content", OSError("PRIVATE_SOURCE_CONTENT")]
        self.assert_storage_error(
            "storage_unavailable", lambda: self.storage.save(KEY, source, claim_token=CLAIM)
        )
        self.assertFalse((self.root / KEY).exists())
        self.assertEqual((self.root / f"{KEY}.part").read_bytes(), b"partial owned content")
        self.assertEqual((self.root / f"{KEY}.claim").read_bytes(), CLAIM.encode("ascii"))
        self.assert_storage_error(
            "ownership_mismatch", lambda: self.storage.delete(KEY, claim_token=OTHER_CLAIM)
        )
        self.storage.delete(KEY, claim_token=CLAIM)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_managed_delete_failure_leaves_claim_until_final_and_partial_are_gone(self) -> None:
        self.storage.save(KEY, io.BytesIO(b"owned content"), claim_token=CLAIM)
        partial = self.root / f"{KEY}.part"
        partial.write_bytes(b"owned partial")
        marker = self.root / f"{KEY}.claim"
        real_unlink = os.unlink

        def fail_partial(path, *args, **kwargs):
            if Path(path) == partial:
                raise PermissionError("PRIVATE_PARTIAL_PATH")
            return real_unlink(path, *args, **kwargs)

        with patch("offerpilot_api.resume_ingestion.storage.os.unlink", side_effect=fail_partial):
            self.assert_storage_error(
                "storage_unavailable", lambda: self.storage.delete(KEY, claim_token=CLAIM)
            )
        self.assertFalse((self.root / KEY).exists())
        self.assertTrue(partial.exists())
        self.assertTrue(marker.exists())
        self.storage.delete(KEY, claim_token=CLAIM)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_corrupt_marker_cannot_authorize_content_cleanup(self) -> None:
        self.storage.save(KEY, io.BytesIO(b"owned content"), claim_token=CLAIM)
        marker = self.root / f"{KEY}.claim"
        for corrupt in (b"", b"c" * 31, b"c" * 33, b"PRIVATE_MARKER_DATA" * 3):
            marker.write_bytes(corrupt)
            for token in (CLAIM, None):
                self.assert_storage_error(
                    "ownership_mismatch", lambda: self.storage.delete(KEY, claim_token=token)
                )
                self.assert_storage_error(
                    "ownership_mismatch", lambda: self.storage.read(KEY, claim_token=token)
                )
            self.assertEqual((self.root / KEY).read_bytes(), b"owned content")
        marker.write_bytes(CLAIM.encode("ascii"))
        self.storage.delete(KEY, claim_token=CLAIM)

    def test_incomplete_claim_only_fails_closed_for_manual_recovery(self) -> None:
        self.root.mkdir()
        marker = self.root / f"{KEY}.claim"
        for incomplete in (b"", CLAIM[:12].encode("ascii")):
            marker.write_bytes(incomplete)
            for token in (CLAIM, OTHER_CLAIM, None):
                self.assert_storage_error(
                    "ownership_mismatch", lambda: self.storage.delete(KEY, claim_token=token)
                )
            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_bytes(), incomplete)

    def test_claim_reparse_attribute_is_rejected_without_reading_or_deleting_objects(self) -> None:
        self.storage.save(KEY, io.BytesIO(b"owned content"), claim_token=CLAIM)
        marker = self.root / f"{KEY}.claim"
        real_lstat = Path.lstat

        def reparse_lstat(path, *args, **kwargs):
            if path == marker:
                return SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o600,
                    st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
                )
            return real_lstat(path, *args, **kwargs)

        with patch.object(Path, "lstat", reparse_lstat):
            for token in (CLAIM, None):
                self.assert_storage_error(
                    "unsafe_path", lambda: self.storage.read(KEY, claim_token=token)
                )
                self.assert_storage_error(
                    "unsafe_path", lambda: self.storage.delete(KEY, claim_token=token)
                )
        self.assertEqual(self.storage.read(KEY, claim_token=CLAIM), b"owned content")
        self.storage.delete(KEY, claim_token=CLAIM)

    def test_keys_reject_traversal_absolute_paths_uppercase_and_alternate_streams(self) -> None:
        invalid = (
            "../" + KEY,
            "folder/" + KEY,
            "folder\\" + KEY,
            str(self.directory / KEY),
            "A" * 32 + ".pdf",
            "a" * 32 + ".PDF",
            KEY + ":secret",
            KEY + ".part",
            "a" * 31 + ".pdf",
            "",
        )
        for object_key in invalid:
            with self.subTest(object_key=object_key):
                source = Mock()
                self.assert_storage_error(
                    "invalid_object_key", lambda: self.storage.save(object_key, source)
                )
                source.read.assert_not_called()
                self.assert_storage_error("invalid_object_key", lambda: self.storage.read(object_key))
                self.assert_storage_error("invalid_object_key", lambda: self.storage.delete(object_key))
        self.assertFalse(self.root.exists())

    def test_save_size_and_empty_limits_retain_deletable_partial_objects(self) -> None:
        storage = LocalResumeStorage(self.root, max_bytes=4)
        self.assert_storage_error("size_limit", lambda: storage.save(KEY, io.BytesIO(b"12345")))
        self.assertFalse((self.root / KEY).exists())
        self.assertTrue((self.root / f"{KEY}.part").exists())
        storage.delete(KEY)
        self.assert_storage_error("empty_file", lambda: storage.save(KEY, io.BytesIO()))
        self.assertTrue((self.root / f"{KEY}.part").exists())
        storage.delete(KEY)
        storage.save(KEY, io.BytesIO(b"1234"))
        self.assertEqual(storage.read(KEY), b"1234")

    def test_byte_limits_count_buffer_bytes_instead_of_multibyte_elements(self) -> None:
        storage = LocalResumeStorage(self.root, max_bytes=4)
        source = Mock()
        source.read.side_effect = [memoryview(array("I", [1, 2])), b""]
        self.assert_storage_error("size_limit", lambda: storage.save(KEY, source))
        self.assertFalse((self.root / KEY).exists())
        storage.delete(KEY)

    def test_existing_final_and_interrupted_partial_are_never_overwritten(self) -> None:
        original = b"original"
        self.storage.save(KEY, io.BytesIO(original))
        self.assert_storage_error("object_exists", lambda: self.storage.save(KEY, io.BytesIO(b"new")))
        self.assertEqual(self.storage.read(KEY), original)
        partial = self.root / f"{OTHER_KEY}.part"
        partial.write_bytes(b"interrupted")
        self.assert_storage_error(
            "object_exists", lambda: self.storage.save(OTHER_KEY, io.BytesIO(b"new"))
        )
        self.assertEqual(partial.read_bytes(), b"interrupted")
        self.storage.delete(OTHER_KEY)
        self.assertFalse(partial.exists())

    def test_publish_collision_preserves_existing_object_without_replace(self) -> None:
        real_link = os.link

        def create_collision(source, target, **kwargs):
            Path(target).write_bytes(b"existing concurrent object")
            return real_link(source, target, **kwargs)

        with patch("offerpilot_api.resume_ingestion.storage.os.link", side_effect=create_collision):
            self.assert_storage_error(
                "object_exists", lambda: self.storage.save(KEY, io.BytesIO(b"new object"))
            )
        self.assertEqual((self.root / KEY).read_bytes(), b"existing concurrent object")
        self.assertEqual((self.root / f"{KEY}.part").read_bytes(), b"new object")

    def test_interrupted_source_and_fsync_failure_close_handles_and_allow_cleanup(self) -> None:
        handles = []
        real_fdopen = os.fdopen

        def record_handle(*args, **kwargs):
            result = real_fdopen(*args, **kwargs)
            handles.append(result)
            return result

        class InterruptedSource:
            def __init__(self):
                self.calls = 0

            def read(self, _size):
                self.calls += 1
                if self.calls == 1:
                    return b"partial content"
                raise OSError("PRIVATE_SOURCE_PATH_AND_CONTENT")

        with patch("offerpilot_api.resume_ingestion.storage.os.fdopen", side_effect=record_handle):
            error = self.assert_storage_error(
                "storage_unavailable", lambda: self.storage.save(KEY, InterruptedSource())
            )
            self.assertNotIn("PRIVATE_SOURCE_PATH_AND_CONTENT", str(error))
            self.assertTrue(all(handle.closed for handle in handles))
            self.storage.delete(KEY)
            with patch(
                "offerpilot_api.resume_ingestion.storage.os.fsync",
                side_effect=OSError("PRIVATE_FSYNC_PATH"),
            ):
                error = self.assert_storage_error(
                    "storage_unavailable", lambda: self.storage.save(KEY, io.BytesIO(b"content"))
                )
                self.assertNotIn("PRIVATE_FSYNC_PATH", str(error))
            self.assertTrue(all(handle.closed for handle in handles))
        self.storage.delete(KEY)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_read_missing_and_oversized_files_fail_with_safe_codes(self) -> None:
        self.assert_storage_error("file_missing", lambda: self.storage.read(KEY))
        self.assertFalse(self.root.exists())
        self.root.mkdir()
        (self.root / KEY).write_bytes(b"12345")
        storage = LocalResumeStorage(self.root, max_bytes=4)
        self.assert_storage_error("size_limit", lambda: storage.read(KEY))

    def test_read_rechecks_size_while_streaming_and_closes_handle(self) -> None:
        self.root.mkdir()
        (self.root / KEY).write_bytes(b"12345")
        storage = LocalResumeStorage(self.root, max_bytes=4)
        real_checked_stat = storage._checked_stat
        real_fdopen = os.fdopen
        handles = []

        def undersized_stat(path):
            info = real_checked_stat(path)
            if info is None:
                return None
            fields = list(info)
            fields[6] = 0
            return os.stat_result(fields)

        def record_handle(*args, **kwargs):
            result = real_fdopen(*args, **kwargs)
            handles.append(result)
            return result

        with (
            patch.object(storage, "_checked_stat", side_effect=undersized_stat),
            patch("offerpilot_api.resume_ingestion.storage.os.fdopen", side_effect=record_handle),
        ):
            self.assert_storage_error("size_limit", lambda: storage.read(KEY))
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)

    def test_successful_read_closes_its_handle(self) -> None:
        self.storage.save(KEY, io.BytesIO(b"content"))
        real_fdopen = os.fdopen
        handles = []

        def record_handle(*args, **kwargs):
            result = real_fdopen(*args, **kwargs)
            handles.append(result)
            return result

        with patch("offerpilot_api.resume_ingestion.storage.os.fdopen", side_effect=record_handle):
            self.assertEqual(self.storage.read(KEY), b"content")
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)

    def test_delete_is_idempotent_and_cleans_both_final_and_partial(self) -> None:
        self.storage.delete(KEY)
        self.assertFalse(self.root.exists())
        self.storage.save(KEY, io.BytesIO(b"content"))
        (self.root / f"{KEY}.part").write_bytes(b"interrupted")
        self.storage.delete(KEY)
        self.storage.delete(KEY)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_delete_failure_is_reported_and_retry_cleans_remaining_objects(self) -> None:
        self.storage.save(KEY, io.BytesIO(b"content"))
        (self.root / f"{KEY}.part").write_bytes(b"interrupted")
        with patch(
            "offerpilot_api.resume_ingestion.storage.os.unlink",
            side_effect=PermissionError("PRIVATE_DELETE_PATH"),
        ):
            error = self.assert_storage_error("storage_unavailable", lambda: self.storage.delete(KEY))
        self.assertNotIn("PRIVATE_DELETE_PATH", str(error))
        self.assertTrue((self.root / KEY).exists())
        self.assertTrue((self.root / f"{KEY}.part").exists())
        self.storage.delete(KEY)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failure_after_publish_leaves_both_entries_for_journal_cleanup(self) -> None:
        with patch(
            "offerpilot_api.resume_ingestion.storage.os.unlink",
            side_effect=PermissionError("PRIVATE_PARTIAL_CLEANUP_PATH"),
        ):
            error = self.assert_storage_error(
                "storage_unavailable", lambda: self.storage.save(KEY, io.BytesIO(b"complete content"))
            )
        self.assertNotIn("PRIVATE_PARTIAL_CLEANUP_PATH", str(error))
        self.assertEqual((self.root / KEY).read_bytes(), b"complete content")
        self.assertEqual((self.root / f"{KEY}.part").read_bytes(), b"complete content")
        self.storage.delete(KEY)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_non_regular_object_or_partial_paths_are_rejected(self) -> None:
        self.root.mkdir()
        (self.root / KEY).mkdir()
        self.assert_storage_error("unsafe_path", lambda: self.storage.read(KEY))
        self.assert_storage_error("unsafe_path", lambda: self.storage.delete(KEY))
        self.assert_storage_error("unsafe_path", lambda: self.storage.save(KEY, io.BytesIO(b"new")))
        self.assertTrue((self.root / KEY).is_dir())
        (self.root / f"{OTHER_KEY}.part").mkdir()
        self.assert_storage_error("unsafe_path", lambda: self.storage.delete(OTHER_KEY))

    def test_file_and_partial_symlinks_never_read_or_delete_external_targets(self) -> None:
        self.root.mkdir()
        outside = self.directory / "outside.pdf"
        outside.write_bytes(b"outside private content")
        self._symlink(self.root / KEY, outside)
        self._symlink(self.root / f"{OTHER_KEY}.part", outside)
        self.assert_storage_error("unsafe_path", lambda: self.storage.read(KEY))
        self.assert_storage_error("unsafe_path", lambda: self.storage.delete(KEY))
        self.assert_storage_error("unsafe_path", lambda: self.storage.delete(OTHER_KEY))
        self.assert_storage_error("unsafe_path", lambda: self.storage.save(KEY, io.BytesIO(b"new")))
        self.assertEqual(outside.read_bytes(), b"outside private content")

    def test_root_and_ancestor_symlinks_are_rejected_in_constructor_and_after_replacement(self) -> None:
        outside = self.directory / "outside"
        outside.mkdir()
        self._symlink(self.root, outside, directory=True)
        self.assert_storage_error("unsafe_path", lambda: LocalResumeStorage(self.root))
        self.assert_storage_error("unsafe_path", lambda: LocalResumeStorage(self.root / "nested"))
        self.assert_storage_error("unsafe_path", lambda: self.storage.read(KEY))
        self.assert_storage_error("unsafe_path", lambda: self.storage.delete(KEY))
        self.assert_storage_error("unsafe_path", lambda: self.storage.save(KEY, io.BytesIO(b"new")))
        self.assertEqual(list(outside.iterdir()), [])

    def test_reparse_attribute_is_rejected_even_without_symlink_mode(self) -> None:
        real_lstat = Path.lstat

        def reparse_lstat(path, *args, **kwargs):
            if path == self.root:
                return SimpleNamespace(
                    st_mode=stat.S_IFDIR | 0o700,
                    st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
                )
            return real_lstat(path, *args, **kwargs)

        with patch.object(Path, "lstat", reparse_lstat):
            self.assert_storage_error("unsafe_path", lambda: LocalResumeStorage(self.root))
            self.assert_storage_error("unsafe_path", lambda: self.storage.delete(KEY))

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_windows_junction_reparse_point_is_rejected_without_touching_target(self) -> None:
        outside = self.directory / "outside"
        outside.mkdir()
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = (
            "New-Item -ItemType Junction -Path " + quote(self.root)
            + " -Value " + quote(outside) + " -ErrorAction Stop | Out-Null"
        )
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if result.returncode:
            self.skipTest("The current account cannot create a directory junction")
        try:
            self.assert_storage_error("unsafe_path", lambda: LocalResumeStorage(self.root))
            self.assert_storage_error("unsafe_path", lambda: self.storage.delete(KEY))
            self.assert_storage_error("unsafe_path", lambda: self.storage.save(KEY, io.BytesIO(b"new")))
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            # Remove only this generated junction entry, never its target tree.
            os.rmdir(self.root)

    def test_legacy_path_resolution_is_direct_bounded_and_does_not_create_or_move_files(self) -> None:
        self.assertEqual(self.storage.resolve_legacy_key(KEY), KEY)
        self.assertEqual(self.storage.resolve_legacy_key(str(self.root / KEY)), KEY)
        self.assertEqual(self.storage.resolve_legacy_key(str(Path(self.root.name) / KEY)), KEY)
        invalid = (
            str(self.directory / KEY),
            "../" + KEY,
            str(self.root / "nested" / KEY),
            str(self.root / ".." / self.root.name / KEY),
        )
        for path in invalid:
            with self.subTest(path=path):
                self.assert_storage_error("unsafe_path", lambda: self.storage.resolve_legacy_key(path))
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
