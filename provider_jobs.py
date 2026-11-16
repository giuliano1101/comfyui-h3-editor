"""Durable provider jobs. Network calls never run under the database lock.

Modified 2026-11-17 for the Windows provider fork (GPL-3.0).
Only the simulation is enabled without explicit server configuration. Prompts,
credentials, provider errors and signed result URLs are never persisted.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sqlite3
from threading import RLock, Lock
import time
from uuid import uuid4


class JobError(ValueError):
    def __init__(self, message, code="invalid_request", status=400):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Capabilities:
    simulated: bool
    cancellation: bool
    max_seconds: int
    cost_notice: str
    models: tuple = ("simulation",)
    resolutions: tuple = ("test",)
    durations: tuple = tuple(range(1, 11))
    inputs: tuple = ("text",)
    outputs: tuple = ()
    auth: str = "none"
    protocol: str = "simulation-v1"
    poll_seconds: int = 0
    live_verified: bool = False


@dataclass(frozen=True)
class Generation:
    prompt: str
    seconds: int
    model: str
    resolution: str


@dataclass(frozen=True)
class Poll:
    state: str
    file_id: str = ""


class SimulatedAdapter:
    """No sockets, media or billing. Safe to exercise without any provider key."""
    capabilities = Capabilities(True, True, 10, "Simulation only: no video and no charge.")
    configured = True
    enabled = True
    price_cents = 0

    def validate(self, request):
        pass

    def quote(self, request):
        return self.price_cents

    def submit(self, request):
        return uuid4().hex

    def poll(self, handle):
        return Poll("succeeded")

    def cancel(self, handle):
        return True


TERMINAL = frozenset(("succeeded", "failed", "cancelled"))
ACTIVE = frozenset(("submitting", "unknown", "queued", "running", "cancel_requested", "timed_out"))
IDENTIFIER = re.compile(r"^[\w.-]{1,128}$", re.UNICODE)
HANDLE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def identifier(value, label="identifier"):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value) or value in (".", ".."):
        raise JobError("invalid " + label)
    return value


def remote_handle(value):
    if not isinstance(value, str) or not HANDLE.fullmatch(value):
        raise JobError("invalid provider task identifier")
    return value


class JobManager:
    """One manager per server; SQLite transactions also serialize multiple threads.

    Submitting is recorded *before* contacting a provider. An interrupted submission
    recovers as unknown, never as a fresh request. Unknown/time-out jobs retain their
    active slot and budget: stopping our wait does not cancel external billing.
    """
    def __init__(self, adapters=None, max_jobs=1000, max_active=2, db_path=":memory:",
                 per_user_active=2, daily_jobs=20, budget_cents=0, timeout=1800, clock=time.time):
        self._adapters = dict(adapters if adapters is not None else {"simulation": SimulatedAdapter()})
        self._lock, self._flight = RLock(), {}
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=DELETE")
        self._db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, idem TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(owner, idem))")
        self._max_jobs, self._max_active = max_jobs, max_active
        self._per_user_active, self._daily_jobs = per_user_active, daily_jobs
        self._budget_cents, self._timeout, self._clock = budget_cents, timeout, clock
        with self._lock, self._db:
            for job in self._all():
                if job["state"] == "submitting":
                    job.update(state="unknown", notice="Submission interrupted. Reconcile the provider account; do not resubmit.")
                    self._write(job)

    def close(self):
        with self._lock:
            self._db.close()

    def capabilities(self):
        return {name: dict(asdict(a.capabilities), configured=bool(a.configured), enabled=bool(a.enabled),
                           estimate_cents=a.price_cents, quotes_cents=getattr(a, "quotes_cents", {}),
                           available=bool(a.configured and a.enabled and (a.capabilities.simulated or
                               any(type(v) is int and 0 < v <= self._budget_cents for v in
                                   list(getattr(a, "quotes_cents", {}).values()) + [a.price_cents]))),
                           service=getattr(a, "service_label", "simulation"),
                           status="simulation" if a.capabilities.simulated else "implemented; live API unverified")
                for name, a in self._adapters.items()}

    def validate(self, provider, prompt, seconds, model=None, resolution=None):
        if not isinstance(provider, str) or provider not in self._adapters:
            raise JobError("unknown provider")
        adapter = self._adapters[provider]
        cap = adapter.capabilities
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
            raise JobError("prompt must contain 1 to 2000 characters")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds not in cap.durations or seconds > cap.max_seconds:
            raise JobError("duration outside provider limits")
        model, resolution = model or cap.models[0], resolution or cap.resolutions[0]
        if model not in cap.models or resolution not in cap.resolutions:
            raise JobError("unsupported model or resolution")
        request = Generation(prompt, seconds, model, resolution)
        adapter.validate(request)
        if not adapter.enabled or not adapter.configured:
            raise JobError("provider disabled or credential not configured", "unavailable", 409)
        quote = adapter.quote(request)
        if not cap.simulated and (type(quote) is not int or quote <= 0):
            raise JobError("verified price is unknown; paid submission disabled", "unknown_price", 409)
        return adapter, request

    def submit(self, provider, prompt, seconds, *, model=None, resolution=None, cost_acknowledged=False,
               owner="local", idempotency_key=None, project="", clip=""):
        identifier(owner, "connection identity")
        adapter, request = self.validate(provider, prompt, seconds, model, resolution)
        if cost_acknowledged is not True:
            raise JobError("acknowledge the provider cost notice first", "confirmation_required", 409)
        if project:
            identifier(project, "project")
        if clip:
            identifier(clip, "clip")
        key = identifier(idempotency_key or uuid4().hex, "idempotency key")
        idem = hashlib.sha256(key.encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps([provider, asdict(request), project, clip], sort_keys=True).encode()).hexdigest()
        with self._lock, self._db:
            rows = self._all()
            for job in rows:
                if job["owner"] == owner and job["_idem"] == idem:
                    if job["_fingerprint"] != fingerprint:
                        raise JobError("idempotency key already used for different input", "conflict", 409)
                    return self._public(job)
            active = [j for j in rows if j["state"] in ACTIVE]
            if len(rows) >= self._max_jobs or len(active) >= self._max_active or sum(j["owner"] == owner for j in active) >= self._per_user_active:
                raise JobError("job capacity reached; reconcile active jobs", "quota", 429)
            today = int(self._clock() // 86400)
            daily = [j for j in rows if int(j["created"] // 86400) == today]
            if sum(j["owner"] == owner for j in daily) >= self._daily_jobs:
                raise JobError("daily connection quota reached", "quota", 429)
            price = adapter.quote(request)
            # Never release a reservation merely because polling failed or a cancellation occurred.
            if price and sum(j["reserved_cents"] for j in rows) + price > self._budget_cents:
                raise JobError("configured lifetime reservation budget reached", "budget", 429)
            job = dict(id=uuid4().hex, owner=owner, provider=provider, model=request.model,
                       resolution=request.resolution, seconds=seconds, project=project, clip=clip,
                       state="submitting", created=self._clock(), updated=self._clock(),
                       reserved_cents=price, simulated=adapter.capabilities.simulated,
                       has_output=False, staged=False, notice="", _handle="", _file_id="",
                       _idem=idem, _fingerprint=fingerprint, _last_poll=0)
            self._write(job)
        # No global lock across a potentially slow network operation.
        try:
            handle = remote_handle(adapter.submit(request))
        except Exception:
            with self._lock, self._db:
                job.update(state="unknown", updated=self._clock(), notice="Submission outcome unknown. Do not resubmit; reconcile the provider account.")
                self._write(job)
            return self._public(job)
        with self._lock, self._db:
            job.update(state="queued", _handle=handle, updated=self._clock())
            self._write(job)
            return self._public(job)

    def _all(self):
        return [json.loads(r[0]) for r in self._db.execute("SELECT data FROM jobs ORDER BY rowid")]

    def _write(self, job):
        self._db.execute("INSERT INTO jobs VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                         (job["id"], job["owner"], job["_idem"], json.dumps(job)))

    def _find(self, job_id, owner):
        if not isinstance(job_id, str):
            raise JobError("unknown job", "not_found", 404)
        row = self._db.execute("SELECT data FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        if not row:
            raise JobError("unknown job", "not_found", 404)
        return json.loads(row[0])

    @staticmethod
    def _public(job):
        return {k: v for k, v in job.items() if not k.startswith("_") and k != "owner"}

    def list(self, owner="local", project=None):
        with self._lock:
            return [self._public(j) for j in self._all() if j["owner"] == owner and (project is None or j["project"] == project)][-100:][::-1]

    def usage(self, owner="local"):
        with self._lock:
            rows = self._all()
            reserved = sum(j["reserved_cents"] for j in rows)
            today = int(self._clock() // 86400)
            return {"budget_cents": self._budget_cents, "reserved_cents": reserved,
                    "remaining_cents": max(0, self._budget_cents - reserved),
                    "active": sum(j["state"] in ACTIVE for j in rows), "max_active": self._max_active,
                    "connection_active": sum(j["state"] in ACTIVE and j["owner"] == owner for j in rows),
                    "connection_max_active": self._per_user_active,
                    "connection_today": sum(j["owner"] == owner and int(j["created"] // 86400) == today for j in rows),
                    "connection_daily_limit": self._daily_jobs}

    def get(self, job_id, owner="local"):
        with self._lock:
            return self._public(self._find(job_id, owner))

    def _call(self, job_id, owner, cancel=False):
        with self._lock:
            job = self._find(job_id, owner)
            if job["state"] in TERMINAL or not job["_handle"]:
                return self._public(job)
            adapter = self._adapters.get(job["provider"])
            if adapter is None or not adapter.configured or not adapter.enabled:
                raise JobError("provider unavailable; job retained", "unavailable", 409)
            if cancel and not adapter.capabilities.cancellation:
                raise JobError("provider does not support cancellation; charges may continue", "unsupported", 409)
            flight = self._flight.setdefault(job_id, Lock())
        if not flight.acquire(blocking=False):
            return self.get(job_id, owner)
        try:
            with self._lock, self._db:
                job = self._find(job_id, owner)
                if job["state"] in TERMINAL:
                    return self._public(job)
                if not cancel and self._clock() - job["_last_poll"] < adapter.capabilities.poll_seconds:
                    return self._public(job)
                job["_last_poll"] = self._clock()
                self._write(job)
            try:
                if cancel:
                    result = Poll("cancelled" if adapter.cancel(job["_handle"]) is True else "cancel_requested")
                else:
                    result = adapter.poll(job["_handle"])
                    if isinstance(result, str):
                        result = Poll(result)
                if result.state not in ("queued", "running", "succeeded", "failed", "cancelled", "cancel_requested"):
                    raise ValueError()
                if result.file_id:
                    remote_handle(result.file_id)
            except Exception:
                with self._lock, self._db:
                    job = self._find(job_id, owner)
                    job["notice"] = "Cancellation unconfirmed; poll again. Charges may continue." if cancel else "Provider status unavailable; poll again. Do not submit again."
                    if self._clock() - job["created"] > self._timeout:
                        job["state"] = "timed_out"
                    self._write(job)
                raise JobError(job["notice"], "provider_unavailable", 502) from None
            with self._lock, self._db:
                job = self._find(job_id, owner)
                if job["state"] not in TERMINAL:
                    # Providers sometimes return a stale queue state; never regress a running task.
                    next_state = result.state
                    if next_state == "queued" and job["state"] in ("running", "cancel_requested"):
                        next_state = job["state"]
                    if job["state"] == "cancel_requested" and next_state in ("queued", "running"):
                        next_state = "cancel_requested"
                    if next_state not in TERMINAL and self._clock() - job["created"] > self._timeout:
                        next_state = "timed_out"
                    job.update(state=next_state, updated=self._clock(), _file_id=result.file_id or job["_file_id"],
                               has_output=bool(result.file_id or job["_file_id"]), notice="")
                    if next_state in ("timed_out", "cancel_requested"):
                        job["notice"] = "Provider outcome not confirmed. Charges may continue; keep polling."
                    self._write(job)
                return self._public(job)
        finally:
            flight.release()

    def poll(self, job_id, owner="local"):
        return self._call(job_id, owner)

    def cancel(self, job_id, owner="local"):
        return self._call(job_id, owner, True)

    def reconcile(self, job_id, handle, owner="local"):
        """Attach an account-verified task ID; this is not a resubmission or refund."""
        handle = remote_handle(handle)
        with self._lock, self._db:
            job = self._find(job_id, owner)
            if job["state"] != "unknown" or job["_handle"]:
                raise JobError("only unknown submissions can be reconciled", "conflict", 409)
            if any(j["provider"] == job["provider"] and j["_handle"] == handle for j in self._all()):
                raise JobError("provider task already tracked", "conflict", 409)
            job.update(_handle=handle, state="queued", notice="", updated=self._clock())
            self._write(job)
            return self._public(job)

    def output(self, job_id, owner="local"):
        with self._lock:
            job = self._find(job_id, owner)
            if job["state"] != "succeeded" or not job["_file_id"]:
                raise JobError("job has no downloadable output", "unavailable", 409)
            return self._adapters[job["provider"]], job["_file_id"]

    def mark_staged(self, job_id, owner="local"):
        with self._lock, self._db:
            job = self._find(job_id, owner)
            job["staged"] = True
            self._write(job)
