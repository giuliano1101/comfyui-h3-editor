import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from provider_jobs import Capabilities, JobError, JobManager, Poll, SimulatedAdapter


class ProviderJobsTest(unittest.TestCase):
    def manager(self, *args, **kwargs):
        manager = JobManager(*args, **kwargs)
        self.addCleanup(manager.close)
        return manager

    def submit(self, manager, **kwargs):
        return manager.submit("simulation", "private prompt", 4, cost_acknowledged=True, **kwargs)

    def test_simulation_has_no_media_or_network_and_terminal_states_are_final(self):
        manager = self.manager()
        self.assertTrue(manager.capabilities()["simulation"]["simulated"])
        job = self.submit(manager)
        self.assertEqual(job["state"], "queued")
        result = manager.poll(job["id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertFalse(result["has_output"])
        self.assertEqual(manager.cancel(job["id"])["state"], "succeeded")
        with self.assertRaises(JobError):
            manager.output(job["id"])

    def test_validation_and_confirmation(self):
        manager = self.manager()
        for provider, prompt, seconds in (("bad", "x", 1), ("simulation", "", 1), ("simulation", "x", 11), ("simulation", "x", True), ("simulation", [], 1)):
            with self.subTest(provider=provider, seconds=seconds), self.assertRaises(JobError):
                manager.submit(provider, prompt, seconds, cost_acknowledged=True)
        for value in (False, 1, "true"):
            with self.assertRaises(JobError):
                manager.submit("simulation", "x", 1, cost_acknowledged=value)
        for kwargs in ({"model": "other"}, {"resolution": "1080P"}, {"project": "../x"}, {"owner": []}):
            with self.assertRaises(JobError):
                self.submit(manager, **kwargs)

    def test_cancel_confirmed_and_terminal_cannot_reverse(self):
        manager = self.manager()
        job = self.submit(manager)
        self.assertEqual(manager.cancel(job["id"])["state"], "cancelled")
        self.assertEqual(manager.poll(job["id"])["state"], "cancelled")

    def test_unconfirmed_cancel_is_not_cancelled(self):
        class Unconfirmed(SimulatedAdapter):
            def cancel(self, handle):
                return False
        manager = self.manager({"simulation": Unconfirmed()})
        job = self.submit(manager)
        self.assertEqual(manager.cancel(job["id"])["state"], "cancel_requested")
        self.assertEqual(manager.poll(job["id"])["state"], "succeeded")

    def test_unknown_submission_is_retained_sanitized_and_not_retried(self):
        class Lost(SimulatedAdapter):
            calls = 0
            def submit(self, request):
                self.calls += 1
                raise RuntimeError("secret-key private prompt https://private.invalid")
        adapter = Lost()
        manager = self.manager({"simulation": adapter})
        job = self.submit(manager, idempotency_key="one")
        self.assertEqual(job["state"], "unknown")
        self.assertEqual(self.submit(manager, idempotency_key="one")["id"], job["id"])
        self.assertEqual(adapter.calls, 1)
        self.assertNotIn("private", json.dumps(job))
        self.assertEqual(manager.poll(job["id"])["state"], "unknown")
        manager.reconcile(job["id"], "verified123")
        self.assertEqual(manager.poll(job["id"])["state"], "succeeded")

    def test_idempotency_rejects_changed_input(self):
        manager = self.manager()
        self.submit(manager, idempotency_key="one")
        with self.assertRaisesRegex(JobError, "different input"):
            manager.submit("simulation", "another", 4, idempotency_key="one", cost_acknowledged=True)

    def test_ownership_isolated(self):
        manager = self.manager()
        job = self.submit(manager, owner="alice", project="测试")
        for action in (manager.get, manager.poll, manager.cancel, manager.output):
            with self.assertRaises(JobError):
                action(job["id"], "bob")
        self.assertEqual(manager.list("bob"), [])
        self.assertEqual(manager.list("alice", "other"), [])

    def test_limits_are_not_reset_by_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            first = JobManager(db_path=path, daily_jobs=1)
            job = self.submit(first)
            first.cancel(job["id"])
            first.close()
            manager = JobManager(db_path=path, daily_jobs=1)
            try:
                self.assertEqual(manager.get(job["id"])["state"], "cancelled")
                with self.assertRaisesRegex(JobError, "daily"):
                    self.submit(manager)
            finally:
                manager.close()
            self.assertNotIn(b"private prompt", path.read_bytes())

    def test_interrupted_submission_recovers_as_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            manager = JobManager(db_path=path)
            job = self.submit(manager)
            with manager._lock, manager._db:
                row = manager._find(job["id"], "local")
                row.update(state="submitting", _handle="")
                manager._write(row)
            manager.close()
            manager = JobManager(db_path=path)
            try:
                self.assertEqual(manager.get(job["id"])["state"], "unknown")
            finally:
                manager.close()

    def test_slow_adapter_does_not_block_reads_or_exceed_concurrency(self):
        started, release = threading.Event(), threading.Event()
        class Slow(SimulatedAdapter):
            def submit(self, request):
                started.set()
                release.wait(3)
                return "slow1"
        manager = self.manager({"simulation": Slow()}, max_active=1)
        thread = threading.Thread(target=self.submit, args=(manager,))
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertEqual(manager.list()[0]["state"], "submitting")
            with self.assertRaisesRegex(JobError, "capacity"):
                self.submit(manager)
        finally:
            release.set()
            thread.join(3)

    def test_poll_and_cancel_race_calls_adapter_once_at_a_time(self):
        started, release = threading.Event(), threading.Event()
        class SlowPoll(SimulatedAdapter):
            def poll(self, handle):
                started.set()
                release.wait(3)
                return Poll("succeeded")
            def cancel(self, handle):
                raise AssertionError("must not race the poll")
        manager = self.manager({"simulation": SlowPoll()})
        job = self.submit(manager)
        thread = threading.Thread(target=manager.poll, args=(job["id"],))
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertEqual(manager.cancel(job["id"])["state"], "queued")
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(manager.get(job["id"])["state"], "succeeded")

    def test_poll_failures_and_timeout_keep_reserved_slot(self):
        now = [100]
        class Offline(SimulatedAdapter):
            def poll(self, handle):
                raise RuntimeError("credential-secret")
        manager = self.manager({"simulation": Offline()}, timeout=5, max_active=1, clock=lambda: now[0])
        job = self.submit(manager)
        now[0] += 10
        with self.assertRaises(JobError) as caught:
            manager.poll(job["id"])
        self.assertNotIn("credential", str(caught.exception))
        self.assertEqual(manager.get(job["id"])["state"], "timed_out")
        with self.assertRaises(JobError):
            self.submit(manager)

    def test_provider_queue_regression_is_ignored(self):
        class States(SimulatedAdapter):
            states = iter(("running", "queued", "failed", "succeeded"))
            def poll(self, handle):
                return Poll(next(self.states))
        manager = self.manager({"simulation": States()})
        job = self.submit(manager)
        self.assertEqual([manager.poll(job["id"])["state"] for _ in range(4)], ["running", "running", "failed", "failed"])

    def test_budget_and_unsupported_cancel(self):
        class Paid(SimulatedAdapter):
            capabilities = Capabilities(False, False, 10, "Paid", poll_seconds=10)
            price_cents = 50
        manager = self.manager({"simulation": Paid()}, budget_cents=50)
        job = self.submit(manager)
        with self.assertRaisesRegex(JobError, "cancellation"):
            manager.cancel(job["id"])
        manager.poll(job["id"])
        with self.assertRaisesRegex(JobError, "budget"):
            self.submit(manager)

    def test_unknown_price_and_disabled_providers_are_unavailable(self):
        class Paid(SimulatedAdapter):
            capabilities = Capabilities(False, False, 10, "Paid")
            price_cents = None
        with self.assertRaisesRegex(JobError, "price"):
            self.submit(self.manager({"simulation": Paid()}))
        adapter = SimulatedAdapter()
        adapter.enabled = False
        with self.assertRaisesRegex(JobError, "disabled"):
            self.submit(self.manager({"simulation": adapter}))
