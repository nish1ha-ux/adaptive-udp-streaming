"""Tests for the backend transfer service and HTTP API."""

import hashlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from backend.server import Handler
from backend.service import (
    MAX_UPLOAD_BYTES,
    TransferError,
    TransferParams,
    run_transfer,
)


@pytest.fixture(scope="module")
def base_url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url) as resp:
        return resp.status, resp.read()


def _post(url, body):
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_params_defaults_and_validation():
    assert TransferParams.from_mapping({}) == TransferParams()
    for bad in ({"arq": "x"}, {"interval": "2"}, {"max_retries": "99"},
                {"initial_rto": "0.01"}, {"interval": "abc"}):
        with pytest.raises(TransferError):
            TransferParams.from_mapping(bad)


def test_run_transfer_roundtrip_integrity():
    data = bytes(range(256)) * 20
    record = run_transfer("a.bin", data, TransferParams(interval=0))
    assert record["integrity_ok"]
    assert record["sha256_received"] == hashlib.sha256(data).hexdigest()
    assert record["packets"] == 4


def test_run_transfer_empty_file():
    assert run_transfer("empty", b"", TransferParams(interval=0))["integrity_ok"]


def test_run_transfer_too_large():
    with pytest.raises(TransferError):
        run_transfer("big", b"x" * (MAX_UPLOAD_BYTES + 1), TransferParams())


def test_api_info_and_static(base_url):
    status, body = _get(f"{base_url}/api/info")
    assert status == 200 and json.loads(body)["name"] == "AUDP"
    status, body = _get(f"{base_url}/")
    assert status == 200 and b"Adaptive UDP Streaming" in body


def test_api_transfer_and_history(base_url):
    status, rec = _post(f"{base_url}/api/transfer?filename=t.txt&interval=0", b"hello" * 500)
    assert status == 200 and rec["integrity_ok"] and rec["filename"] == "t.txt"
    _, body = _get(f"{base_url}/api/history")
    assert json.loads(body)[0]["filename"] == "t.txt"


def test_api_rejects_bad_params(base_url):
    status, body = _post(f"{base_url}/api/transfer?arq=bogus", b"x")
    assert status == 422 and "arq" in body["error"]


def test_static_path_traversal_blocked(base_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(f"{base_url}/..%2fREADME.md")
    assert exc.value.code == 404


def test_samples_are_deterministic_and_sized():
    from backend.service import SAMPLES, build_sample
    for sample in SAMPLES:
        name, data = build_sample(sample.id)
        assert len(data) == sample.size and name.startswith(sample.id)
        assert build_sample(sample.id)[1] == data


def test_api_samples_and_sample_transfer(base_url):
    status, body = _get(f"{base_url}/api/samples")
    ids = [s["id"] for s in json.loads(body)]
    assert status == 200 and "note" in ids and "empty" in ids
    status, rec = _post(f"{base_url}/api/transfer?sample=note&interval=0", b"")
    assert status == 200 and rec["integrity_ok"] and rec["bytes"] == 900


def test_api_unknown_sample_rejected(base_url):
    status, body = _post(f"{base_url}/api/transfer?sample=nope", b"")
    assert status == 422 and "Unknown sample" in body["error"]


def test_api_results_reads_latest_file_from_disk(base_url, tmp_path, monkeypatch):
    import backend.service as service
    monkeypatch.setattr(service, "RESULTS_DIR", tmp_path)
    (tmp_path / "experiment_repeats.json").write_text('{"meta": {}, "summary": [1]}', encoding="utf-8")
    first = service.load_results()
    assert first["source"] == "experiment_repeats.json" and first["data"]["summary"] == [1]
    (tmp_path / "experiment_tuned.json").write_text('{"meta": {}, "summary": [2]}', encoding="utf-8")
    assert service.load_results()["data"]["summary"] == [2]  # newer file wins, no restart needed


def test_load_results_missing_raises(tmp_path, monkeypatch):
    import backend.service as service
    monkeypatch.setattr(service, "RESULTS_DIR", tmp_path)
    with pytest.raises(TransferError):
        service.load_results()
