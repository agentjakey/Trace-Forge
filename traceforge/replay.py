import time
from typing import Optional, Callable, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .storage import TraceStorage


def get_step_inputs(storage: "TraceStorage", step_id: str) -> Optional[dict]:
    """Return everything needed to replay a step: its input, model, and original output."""
    step = storage.get_step(step_id)
    if not step:
        return None
    return {
        "step_id":              step["step_id"],
        "step_name":            step["step_name"],
        "run_id":               step["run_id"],
        "model":                step["model"],
        "input_data":           step["input_data"],
        "metadata":             step["metadata"],
        "original_output":      step["output_data"],
        "original_tokens_in":   step["tokens_input"],
        "original_tokens_out":  step["tokens_output"],
        "original_latency_ms":  step["latency_ms"],
    }


def replay_step(
    storage: "TraceStorage",
    step_id: str,
    executor: Callable[[Any], Any],
    override_input: Any = None,
) -> dict:
    """Re-run a step in isolation using the provided executor function.

    executor receives the input_data and should return the new output.
    override_input replaces the recorded input_data when provided.
    """
    inputs = get_step_inputs(storage, step_id)
    if not inputs:
        raise ValueError(f"Step {step_id} not found")

    effective_input = override_input if override_input is not None else inputs["input_data"]
    started_at = time.time()

    try:
        result = executor(effective_input)
        latency_ms = (time.time() - started_at) * 1000
        return {
            "success":         True,
            "step_name":       inputs["step_name"],
            "input_data":      effective_input,
            "output":          result,
            "latency_ms":      latency_ms,
            "original_output": inputs["original_output"],
        }
    except Exception as exc:
        latency_ms = (time.time() - started_at) * 1000
        return {
            "success":         False,
            "step_name":       inputs["step_name"],
            "input_data":      effective_input,
            "error":           str(exc),
            "latency_ms":      latency_ms,
            "original_output": inputs["original_output"],
        }
