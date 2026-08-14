"""Tests für Shorts-Stufe 2, Teil 3: den lokalen Urteilsserver (Auftrag 22).

Echte HTTP-Anfragen gegen einen echten, auf ``127.0.0.1:0`` gestarteten
Server - kein Mock von ``http.server``. Das Video ist eine kleine
Testdatei, kein echtes MP4; der Server behandelt sie byteweise, das genügt
für Bereichsanfragen.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import judge_server as srv
from matrix_auto_cutter.shorts.judge import JudgeEntry


def _entry(index: int, start_ms: int = 0, end_ms: int = 1_000) -> JudgeEntry:
    return JudgeEntry(
        index=index,
        titel=f"Titel {index}",
        begruendung="Begruendung",
        sicherheit="hoch",
        start_ms=start_ms,
        end_ms=end_ms,
        is_child=False,
        transcript_text="Text",
        transcript_precise=True,
        cluster=(),
    )


@pytest.fixture
def running_server(tmp_path: Path) -> Iterator[tuple[srv.ThreadingHTTPServer, Path, Path]]:
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(bytes(range(256)) * 40)  # 10240 Bytes, adressierbar per Index
    urteile_path = tmp_path / "urteile.json"
    entries = [_entry(0, 1_000, 5_000), _entry(1, 5_000, 9_000)]
    server = srv.build_server(
        html=b"<!doctype html><title>Test</title>",
        video_path=video_path,
        urteile_path=urteile_path,
        entries=entries,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, video_path, urteile_path
    finally:
        srv.shutdown_server(server)
        thread.join(timeout=2)


def _connection(server: srv.ThreadingHTTPServer) -> http.client.HTTPConnection:
    return http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)


# --- Bindung -------------------------------------------------------------------------


def test_server_binds_to_loopback_not_all_interfaces(running_server: tuple) -> None:
    server, _video_path, _urteile_path = running_server
    assert server.server_address[0] == "127.0.0.1"
    assert server.server_address[0] != "0.0.0.0"


def test_build_server_picks_a_free_port_when_zero_is_requested(
    running_server: tuple,
) -> None:
    server, _video_path, _urteile_path = running_server
    assert server.server_address[1] != 0


# --- Auslieferungsgrenze ---------------------------------------------------------------


def test_root_and_video_and_urteile_are_served(running_server: tuple) -> None:
    server, _video_path, _urteile_path = running_server
    conn = _connection(server)
    conn.request("GET", "/")
    response = conn.getresponse()
    assert response.status == 200
    response.read()
    conn.close()


def test_path_traversal_attempt_is_rejected(running_server: tuple) -> None:
    """Genau das im Auftrag genannte Beispiel: kein allgemeiner Dateiserver."""
    server, _video_path, _urteile_path = running_server
    conn = _connection(server)
    conn.request("GET", "/../../../windows/win.ini")
    response = conn.getresponse()
    assert response.status == 404
    response.read()
    conn.close()


def test_unknown_path_is_rejected(running_server: tuple) -> None:
    server, _video_path, _urteile_path = running_server
    conn = _connection(server)
    conn.request("GET", "/beliebige-datei.txt")
    response = conn.getresponse()
    assert response.status == 404
    response.read()
    conn.close()


# --- Bereichsanfragen --------------------------------------------------------------


def test_video_range_request_returns_206_and_the_right_slice(running_server: tuple) -> None:
    server, video_path, _urteile_path = running_server
    full = video_path.read_bytes()
    conn = _connection(server)
    conn.request("GET", "/video", headers={"Range": "bytes=10-19"})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    assert response.status == 206
    assert response.getheader("Content-Range") == f"bytes 10-19/{len(full)}"
    assert response.getheader("Accept-Ranges") == "bytes"
    assert body == full[10:20]


def test_video_without_range_header_returns_full_file(running_server: tuple) -> None:
    server, video_path, _urteile_path = running_server
    full = video_path.read_bytes()
    conn = _connection(server)
    conn.request("GET", "/video")
    response = conn.getresponse()
    body = response.read()
    conn.close()
    assert response.status == 200
    assert body == full


def test_video_open_ended_range_returns_rest_of_file(running_server: tuple) -> None:
    server, video_path, _urteile_path = running_server
    full = video_path.read_bytes()
    conn = _connection(server)
    conn.request("GET", "/video", headers={"Range": "bytes=100-"})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    assert response.status == 206
    assert body == full[100:]


def test_video_suffix_range_returns_last_bytes(running_server: tuple) -> None:
    server, video_path, _urteile_path = running_server
    full = video_path.read_bytes()
    conn = _connection(server)
    conn.request("GET", "/video", headers={"Range": "bytes=-50"})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    assert response.status == 206
    assert body == full[-50:]


def test_video_range_beyond_file_size_returns_416(running_server: tuple) -> None:
    server, video_path, _urteile_path = running_server
    full_len = len(video_path.read_bytes())
    conn = _connection(server)
    conn.request("GET", "/video", headers={"Range": f"bytes={full_len + 100}-{full_len + 200}"})
    response = conn.getresponse()
    response.read()
    conn.close()
    assert response.status == 416


def test_parse_range_reads_a_plain_range() -> None:
    assert srv.parse_range("bytes=10-19", file_size=100) == (10, 19)


def test_parse_range_none_header_means_whole_file() -> None:
    assert srv.parse_range(None, file_size=100) is None


def test_parse_range_open_ended_reaches_the_end() -> None:
    assert srv.parse_range("bytes=90-", file_size=100) == (90, 99)


def test_parse_range_suffix_form() -> None:
    assert srv.parse_range("bytes=-10", file_size=100) == (90, 99)


def test_parse_range_rejects_unsupported_unit() -> None:
    with pytest.raises(srv.UnsatisfiableRangeError):
        srv.parse_range("items=0-1", file_size=100)


def test_parse_range_rejects_start_beyond_file() -> None:
    with pytest.raises(srv.UnsatisfiableRangeError):
        srv.parse_range("bytes=200-300", file_size=100)


def test_parse_range_clamps_end_to_file_size() -> None:
    assert srv.parse_range("bytes=90-1000", file_size=100) == (90, 99)


# --- Urteile: schreiben, wiederherstellen -------------------------------------------


def test_posting_a_verdict_writes_it_and_get_restores_it(running_server: tuple) -> None:
    server, _video_path, urteile_path = running_server
    body = json.dumps({"index": 0, "urteil": "ja", "notiz": "gut"}).encode("utf-8")
    conn = _connection(server)
    conn.request(
        "POST", "/urteile", body=body, headers={"Content-Type": "application/json"}
    )
    response = conn.getresponse()
    response.read()
    conn.close()
    assert response.status == 204
    assert urteile_path.is_file()

    conn = _connection(server)
    conn.request("GET", "/urteile")
    response = conn.getresponse()
    payload = json.loads(response.read())
    conn.close()
    assert payload["0"] == {"urteil": "ja", "notiz": "gut"}


def test_each_verdict_is_persisted_immediately_not_only_at_the_end(
    running_server: tuple,
) -> None:
    """Ein Absturz nach dem ersten Urteil darf das erste Urteil nicht kosten."""
    server, _video_path, urteile_path = running_server
    body = json.dumps({"index": 0, "urteil": "nein", "notiz": ""}).encode("utf-8")
    conn = _connection(server)
    conn.request("POST", "/urteile", body=body, headers={"Content-Type": "application/json"})
    conn.getresponse().read()
    conn.close()

    on_disk = json.loads(urteile_path.read_text(encoding="utf-8"))
    assert on_disk["kandidaten"]["0"]["urteil"] == "nein"


def test_second_verdict_does_not_erase_the_first(running_server: tuple) -> None:
    server, _video_path, _urteile_path = running_server
    for index, urteil in ((0, "ja"), (1, "spaeter")):
        body = json.dumps({"index": index, "urteil": urteil, "notiz": ""}).encode("utf-8")
        conn = _connection(server)
        conn.request("POST", "/urteile", body=body, headers={"Content-Type": "application/json"})
        conn.getresponse().read()
        conn.close()

    conn = _connection(server)
    conn.request("GET", "/urteile")
    payload = json.loads(conn.getresponse().read())
    conn.close()
    assert payload["0"]["urteil"] == "ja"
    assert payload["1"]["urteil"] == "spaeter"


def test_concurrent_verdicts_do_not_lose_or_corrupt_each_other(running_server: tuple) -> None:
    """Zwei fast gleichzeitige Urteile (z. B. zwei Klicks kurz hintereinander).

    Ohne Sperre und eindeutigen Temp-Dateinamen ueberschreiben sich zwei
    fast gleichzeitige Schreibvorgaenge gegenseitig - beobachtet im echten
    Abnahmelauf (Auftrag 22): das zweite Urteil fehlte, und die Datei trug
    angehaengten Muell aus dem ersten, unfertigen Schreibvorgang.
    """
    server, _video_path, urteile_path = running_server

    def post(index: int, urteil: str) -> None:
        body = json.dumps({"index": index, "urteil": urteil, "notiz": ""}).encode("utf-8")
        conn = _connection(server)
        conn.request("POST", "/urteile", body=body, headers={"Content-Type": "application/json"})
        conn.getresponse().read()
        conn.close()

    threads = [
        threading.Thread(target=post, args=(0, "ja")),
        threading.Thread(target=post, args=(1, "nein")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    # Die Datei muss gueltiges JSON sein - kein angehaengter Rest eines
    # ueberholten Schreibvorgangs.
    on_disk = json.loads(urteile_path.read_text(encoding="utf-8"))
    assert on_disk["kandidaten"]["0"]["urteil"] == "ja"
    assert on_disk["kandidaten"]["1"]["urteil"] == "nein"


def test_verdict_for_unknown_candidate_index_is_rejected(running_server: tuple) -> None:
    server, _video_path, _urteile_path = running_server
    body = json.dumps({"index": 999, "urteil": "ja", "notiz": ""}).encode("utf-8")
    conn = _connection(server)
    conn.request("POST", "/urteile", body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    response.read()
    conn.close()
    assert response.status == 404


def test_verdict_with_invalid_urteil_value_is_rejected(running_server: tuple) -> None:
    server, _video_path, _urteile_path = running_server
    body = json.dumps({"index": 0, "urteil": "vielleicht", "notiz": ""}).encode("utf-8")
    conn = _connection(server)
    conn.request("POST", "/urteile", body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    response.read()
    conn.close()
    assert response.status == 400


def test_load_urteile_missing_file_returns_empty(tmp_path: Path) -> None:
    assert srv.load_urteile(tmp_path / "fehlt.json") == {}


def test_write_and_load_urteile_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "urteile.json"
    urteile = {
        0: srv.Urteil(
            index=0, titel="T", start_ms=0, end_ms=1_000, ist_kind=False, urteil="ja", notiz="n"
        )
    }
    srv.write_urteile(path, urteile)
    restored = srv.load_urteile(path)
    assert restored == urteile


# --- Sperrdatei ------------------------------------------------------------------------


def test_lock_file_name_differs_from_review_lock(tmp_path: Path) -> None:
    guard = srv.JudgeServerSingleInstance(tmp_path / "shorts-urteilsserver.lock")
    assert guard.path.name != "review.lock"
    assert guard.path.name == "shorts-urteilsserver.lock"


def test_lock_can_be_acquired_and_released(tmp_path: Path) -> None:
    guard = srv.JudgeServerSingleInstance(tmp_path / "shorts-urteilsserver.lock")
    assert guard.acquire() is True
    guard.close()
    other = srv.JudgeServerSingleInstance(tmp_path / "shorts-urteilsserver.lock")
    assert other.acquire() is True
    other.close()


def test_second_lock_holder_is_refused_while_first_holds_it(tmp_path: Path) -> None:
    lock_path = tmp_path / "shorts-urteilsserver.lock"
    first = srv.JudgeServerSingleInstance(lock_path)
    second = srv.JudgeServerSingleInstance(lock_path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.close()
