import json

from djd_maker.core.storage import JsonStore


def test_json_store_round_trip_is_utf8(tmp_path) -> None:
    path = tmp_path / "system" / "job.json"
    store = JsonStore(path)
    value = {"script": "SD001_仕事ができる人", "state": "WAITING"}
    store.save(value)
    assert store.load() == value
    assert "仕事ができる人" in path.read_text(encoding="utf-8")


def test_json_store_returns_default_when_missing(tmp_path) -> None:
    assert JsonStore(tmp_path / "missing.json").load({"schema": 1}) == {"schema": 1}


def test_failed_serialization_keeps_previous_file(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = JsonStore(path)
    store.save({"state": "WAITING"})
    try:
        store.save({"not_serializable": object()})
    except TypeError:
        pass
    assert json.loads(path.read_text(encoding="utf-8")) == {"state": "WAITING"}
    assert not list(tmp_path.glob("*.tmp"))

