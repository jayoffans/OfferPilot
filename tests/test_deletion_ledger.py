"""Deterministic publication and failure boundaries for a fresh deletion registry."""

import multiprocessing
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from offerpilot_api.account_lifecycle import ledger as ledger_module
from offerpilot_api.account_lifecycle.ledger import DeletionLedger, LedgerError


MARKER = "registry-v1"
MARKER_CONTENT = "offerpilot-deletions/1\n"


def _initialize_process(root, barrier, results):
    """Spawn-safe worker; only fixed diagnostic values cross the result queue."""
    original_link = os.link

    def synchronized_link(source, destination, **kwargs):
        if Path(source).read_text(encoding="ascii") != MARKER_CONTENT:
            raise AssertionError("Initialization published incomplete content")
        barrier.wait(timeout=20)
        return original_link(source, destination, **kwargs)

    try:
        with patch.object(ledger_module.os, "link", side_effect=synchronized_link):
            registry = DeletionLedger(Path(root))
            registry.initialize()
            registry._check()
        results.put("initialized")
    except BaseException as error:
        results.put(type(error).__name__)


def _pause_before_publication(root, entered, release):
    original_link = os.link

    def paused_link(source, destination, **kwargs):
        if Path(source).read_text(encoding="ascii") != MARKER_CONTENT:
            raise AssertionError("Initialization staged incomplete content")
        entered.set()
        if not release.wait(timeout=30):
            raise RuntimeError("Synthetic publication gate timed out")
        return original_link(source, destination, **kwargs)

    with patch.object(ledger_module.os, "link", side_effect=paused_link):
        DeletionLedger(Path(root)).initialize()


class DeletionLedgerInitializationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="offerpilot-registry-test-")
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.root = self.parent / "deletion-ledger"
        self.registry = DeletionLedger(self.root)
        self.marker = self.root / MARKER

    def assert_complete(self):
        self.assertEqual(self.marker.read_text(encoding="ascii"), MARKER_CONTENT)
        self.registry._check()
        self.assertEqual({path.name for path in self.root.iterdir()}, {MARKER})

    def assert_unpublished(self):
        self.assertFalse(self.marker.exists())
        with self.assertRaises(LedgerError):
            self.registry._check()

    def test_marker_is_absent_until_complete_staged_file_is_synced_and_closed(self):
        entered, release, synced = Event(), Event(), Event()
        original_link, original_fsync = os.link, os.fsync
        original_open = Path.open
        staged = {}

        def open_file(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if path.name == MARKER and path.parent != self.root and mode == "x":
                staged["writer"] = stream
            return stream

        def sync(descriptor):
            original_fsync(descriptor)
            synced.set()

        def publish(source, destination, **kwargs):
            self.assertTrue(synced.is_set())
            self.assertEqual(Path(source).read_text(encoding="ascii"), MARKER_CONTENT)
            self.assertTrue(staged["writer"].closed)
            entered.set()
            if not release.wait(timeout=10):
                raise RuntimeError("Synthetic publication gate timed out")
            return original_link(source, destination, **kwargs)

        with patch.object(ledger_module.os, "fsync", side_effect=sync), \
             patch.object(ledger_module.os, "link", side_effect=publish), \
             patch.object(Path, "open", new=open_file):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.registry.initialize)
                try:
                    self.assertTrue(entered.wait(timeout=10))
                    self.assert_unpublished()
                finally:
                    release.set()
                future.result(timeout=10)
        self.assert_complete()
        self.assertEqual({path.name for path in self.parent.iterdir()}, {self.root.name})

    def test_two_threads_publish_one_complete_marker_without_losing_initializer(self):
        ready = Barrier(2)
        original_link = os.link

        def publish(source, destination, **kwargs):
            self.assertEqual(Path(source).read_text(encoding="ascii"), MARKER_CONTENT)
            ready.wait(timeout=10)
            return original_link(source, destination, **kwargs)

        with patch.object(ledger_module.os, "link", side_effect=publish):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(DeletionLedger(self.root).initialize) for _ in range(2)]
                for future in futures:
                    future.result(timeout=15)
        self.assert_complete()
        self.assertEqual({path.name for path in self.parent.iterdir()}, {self.root.name})

    def test_two_spawned_processes_publish_one_complete_marker(self):
        context = multiprocessing.get_context("spawn")
        ready, results = context.Barrier(2), context.Queue()
        processes = [context.Process(target=_initialize_process, args=(str(self.root), ready, results))
                     for _ in range(2)]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=30)
                self.assertFalse(process.is_alive(), "Registry initializer did not finish")
                self.assertEqual(process.exitcode, 0)
            self.assertCountEqual([results.get(timeout=5) for _ in processes], ["initialized"] * 2)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            results.close()
            results.join_thread()
        self.assert_complete()
        self.assertEqual({path.name for path in self.parent.iterdir()}, {self.root.name})

    def test_process_interrupted_before_publication_stays_closed_and_can_be_prepared_again(self):
        context = multiprocessing.get_context("spawn")
        entered, release = context.Event(), context.Event()
        process = context.Process(target=_pause_before_publication, args=(str(self.root), entered, release))
        process.start()
        try:
            self.assertTrue(entered.wait(timeout=20))
            self.assert_unpublished()
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)
        self.assertFalse(process.is_alive())
        self.assert_unpublished()
        # Recovery is an explicit trusted preparation call, never a request.
        self.registry.initialize()
        self.assert_complete()

    def test_corrupt_marker_is_never_replaced(self):
        self.root.mkdir()
        for content in (b"", b"offerpilot-deletions/other\n", b"\xff"):
            with self.subTest(content_length=len(content)):
                self.marker.write_bytes(content)
                with patch.object(ledger_module.os, "link") as publish:
                    with self.assertRaises(LedgerError):
                        self.registry.initialize()
                publish.assert_not_called()
                self.assertEqual(self.marker.read_bytes(), content)

    def test_unrecognized_existing_entry_is_not_overwritten_or_adopted(self):
        self.root.mkdir()
        unknown = self.root / "untrusted-entry"
        unknown.write_bytes(b"synthetic-unknown")
        with self.assertRaises(LedgerError):
            self.registry.initialize()
        self.assert_unpublished()
        self.assertEqual(unknown.read_bytes(), b"synthetic-unknown")

    def test_existing_tombstone_is_preserved_by_repeated_preparation(self):
        self.registry.initialize()
        user_id, deletion_id = uuid4(), uuid4()
        self.registry.record(user_id, deletion_id)
        path = self.root / f"{user_id.hex}.json"
        before = path.read_bytes()
        self.registry.initialize()
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(self.registry.contains(user_id))

    def test_staged_write_failure_publishes_nothing_and_allows_trusted_retry(self):
        original_open = Path.open

        class FailingWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, _content):
                raise OSError("Synthetic staged write failure")

            def __getattr__(self, name):
                return getattr(self.stream, name)

        def open_file(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if path.name == MARKER and path.parent != self.root and mode == "x":
                return FailingWriter(stream)
            return stream

        with patch.object(Path, "open", new=open_file):
            with self.assertRaises(LedgerError):
                self.registry.initialize()
        self.assert_unpublished()
        self.assertEqual({path.name for path in self.parent.iterdir()}, {self.root.name})
        self.registry.initialize()
        self.assert_complete()

    def test_file_fsync_failure_publishes_nothing_and_allows_trusted_retry(self):
        with patch.object(ledger_module.os, "fsync", side_effect=OSError("Synthetic fsync failure")):
            with self.assertRaises(LedgerError):
                self.registry.initialize()
        self.assert_unpublished()
        self.assertEqual({path.name for path in self.parent.iterdir()}, {self.root.name})
        self.registry.initialize()
        self.assert_complete()

    def test_link_failure_publishes_nothing_and_allows_trusted_retry(self):
        with patch.object(ledger_module.os, "link", side_effect=OSError("Synthetic link failure")):
            with self.assertRaises(LedgerError):
                self.registry.initialize()
        self.assert_unpublished()
        self.assertEqual({path.name for path in self.parent.iterdir()}, {self.root.name})
        self.registry.initialize()
        self.assert_complete()

    def test_directory_sync_failure_never_leaves_a_partial_marker(self):
        with patch.object(DeletionLedger, "_sync_directory", side_effect=OSError("Synthetic directory sync failure")):
            with self.assertRaises(LedgerError):
                self.registry.initialize()
        self.assert_complete()
        self.registry.initialize()
        self.assert_complete()


if __name__ == "__main__":
    unittest.main()
