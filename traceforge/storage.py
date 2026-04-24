import sqlite3
import json
import threading
from typing import Optional, List

from .models import Step, Run


class TraceStorage:
    def __init__(self, db_path: str = "traceforge.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id      TEXT PRIMARY KEY,
                run_name    TEXT NOT NULL,
                started_at  REAL NOT NULL,
                ended_at    REAL,
                status      TEXT NOT NULL DEFAULT 'running',
                metadata    TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS steps (
                step_id        TEXT PRIMARY KEY,
                run_id         TEXT NOT NULL,
                parent_step_id TEXT,
                step_name      TEXT NOT NULL,
                model          TEXT,
                input_data     TEXT NOT NULL,
                output_data    TEXT,
                tokens_input   INTEGER NOT NULL DEFAULT 0,
                tokens_output  INTEGER NOT NULL DEFAULT 0,
                cost_usd       REAL NOT NULL DEFAULT 0.0,
                latency_ms     REAL NOT NULL DEFAULT 0.0,
                started_at     REAL NOT NULL,
                ended_at       REAL,
                error          TEXT,
                metadata       TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );

            CREATE INDEX IF NOT EXISTS idx_steps_run_id    ON steps(run_id);
            CREATE INDEX IF NOT EXISTS idx_steps_parent    ON steps(parent_step_id);
            CREATE INDEX IF NOT EXISTS idx_steps_name      ON steps(step_name);
            CREATE INDEX IF NOT EXISTS idx_steps_started   ON steps(started_at);
            CREATE INDEX IF NOT EXISTS idx_runs_started    ON runs(started_at);
        """)
        conn.commit()
        conn.close()

    def save_run(self, run: Run):
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, run_name, started_at, ended_at, status, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run.run_id, run.run_name, run.started_at, run.ended_at,
             run.status, json.dumps(run.metadata)),
        )
        conn.commit()

    def update_run(self, run_id: str, ended_at: float, status: str):
        conn = self._conn()
        conn.execute(
            "UPDATE runs SET ended_at = ?, status = ? WHERE run_id = ?",
            (ended_at, status, run_id),
        )
        conn.commit()

    def save_step(self, step: Step):
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO steps "
            "(step_id, run_id, parent_step_id, step_name, model, input_data, output_data, "
            " tokens_input, tokens_output, cost_usd, latency_ms, started_at, ended_at, error, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                step.step_id, step.run_id, step.parent_step_id, step.step_name,
                step.model,
                json.dumps(step.input_data),
                json.dumps(step.output_data) if step.output_data is not None else None,
                step.tokens_input, step.tokens_output,
                step.cost_usd, step.latency_ms,
                step.started_at, step.ended_at,
                step.error, json.dumps(step.metadata),
            ),
        )
        conn.commit()

    def get_runs(self, limit: int = 50, offset: int = 0) -> List[dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT r.*, "
            "  COUNT(s.step_id)                        AS step_count, "
            "  COALESCE(SUM(s.tokens_input + s.tokens_output), 0) AS total_tokens, "
            "  COALESCE(SUM(s.cost_usd), 0)            AS total_cost "
            "FROM runs r "
            "LEFT JOIN steps s ON r.run_id = s.run_id "
            "GROUP BY r.run_id "
            "ORDER BY r.started_at DESC "
            "LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("metadata"), str):
                d["metadata"] = json.loads(d["metadata"])
            result.append(d)
        return result

    def get_run(self, run_id: str) -> Optional[dict]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("metadata"), str):
            d["metadata"] = json.loads(d["metadata"])
        return d

    def get_steps(self, run_id: str) -> List[dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY started_at ASC",
            (run_id,),
        ).fetchall()
        return [self._row_to_step(r) for r in rows]

    def get_step(self, step_id: str) -> Optional[dict]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM steps WHERE step_id = ?", (step_id,)).fetchone()
        return self._row_to_step(row) if row else None

    def query_steps(
        self,
        run_id: Optional[str] = None,
        step_name: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        min_tokens: Optional[int] = None,
        has_error: Optional[bool] = None,
        limit: int = 100,
    ) -> List[dict]:
        clauses, params = [], []
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if step_name:
            clauses.append("step_name LIKE ?")
            params.append(f"%{step_name}%")
        if start_time is not None:
            clauses.append("started_at >= ?")
            params.append(start_time)
        if end_time is not None:
            clauses.append("started_at <= ?")
            params.append(end_time)
        if min_tokens is not None:
            clauses.append("(tokens_input + tokens_output) >= ?")
            params.append(min_tokens)
        if has_error is not None:
            clauses.append("error IS NOT NULL" if has_error else "error IS NULL")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        conn = self._conn()
        rows = conn.execute(
            f"SELECT * FROM steps {where} ORDER BY started_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_step(r) for r in rows]

    @staticmethod
    def _row_to_step(row) -> dict:
        d = dict(row)
        d["input_data"] = json.loads(d["input_data"]) if d.get("input_data") else None
        d["output_data"] = json.loads(d["output_data"]) if d.get("output_data") else None
        d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else {}
        return d
