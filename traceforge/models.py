from dataclasses import dataclass
from typing import Optional, Any


@dataclass
class Step:
    step_id: str
    run_id: str
    step_name: str
    input_data: Any
    output_data: Any
    model: Optional[str]
    tokens_input: int
    tokens_output: int
    cost_usd: float
    latency_ms: float
    started_at: float
    ended_at: Optional[float]
    parent_step_id: Optional[str]
    error: Optional[str]
    metadata: dict


@dataclass
class Run:
    run_id: str
    run_name: str
    started_at: float
    ended_at: Optional[float]
    status: str
    metadata: dict
