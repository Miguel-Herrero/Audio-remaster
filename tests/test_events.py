"""Tests de remaster/events.py.

La traza es lo que la UI usa para dibujar el timeline, asi que importa
que sea parseable siempre — incluso leyendola a la vez que se escribe.
"""

from __future__ import annotations

import json

from remaster.events import EventLog, read_events


def test_disabled_log_writes_nothing(tmp_path):
    """Sin --events la CLI se comporta exactamente como antes."""
    log = EventLog(None)
    assert not log.enabled
    log.start("extract")
    log.note("ran")
    log.close()
    assert list(tmp_path.iterdir()) == []


def test_running_then_done(tmp_path):
    path = tmp_path / "e.ndjson"
    log = EventLog(path)
    log.start("extract")
    log.note("ran")
    log.close()

    events = read_events(path)
    assert [e["state"] for e in events] == ["running", "done"]
    assert events[0]["step"] == "extract"
    assert events[1]["outcome"] == "ran"
    assert events[1]["seconds"] >= 0


def test_noop_when_nothing_ran(tmp_path):
    path = tmp_path / "e.ndjson"
    log = EventLog(path)
    log.start("retime")
    log.note("noop")
    log.close()
    assert read_events(path)[-1]["outcome"] == "noop"


def test_ran_wins_over_noop(tmp_path):
    """extract reporta es/en/ja por separado: si alguno se ejecuto, el
    paso se ejecuto.
    """
    path = tmp_path / "e.ndjson"
    log = EventLog(path)
    log.start("extract")
    log.note("noop")
    log.note("ran")
    log.note("noop")
    log.close()
    assert read_events(path)[-1]["outcome"] == "ran"


def test_only_skipped_notes_means_skipped(tmp_path):
    path = tmp_path / "e.ndjson"
    log = EventLog(path)
    log.start("extract")
    log.note("skipped")
    log.close()
    assert read_events(path)[-1]["outcome"] == "skipped"


def test_starting_a_step_closes_the_previous_one(tmp_path):
    """Cerrar al abrir el siguiente evita sembrar la CLI de llamadas
    simetricas que un `return` temprano se saltaria.
    """
    path = tmp_path / "e.ndjson"
    log = EventLog(path)
    log.start("align")
    log.note("ran")
    log.start("loudness")
    log.close()

    states = [(e["step"], e["state"]) for e in read_events(path)]
    assert states == [
        ("align", "running"), ("align", "done"),
        ("loudness", "running"), ("loudness", "done"),
    ]


def test_skip_records_the_reason(tmp_path):
    path = tmp_path / "e.ndjson"
    log = EventLog(path)
    log.skip("remux4k", "sin fuente 4K en 00_sources")
    event = read_events(path)[-1]
    assert event["outcome"] == "skipped"
    assert event["reason"] == "sin fuente 4K en 00_sources"


def test_close_with_explicit_outcome_and_reason(tmp_path):
    path = tmp_path / "e.ndjson"
    log = EventLog(path)
    log.start("verify")
    log.close(outcome="skipped", reason="sin medida de REAPER")
    event = read_events(path)[-1]
    assert event["outcome"] == "skipped"
    assert event["reason"] == "sin medida de REAPER"


def test_close_without_open_step_is_a_noop(tmp_path):
    path = tmp_path / "e.ndjson"
    EventLog(path).close()
    assert read_events(path) == []


def test_reader_tolerates_a_half_written_line(tmp_path):
    """Se lee mientras el otro proceso escribe."""
    path = tmp_path / "e.ndjson"
    path.write_text('{"step":"a","state":"running"}\n{"step":"b","sta')
    events = read_events(path)
    assert len(events) == 1
    assert events[0]["step"] == "a"


def test_reader_on_missing_file(tmp_path):
    assert read_events(tmp_path / "nada.ndjson") == []


def test_every_line_is_standalone_json(tmp_path):
    path = tmp_path / "e.ndjson"
    log = EventLog(path)
    log.start("mux")
    log.close()
    for line in path.read_text().splitlines():
        assert "t" in json.loads(line)
