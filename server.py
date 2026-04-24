import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import traceforge
from traceforge.export import export_run_jsonl
from traceforge.replay import get_step_inputs

app = FastAPI(title="TraceForge", version="1.0.0")
storage = traceforge.init()

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    return Path("static/index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@app.get("/api/runs")
async def list_runs(limit: int = 50, offset: int = 0):
    return {"runs": storage.get_runs(limit=limit, offset=offset)}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    run = storage.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    steps = storage.get_steps(run_id)

    # attach children lists for tree reconstruction
    step_map = {s["step_id"]: s for s in steps}
    for s in steps:
        s["children"] = []
    roots = []
    for s in steps:
        pid = s.get("parent_step_id")
        if pid and pid in step_map:
            step_map[pid]["children"].append(s["step_id"])
        else:
            roots.append(s["step_id"])

    total_tokens = sum(
        (s.get("tokens_input") or 0) + (s.get("tokens_output") or 0)
        for s in steps
    )
    total_cost = sum(s.get("cost_usd") or 0 for s in steps)

    return {
        "run":          run,
        "steps":        steps,
        "roots":        roots,
        "total_tokens": total_tokens,
        "total_cost":   total_cost,
    }


@app.get("/api/runs/{run_id}/export", response_class=PlainTextResponse)
async def export_run(run_id: str):
    if not storage.get_run(run_id):
        raise HTTPException(404, "Run not found")
    return PlainTextResponse(
        export_run_jsonl(storage, run_id),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="trace-{run_id[:8]}.jsonl"'},
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

@app.get("/api/steps/{step_id}")
async def get_step(step_id: str):
    step = storage.get_step(step_id)
    if not step:
        raise HTTPException(404, "Step not found")
    return step


@app.get("/api/steps/{step_id}/inputs")
async def step_inputs(step_id: str):
    inputs = get_step_inputs(storage, step_id)
    if not inputs:
        raise HTTPException(404, "Step not found")
    return inputs


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/api/search")
async def search_steps(
    run_id:     Optional[str]  = Query(None),
    step_name:  Optional[str]  = Query(None),
    has_error:  Optional[bool] = Query(None),
    min_tokens: Optional[int]  = Query(None),
    limit:      int            = Query(100),
):
    steps = storage.query_steps(
        run_id=run_id,
        step_name=step_name,
        has_error=has_error,
        min_tokens=min_tokens,
        limit=limit,
    )
    return {"steps": steps}


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

@app.get("/api/compare")
async def compare_runs(run_id_a: str, run_id_b: str):
    run_a = storage.get_run(run_id_a)
    run_b = storage.get_run(run_id_b)
    if not run_a or not run_b:
        raise HTTPException(404, "One or both runs not found")

    steps_a = storage.get_steps(run_id_a)
    steps_b = storage.get_steps(run_id_b)

    by_name_a = {}
    for s in steps_a:
        by_name_a.setdefault(s["step_name"], []).append(s)
    by_name_b = {}
    for s in steps_b:
        by_name_b.setdefault(s["step_name"], []).append(s)

    all_names = sorted(set(list(by_name_a) + list(by_name_b)))
    diff = [
        {
            "step_name": name,
            "run_a":     by_name_a.get(name, [None])[0],
            "run_b":     by_name_b.get(name, [None])[0],
        }
        for name in all_names
    ]

    return {"run_a": run_a, "run_b": run_b, "diff": diff}


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

class ReplayRequest(BaseModel):
    step_id:        str
    override_input: Optional[dict] = None


@app.post("/api/replay")
async def replay_endpoint(req: ReplayRequest):
    inputs = get_step_inputs(storage, req.step_id)
    if not inputs:
        raise HTTPException(404, "Step not found")

    effective_input = req.override_input if req.override_input is not None else inputs["input_data"]

    return {
        "step_id":         req.step_id,
        "step_name":       inputs["step_name"],
        "model":           inputs["model"],
        "input_data":      effective_input,
        "original_output": inputs["original_output"],
        "instructions":    "Pass input_data to your LLM client with the specified model to replay this step.",
    }
