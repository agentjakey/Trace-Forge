"""
TraceForge demo: a simulated multi-step research agent.

Pipeline: web_search x3 -> extract_facts -> summarize (with nested sub-steps) -> fact_check -> format_report

Runs in MOCK mode by default (no API key needed).
Set ANTHROPIC_API_KEY to use real Claude calls.
"""
import os
import random
import time

import traceforge
from traceforge import trace_run, trace_step

MOCK = not os.environ.get("ANTHROPIC_API_KEY")

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

_SEARCH_RESULTS = {
    "climate change": [
        "Global average temperature has risen 1.1 degrees Celsius since pre-industrial times.",
        "Arctic sea ice extent has declined roughly 13 percent per decade since 1979.",
        "2023 was the hottest year on record globally according to multiple datasets.",
        "Sea levels are rising at approximately 3.6 mm per year, accelerating since 1990.",
        "Extreme weather events have increased in frequency and intensity over the past 50 years.",
    ],
    "quantum computing": [
        "IBM unveiled a 1,121-qubit quantum processor called Condor in late 2023.",
        "Google claimed quantum supremacy in 2019 with a 53-qubit Sycamore processor.",
        "Quantum error correction remains the central unsolved engineering challenge.",
        "Microsoft is pursuing topological qubits to achieve inherent error resistance.",
        "The global quantum computing market is projected to reach 450 billion dollars by 2030.",
    ],
    "large language models": [
        "GPT-4 was trained on an estimated 1 trillion tokens of text and code.",
        "Scaling laws suggest model capability improves predictably with compute and data.",
        "Retrieval-augmented generation reduces hallucination rates significantly.",
        "Mixture-of-experts architectures allow large models with lower inference cost.",
        "Constitutional AI methods align model behavior without requiring human labels.",
    ],
}
_DEFAULT_RESULTS = [
    "Recent peer-reviewed studies show measurable advances across multiple dimensions.",
    "Researchers have identified key structural factors with broad downstream effects.",
    "New methodologies have improved reproducibility and reduced error rates by 35 percent.",
    "Adoption has accelerated following regulatory clarity in major markets.",
]

_SUMMARIES = [
    "The evidence presents a compelling picture of rapid change with clear quantitative benchmarks. "
    "Multiple independent research groups corroborate the core findings, lending high confidence to "
    "the central conclusions. Continued monitoring will be essential to track divergence from models.",
    "Analysis of the available data reveals consistent trends across geographies and time horizons. "
    "The field has matured significantly, with practical applications now demonstrating real-world "
    "impact at scale. Key uncertainties remain around long-term trajectories and edge cases.",
]

_FACT_CHECKS = [
    {"verified": True,  "confidence": 0.93, "issues": []},
    {"verified": True,  "confidence": 0.81, "issues": ["one statistic could not be independently sourced"]},
    {"verified": False, "confidence": 0.52, "issues": ["projected figure appears optimistic", "date range is ambiguous"]},
]


# ---------------------------------------------------------------------------
# LLM interface
# ---------------------------------------------------------------------------

def _mock_llm(prompt: str, model: str, max_tokens: int) -> dict:
    time.sleep(random.uniform(0.2, 0.9))
    text = random.choice(_SUMMARIES)
    return {
        "content": text,
        "model": model,
        "usage": {
            "input_tokens":  max(10, len(prompt.split())),
            "output_tokens": max(10, len(text.split())),
        },
    }


def _real_llm(prompt: str, model: str, max_tokens: int) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return {
        "content": msg.content[0].text,
        "model": msg.model,
        "usage": {
            "input_tokens":  msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        },
    }


def llm(prompt: str, model: str = "claude-haiku-4-5-20251001", max_tokens: int = 512) -> dict:
    if MOCK:
        return _mock_llm(prompt, model, max_tokens)
    return _real_llm(prompt, model, max_tokens)


def web_search(query: str) -> list:
    time.sleep(random.uniform(0.05, 0.3))
    results = _SEARCH_RESULTS.get(query.lower())
    if not results:
        for key in _SEARCH_RESULTS:
            if key in query.lower():
                results = _SEARCH_RESULTS[key]
                break
    if not results:
        results = _DEFAULT_RESULTS
    count = random.randint(3, len(results))
    return results[:count]


# ---------------------------------------------------------------------------
# Agent pipeline
# ---------------------------------------------------------------------------

def research_agent(topic: str) -> str:
    """Full pipeline: search -> extract -> summarize -> fact_check -> format."""
    print(f"\n[demo] Running research agent: {topic!r}  (mock={MOCK})")

    cost_in  = 0.0 if MOCK else 3e-7    # claude-haiku input  $/token
    cost_out = 0.0 if MOCK else 1.25e-6 # claude-haiku output $/token

    with trace_run(f"research: {topic}", metadata={"topic": topic, "mock": MOCK}) as run_id:
        print(f"[demo] run_id={run_id}")

        # ---- 1. Web search (three parallel-style queries) ------------------
        all_results = []
        queries = [topic, f"{topic} recent data", f"{topic} future outlook"]
        for q in queries:
            with trace_step("web_search", input_data={"query": q}) as r:
                hits = web_search(q)
                r["output"] = {"results": hits, "count": len(hits)}
            all_results.extend(hits)

        all_results = list(dict.fromkeys(all_results))  # deduplicate, preserve order

        # ---- 2. Extract key facts (fast model) -----------------------------
        facts_prompt = (
            f'From these search results about "{topic}", extract the 5 most important facts '
            f'as a numbered list. Be specific and include numbers where present.\n\n'
            + "\n".join(f"- {r}" for r in all_results)
        )
        with trace_step(
            "extract_facts",
            input_data={"topic": topic, "result_count": len(all_results)},
            model="claude-haiku-4-5-20251001",
            cost_per_input_token=cost_in,
            cost_per_output_token=cost_out,
        ) as r:
            resp = llm(facts_prompt, "claude-haiku-4-5-20251001", 256)
            facts = [
                ln.strip().lstrip("0123456789.-) ")
                for ln in resp["content"].splitlines()
                if ln.strip()
            ][:5]
            r["output"]         = {"facts": facts}
            r["tokens_input"]   = resp["usage"]["input_tokens"]
            r["tokens_output"]  = resp["usage"]["output_tokens"]
            r["model"]          = resp["model"]

        # ---- 3. Summarize with nested draft + refine -----------------------
        cost_in_s  = 0.0 if MOCK else 3e-6   # claude-sonnet
        cost_out_s = 0.0 if MOCK else 1.5e-5

        with trace_step(
            "summarize",
            input_data={"fact_count": len(facts)},
            model="claude-sonnet-4-6",
            cost_per_input_token=cost_in_s,
            cost_per_output_token=cost_out_s,
        ) as r_sum:
            draft_prompt = (
                f'Write a 2-paragraph summary about "{topic}" based on:\n'
                + "\n".join(f"- {f}" for f in facts)
            )
            with trace_step(
                "draft_summary",
                input_data={"prompt_len": len(draft_prompt)},
                cost_per_input_token=cost_in_s,
                cost_per_output_token=cost_out_s,
            ) as r_draft:
                resp_d = llm(draft_prompt, "claude-sonnet-4-6", 400)
                draft = resp_d["content"]
                r_draft["output"]        = {"preview": draft[:120] + "..."}
                r_draft["tokens_input"]  = resp_d["usage"]["input_tokens"]
                r_draft["tokens_output"] = resp_d["usage"]["output_tokens"]
                r_draft["model"]         = resp_d["model"]

            refine_prompt = f"Improve this summary for clarity and precision:\n\n{draft}"
            with trace_step(
                "refine_summary",
                input_data={"draft_len": len(draft)},
                cost_per_input_token=cost_in_s,
                cost_per_output_token=cost_out_s,
            ) as r_ref:
                resp_r = llm(refine_prompt, "claude-sonnet-4-6", 400)
                refined = resp_r["content"]
                r_ref["output"]        = {"preview": refined[:120] + "..."}
                r_ref["tokens_input"]  = resp_r["usage"]["input_tokens"]
                r_ref["tokens_output"] = resp_r["usage"]["output_tokens"]
                r_ref["model"]         = resp_r["model"]

            r_sum["output"]        = {"summary_preview": refined[:200] + "..."}
            r_sum["tokens_input"]  = resp_d["usage"]["input_tokens"] + resp_r["usage"]["input_tokens"]
            r_sum["tokens_output"] = resp_d["usage"]["output_tokens"] + resp_r["usage"]["output_tokens"]
            r_sum["model"]         = "claude-sonnet-4-6"

        # ---- 4. Fact-check -------------------------------------------------
        fc_prompt = (
            f'For each fact about "{topic}", assess accuracy (0-1 confidence):\n'
            + "\n".join(f"{i+1}. {f}" for i, f in enumerate(facts))
        )
        with trace_step(
            "fact_check",
            input_data={"fact_count": len(facts)},
            model="claude-sonnet-4-6",
            cost_per_input_token=cost_in_s,
            cost_per_output_token=cost_out_s,
        ) as r:
            resp_fc = llm(fc_prompt, "claude-sonnet-4-6", 256)
            check = random.choice(_FACT_CHECKS)
            r["output"]        = check
            r["tokens_input"]  = resp_fc["usage"]["input_tokens"]
            r["tokens_output"] = resp_fc["usage"]["output_tokens"]
            r["model"]         = resp_fc["model"]

        # ---- 5. Format final report ----------------------------------------
        fmt_prompt = (
            f'Write a concise research brief about "{topic}".\n'
            f"Summary: {refined[:400]}\n"
            f"Fact-check: confidence={check['confidence']}, verified={check['verified']}\n"
            "Include: executive summary, key findings, confidence note."
        )
        with trace_step(
            "format_report",
            input_data={
                "summary_len": len(refined),
                "fact_check":  check,
            },
            model="claude-haiku-4-5-20251001",
            cost_per_input_token=cost_in,
            cost_per_output_token=cost_out,
        ) as r:
            resp_fmt = llm(fmt_prompt, "claude-haiku-4-5-20251001", 512)
            report = resp_fmt["content"]
            r["output"]        = {"word_count": len(report.split()), "preview": report[:300] + "..."}
            r["tokens_input"]  = resp_fmt["usage"]["input_tokens"]
            r["tokens_output"] = resp_fmt["usage"]["output_tokens"]
            r["model"]         = resp_fmt["model"]

        print(f"[demo] done  run_id={run_id}")
        return run_id


def error_demo(topic: str) -> str:
    """Run that intentionally fails one step to demonstrate error traces."""
    print(f"\n[demo] Running error-demo agent: {topic!r}")

    with trace_run(f"error-demo: {topic}") as run_id:
        with trace_step("web_search", input_data={"query": topic}) as r:
            hits = web_search(topic)
            r["output"] = {"results": hits}

        try:
            with trace_step("broken_step", input_data={"topic": topic}) as r:
                time.sleep(0.15)
                raise RuntimeError(f"Upstream API rate-limit exceeded for query: {topic!r}")
        except RuntimeError:
            pass  # error is recorded; pipeline continues with fallback

        with trace_step(
            "fallback_summary",
            input_data={"topic": topic, "reason": "upstream_error"},
            model="claude-haiku-4-5-20251001",
        ) as r:
            time.sleep(random.uniform(0.2, 0.6))
            r["output"]        = {"summary": "Generated via fallback path; upstream error occurred."}
            r["tokens_input"]  = 142
            r["tokens_output"] = 64
            r["model"]         = "claude-haiku-4-5-20251001"

        return run_id


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    traceforge.init("traceforge.db")

    research_agent("climate change")
    research_agent("quantum computing")
    research_agent("large language models")
    error_demo("renewable energy")

    print("\n[demo] All runs complete.")
    print("[demo] Start the UI:  uvicorn server:app --reload")
    print("[demo] Then open:     http://localhost:8000")
