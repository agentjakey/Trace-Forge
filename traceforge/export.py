import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .storage import TraceStorage


def _step_to_otel_span(step: dict) -> dict:
    """Convert a step dict to an OpenTelemetry-compatible span dict."""
    trace_id = step["run_id"].replace("-", "")[:32].ljust(32, "0")
    span_id = step["step_id"].replace("-", "")[:16].ljust(16, "0")

    started_ns = int(step["started_at"] * 1e9)
    ended_ns = int((step["ended_at"] or step["started_at"]) * 1e9)

    attributes = [
        {"key": "llm.model",              "value": {"stringValue": str(step.get("model") or "")}},
        {"key": "llm.tokens.input",       "value": {"intValue": str(step.get("tokens_input", 0))}},
        {"key": "llm.tokens.output",      "value": {"intValue": str(step.get("tokens_output", 0))}},
        {"key": "llm.cost_usd",           "value": {"doubleValue": step.get("cost_usd", 0.0)}},
        {"key": "traceforge.step_name",   "value": {"stringValue": step["step_name"]}},
        {"key": "traceforge.run_id",      "value": {"stringValue": step["run_id"]}},
        {"key": "traceforge.latency_ms",  "value": {"doubleValue": step.get("latency_ms", 0.0)}},
    ]
    if step.get("error"):
        attributes.append({"key": "error.message", "value": {"stringValue": step["error"]}})

    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "operationName": step["step_name"],
        "startTimeUnixNano": str(started_ns),
        "endTimeUnixNano": str(ended_ns),
        "attributes": attributes,
        "status": {
            "code": "STATUS_CODE_ERROR" if step.get("error") else "STATUS_CODE_OK"
        },
    }

    if step.get("parent_step_id"):
        span["parentSpanId"] = step["parent_step_id"].replace("-", "")[:16].ljust(16, "0")

    return span


def export_run_jsonl(storage: "TraceStorage", run_id: str) -> str:
    """Return JSONL string of OTel spans for a single run."""
    steps = storage.get_steps(run_id)
    return "\n".join(json.dumps(_step_to_otel_span(s)) for s in steps)


def export_all_jsonl(storage: "TraceStorage") -> str:
    """Return JSONL string of OTel spans for all runs."""
    runs = storage.get_runs(limit=1000)
    lines = []
    for run in runs:
        for step in storage.get_steps(run["run_id"]):
            lines.append(json.dumps(_step_to_otel_span(step)))
    return "\n".join(lines)
