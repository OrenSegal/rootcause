"""The event log refuses a database written under a different schema, instead of
failing later on the first insert."""

import sqlite3

import pytest

from rootcause.adapters.sqlite_events import SCHEMA_VERSION, SqliteEventLog


def test_new_database_is_stamped_with_the_schema_version(tmp_path):
    path = tmp_path / "runs.sqlite"
    SqliteEventLog(path)
    (version,) = sqlite3.connect(path).execute("pragma user_version").fetchone()
    assert version == SCHEMA_VERSION


def test_a_database_from_another_schema_is_rejected_clearly(tmp_path):
    path = tmp_path / "runs.sqlite"
    old = sqlite3.connect(path)
    old.execute("create table runs (run_id text primary key, scenario_id text, provider text)")
    old.commit()
    with pytest.raises(RuntimeError, match="schema version 0, expected"):
        SqliteEventLog(path)


def test_reopening_a_current_database_keeps_its_runs(tmp_path):
    path = tmp_path / "runs.sqlite"
    run_id = SqliteEventLog(path).start_run(scenario_id="s", agent="a", provider="p")
    assert SqliteEventLog(path).run_info(run_id).agent == "a"
