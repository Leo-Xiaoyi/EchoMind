"""Request-scoped timings and provider-reported token usage; no prompt content."""
import copy
import time
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar("echomind_performance", default=None)
_totals = {}
_token_fields = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _aggregate(rows):
    result = {"calls": len(rows), "errors": sum(row["status"] != "success" for row in rows),
              "latency_ms": round(sum(row["latency_ms"] for row in rows), 1),
              "usage_reported_calls": sum(row.get("input_tokens") is not None for row in rows)}
    result.update({field: sum(row.get(field) or 0 for row in rows) for field in _token_fields})
    return result


@contextmanager
def collect_performance():
    trace = {"stages_ms": {}, "llm_calls": [], "events": {}}
    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)


@contextmanager
def measure_stage(name):
    trace = _current.get()
    started = time.monotonic()
    try:
        yield
    finally:
        if trace is not None:
            stages = trace["stages_ms"]
            stages[name] = stages.get(name, 0.0) + (time.monotonic() - started) * 1000


def record_event(name):
    trace = _current.get()
    if trace is not None:
        trace["events"][name] = trace["events"].get(name, 0) + 1


def record_llm_call(stage, model, started, status, response=None):
    row = {"stage": stage, "model": model, "status": status,
           "latency_ms": round((time.monotonic() - started) * 1000, 1)}
    usage = getattr(response, "usage", None)
    for field in _token_fields:
        value = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
        row[field] = value if type(value) is int and value >= 0 else None
    trace = _current.get()
    if trace is not None:
        trace["llm_calls"].append(row)
    values = _aggregate([row])
    totals = _totals.setdefault(stage, {key: 0 for key in values})
    for key, value in values.items():
        totals[key] += value


def performance_snapshot():
    trace = _current.get()
    if trace is None:
        return {}
    result = copy.deepcopy(trace)
    result["stages_ms"] = {key: round(value, 1) for key, value in result["stages_ms"].items()}
    result["llm"] = _aggregate(result["llm_calls"])
    return result


def llm_usage_summary():
    return {"scope": "process", "by_stage": copy.deepcopy(_totals),
            "note": "Logical SDK calls; token fields are provider-reported. SDK internal retries and unreported usage are not estimated."}
