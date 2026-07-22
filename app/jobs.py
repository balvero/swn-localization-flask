"""Tiny in-memory job tracker for long-running background work (currently
just "Draft all with AI"). A Flask dev/prod process is long-lived, so a
plain dict + lock is enough — no Redis/Celery needed at this scale. Not
persisted across restarts; a job that's mid-flight when the server
restarts is simply lost (acceptable for this internal tool)."""

import threading
import uuid

_jobs = {}
_lock = threading.Lock()


def create_job(total):
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {"done": 0, "total": total, "failed": 0, "status": "running", "error": None}
    return job_id


def update_job(job_id, done=None, failed=None, status=None, error=None):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        if done is not None:
            job["done"] = done
        if failed is not None:
            job["failed"] = failed
        if status is not None:
            job["status"] = status
        if error is not None:
            job["error"] = error


def get_job(job_id):
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def cleanup_job(job_id):
    with _lock:
        _jobs.pop(job_id, None)
