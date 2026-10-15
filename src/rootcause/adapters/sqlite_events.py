"""The event log in SQLite. Append-only: there is no update or delete path."""

import json
import sqlite3
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rootcause.application.ports import Event, RunInfo

# Bump on any change to the tables below. Old logs are refused, never silently migrated.
SCHEMA_VERSION = 2

SCHEMA = """
create table if not exists runs (
    run_id text primary key,
    scenario_id text not null,
    agent text not null,
    provider text not null,
    parent_run_id text references runs(run_id),
    started_at real not null
);
create table if not exists events (
    run_id text not null references runs(run_id),
    seq integer not null,
    ts real not null,
    kind text not null,
    payload text not null,
    primary key (run_id, seq)
);
"""


class SqliteEventLog:
    def __init__(self, path: Path | str = ":memory:") -> None:
        self.db = sqlite3.connect(path)
        (version,) = self.db.execute("pragma user_version").fetchone()
        has_tables = self.db.execute("select count(*) from sqlite_master").fetchone()[0] > 0
        if has_tables and version != SCHEMA_VERSION:
            raise RuntimeError(
                f"{path} uses event log schema version {version}, expected {SCHEMA_VERSION}. "
                "Move it aside or pass a new --runlog path."
            )
        self.db.executescript(SCHEMA)
        self.db.execute(f"pragma user_version = {SCHEMA_VERSION}")

    def start_run(
        self, *, scenario_id: str, agent: str, provider: str, parent_run_id: str | None = None
    ) -> str:
        run_id = str(uuid.uuid7())  # time-ordered, so runs sort by start
        with self.db:
            self.db.execute(
                "insert into runs values (?, ?, ?, ?, ?, ?)",
                (run_id, scenario_id, agent, provider, parent_run_id, time.time()),
            )
        return run_id

    def append(self, run_id: str, kind: str, payload: Mapping[str, Any]) -> None:
        with self.db:
            (seq,) = self.db.execute(
                "select coalesce(max(seq), 0) + 1 from events where run_id = ?", (run_id,)
            ).fetchone()
            self.db.execute(
                "insert into events values (?, ?, ?, ?, ?)",
                (run_id, seq, time.time(), kind, json.dumps(payload, default=str)),
            )

    def events(self, run_id: str) -> list[Event]:
        rows = self.db.execute(
            "select seq, kind, payload from events where run_id = ? order by seq", (run_id,)
        ).fetchall()
        return [Event(seq, kind, json.loads(payload)) for seq, kind, payload in rows]

    def run_info(self, run_id: str) -> RunInfo:
        row = self.db.execute(
            "select run_id, scenario_id, agent, provider, parent_run_id from runs where run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunInfo(*row)

    def children(self, run_id: str) -> list[RunInfo]:
        rows = self.db.execute(
            "select run_id, scenario_id, agent, provider, parent_run_id from runs"
            " where parent_run_id = ? order by run_id",
            (run_id,),
        ).fetchall()
        return [RunInfo(*row) for row in rows]
