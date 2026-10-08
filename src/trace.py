"""Persistence: append-only execution events, run state, approvals and human questions (SQLite).

Events are append-only by construction: triggers make UPDATE and DELETE on `events` fail. No chain-of-thought is
stored, only the action rationale the model gives with each proposal and observable facts.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from src.contracts import ApprovalSnapshot, ExecutionEvent, TaskState, now_iso

SCHEMA = """
create table if not exists runs(run_id text primary key, goal_text text, status text, state_json text,
  created_at text, updated_at text);
create table if not exists events(id integer primary key autoincrement, run_id text, step_index int,
  attempt_index int, event_type text, payload text, timestamp text);
create index if not exists events_run on events(run_id, id);
create trigger if not exists events_no_update before update on events begin select raise(abort, 'events are append-only'); end;
create trigger if not exists events_no_delete before delete on events begin select raise(abort, 'events are append-only'); end;
create table if not exists approvals(approval_id text primary key, run_id text, status text, snapshot text);
create table if not exists questions(question_id text primary key, run_id text, question text, answer text,
  status text, asked_at text, answered_at text);
"""


def runs_dir() -> Path:
    d = Path(os.environ.get("RUNS_DIR", "runs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or runs_dir() / "worker.db"
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)

    def q(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # ------------------------------------------------------------- events

    def event(self, run_id: str, step: int, attempt: int, event_type: str, payload: dict | None = None) -> ExecutionEvent:
        ev = ExecutionEvent(run_id=run_id, step_index=step, attempt_index=attempt, event_type=event_type,
                            payload=payload or {}, timestamp=now_iso())
        self.q("insert into events(run_id,step_index,attempt_index,event_type,payload,timestamp) values(?,?,?,?,?,?)",
               run_id, step, attempt, event_type, json.dumps(ev.payload, default=str), ev.timestamp)
        return ev

    def events(self, run_id: str, after: int = 0) -> list[dict]:
        return [{**dict(r), "payload": json.loads(r["payload"])}
                for r in self.q("select * from events where run_id=? and id>? order by id", run_id, after)]

    # ------------------------------------------------------------- state

    def save(self, s: TaskState) -> None:
        now = now_iso()
        self.q("insert into runs(run_id,goal_text,status,state_json,created_at,updated_at) values(?,?,?,?,?,?) "
               "on conflict(run_id) do update set status=excluded.status, state_json=excluded.state_json, "
               "updated_at=excluded.updated_at", s.run_id, s.goal_text, s.status.value, s.model_dump_json(), now, now)

    def load(self, run_id: str) -> TaskState:
        rows = self.q("select state_json from runs where run_id=?", run_id)
        if not rows:
            raise KeyError(run_id)
        return TaskState.model_validate_json(rows[0]["state_json"])

    def runs(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.q("select run_id,goal_text,status,created_at,updated_at from runs "
                                        "order by created_at desc limit ?", limit)]

    # ------------------------------------------------------------- approvals (server-side records)

    def put_approval(self, a: ApprovalSnapshot) -> None:
        self.q("insert or replace into approvals(approval_id,run_id,status,snapshot) values(?,?,?,?)",
               a.approval_id, a.run_id, a.status, a.model_dump_json())

    def approval(self, approval_id: str) -> ApprovalSnapshot | None:
        rows = self.q("select snapshot from approvals where approval_id=?", approval_id)
        return ApprovalSnapshot.model_validate_json(rows[0]["snapshot"]) if rows else None

    def approvals(self, run_id: str) -> list[ApprovalSnapshot]:
        return [ApprovalSnapshot.model_validate_json(r["snapshot"])
                for r in self.q("select snapshot from approvals where run_id=? order by rowid", run_id)]

    # ------------------------------------------------------------- clarification questions

    def ask(self, run_id: str, question: str) -> str:
        qid = "q-" + uuid.uuid4().hex[:10]
        self.q("insert into questions values(?,?,?,?,?,?,?)", qid, run_id, question, None, "PENDING", now_iso(), None)
        return qid

    def answer(self, qid: str, answer: str) -> None:
        self.q("update questions set answer=?, status='ANSWERED', answered_at=? where question_id=? and status='PENDING'",
               answer, now_iso(), qid)

    def question(self, qid: str) -> dict | None:
        rows = self.q("select * from questions where question_id=?", qid)
        return dict(rows[0]) if rows else None
