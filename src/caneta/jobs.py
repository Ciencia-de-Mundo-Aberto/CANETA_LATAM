"""Background jobs for slow work (AI calls, literature search, PDF download). The page polls /jobs/<id>."""

from __future__ import annotations

import secrets
import threading
import time
import traceback
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    title: str
    status: str = "running"  # running | done | error
    log: list[str] = field(default_factory=list)
    result: object = None
    error: str | None = None
    next_url: str | None = None
    started: float = field(default_factory=time.monotonic)

    def say(self, line: str) -> None:
        self.log.append(line)
        del self.log[:-200]

    @property
    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def start(title: str, fn, *args, **kwargs) -> Job:
    """Run fn(job, *args, **kwargs) in a thread. fn may set job.next_url to redirect when done."""
    job = Job(id=secrets.token_hex(6), title=title)
    with _LOCK:
        _JOBS[job.id] = job
        for old in [k for k, j in _JOBS.items() if j.status != "running" and j.elapsed > 3600]:
            _JOBS.pop(old, None)

    def run():
        try:
            job.result = fn(job, *args, **kwargs)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 - shown to the user
            job.error = f"{exc}"
            job.say(traceback.format_exc(limit=2).splitlines()[-1])
            job.status = "error"

    threading.Thread(target=run, daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    return _JOBS.get(job_id)
