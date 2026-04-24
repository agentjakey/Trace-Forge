import uuid
import time
import functools
import threading
from contextlib import contextmanager
from typing import Optional, Any, Callable

from .models import Step, Run
from .storage import TraceStorage

_storage: Optional[TraceStorage] = None
_thread_local = threading.local()


def init(db_path: str = "traceforge.db") -> TraceStorage:
    global _storage
    _storage = TraceStorage(db_path)
    return _storage


def get_storage() -> TraceStorage:
    global _storage
    if _storage is None:
        _storage = TraceStorage()
    return _storage


def _current_run_id() -> Optional[str]:
    return getattr(_thread_local, "run_id", None)


def _current_step_id() -> Optional[str]:
    stack = getattr(_thread_local, "step_stack", [])
    return stack[-1] if stack else None


def _push_step(step_id: str):
    if not hasattr(_thread_local, "step_stack"):
        _thread_local.step_stack = []
    _thread_local.step_stack.append(step_id)


def _pop_step():
    stack = getattr(_thread_local, "step_stack", [])
    if stack:
        stack.pop()


@contextmanager
def trace_run(run_name: str, metadata: dict = None):
    """Context manager that wraps a complete agent pipeline run."""
    storage = get_storage()
    run_id = str(uuid.uuid4())
    started_at = time.time()

    _thread_local.run_id = run_id
    _thread_local.step_stack = []

    run = Run(
        run_id=run_id,
        run_name=run_name,
        started_at=started_at,
        ended_at=None,
        status="running",
        metadata=metadata or {},
    )
    storage.save_run(run)

    try:
        yield run_id
        storage.update_run(run_id, time.time(), "success")
    except Exception:
        storage.update_run(run_id, time.time(), "error")
        raise
    finally:
        _thread_local.run_id = None
        _thread_local.step_stack = []


@contextmanager
def trace_step(
    step_name: str,
    input_data: Any = None,
    model: str = None,
    metadata: dict = None,
    run_id: str = None,
    cost_per_input_token: float = 0.0,
    cost_per_output_token: float = 0.0,
):
    """Context manager that records a single LLM or tool call.

    Yields a result_holder dict. Caller sets:
        result_holder["output"]         - the step output (any JSON-serializable value)
        result_holder["tokens_input"]   - input token count
        result_holder["tokens_output"]  - output token count
        result_holder["model"]          - model name override
    """
    storage = get_storage()
    step_id = str(uuid.uuid4())

    effective_run_id = run_id or _current_run_id()
    if not effective_run_id:
        # auto-create a run if called outside trace_run
        effective_run_id = str(uuid.uuid4())
        auto_run = Run(
            run_id=effective_run_id,
            run_name="auto",
            started_at=time.time(),
            ended_at=None,
            status="running",
            metadata={},
        )
        storage.save_run(auto_run)

    parent_step_id = _current_step_id()
    started_at = time.time()

    step = Step(
        step_id=step_id,
        run_id=effective_run_id,
        step_name=step_name,
        input_data=input_data,
        output_data=None,
        model=model,
        tokens_input=0,
        tokens_output=0,
        cost_usd=0.0,
        latency_ms=0.0,
        started_at=started_at,
        ended_at=None,
        parent_step_id=parent_step_id,
        error=None,
        metadata=metadata or {},
    )
    storage.save_step(step)
    _push_step(step_id)

    result_holder: dict = {"output": None, "tokens_input": 0, "tokens_output": 0, "model": model}

    try:
        yield result_holder
        ended_at = time.time()
        step.output_data = result_holder.get("output")
        step.tokens_input = result_holder.get("tokens_input", 0) or 0
        step.tokens_output = result_holder.get("tokens_output", 0) or 0
        step.model = result_holder.get("model") or model
        step.latency_ms = (ended_at - started_at) * 1000
        step.ended_at = ended_at
        step.cost_usd = (
            step.tokens_input * cost_per_input_token
            + step.tokens_output * cost_per_output_token
        )
        storage.save_step(step)
    except Exception as exc:
        ended_at = time.time()
        step.error = str(exc)
        step.latency_ms = (ended_at - started_at) * 1000
        step.ended_at = ended_at
        storage.save_step(step)
        raise
    finally:
        _pop_step()


def trace_step_decorator(
    step_name: str = None,
    model: str = None,
    metadata: dict = None,
    cost_per_input_token: float = 0.0,
    cost_per_output_token: float = 0.0,
):
    """Decorator factory: @trace_step_decorator() wraps a function as a traced step."""
    def decorator(func: Callable):
        name = step_name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            input_data = {
                "args": [repr(a) for a in args],
                "kwargs": {k: repr(v) for k, v in kwargs.items()},
            }
            with trace_step(
                name,
                input_data=input_data,
                model=model,
                metadata=metadata,
                cost_per_input_token=cost_per_input_token,
                cost_per_output_token=cost_per_output_token,
            ) as r:
                result = func(*args, **kwargs)
                r["output"] = (
                    result
                    if isinstance(result, (str, dict, list, int, float, bool, type(None)))
                    else repr(result)
                )
                return result

        return wrapper

    return decorator
