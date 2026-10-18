"""Local cron output policy. Results are persisted by the firing scheduler."""
from __future__ import annotations


def _normalize_deliver_value(value) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item).strip() for item in value if str(item).strip()).lower() or "local"
    return str(value or "local").strip().lower() or "local"


def _delivery_lane_value(job: dict, *, for_failure: bool = False):
    if for_failure and job.get("failure_deliver"):
        return job["failure_deliver"]
    return job.get("deliver", "local")


def local_delivery_error(job: dict) -> str | None:
    for key in ("deliver", "failure_deliver"):
        value = _normalize_deliver_value(job.get(key))
        if value != "local":
            return f"{key}={value!r} is unsupported: terminal cron saves results locally; use 'local'."
    if job.get("attach_to_session"):
        return "attach_to_session is unsupported: terminal cron results are stored locally."
    return None
