"""Sperrkonflikte beim atomaren Ersetzen: Wiederholung, geteiltes Lesen, Statusfehler.

Unter Windows scheitert ``os.replace`` mit ``ERROR_ACCESS_DENIED`` (5) oder
``ERROR_SHARING_VIOLATION`` (32), solange ein anderer Prozess die *Zieldatei*
geöffnet hält.  Genau so ist der Render am 9.8. um 17:02 gestorben, während das
Review-Fenster ``render-status.json`` im Sekundentakt las.

Kein Test hier hängt an einer Wanduhr: der Sperrkonflikt ist echt, aber die
Freigabe passiert im eingehängten ``sleep``, nicht in einem Timer.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from matrix_auto_cutter import product_runner, render
from matrix_auto_cutter.atomic import (
    REPLACE_ATTEMPTS,
    REPLACE_RETRY_SECONDS,
    open_shared,
    read_bytes_shared,
    replace_atomically,
)
from matrix_auto_cutter.render import (
    RenderStatus,
    load_render_status,
    render_status_path,
    write_render_status,
)

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="Der Sperrkonflikt existiert nur unter Windows."
)

NOW = datetime(2026, 8, 9, 17, 2, 11, tzinfo=UTC)


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    """Lege eine belegte Zieldatei und ein fertiges Temporär daneben."""
    target = tmp_path / "render-status.json"
    target.write_bytes(b"alt\n")
    temporary = tmp_path / ".render-status.json.tmp"
    temporary.write_bytes(b"neu\n")
    return target, temporary


def _status(state: str, message: str) -> RenderStatus:
    return RenderStatus(
        artifact_type="matrix_auto_cutter_render_status",
        schema_version="1.0",
        proposal_id="proposal-" + "a" * 8,
        state=cast(Any, state),
        message_de=message,
        updated_at=NOW,
    )


@windows_only
def test_an_open_target_is_replaced_after_the_reader_releases_it(tmp_path: Path) -> None:
    """Die gemeldete Lage: Ziel offen, Ersetzen scheitert, Wiederholung gelingt."""
    target, temporary = _pair(tmp_path)
    holder = target.open("rb")
    waits: list[float] = []

    def release(seconds: float) -> None:
        waits.append(seconds)
        holder.close()

    try:
        replace_atomically(temporary, target, sleep=release)
    finally:
        holder.close()

    assert target.read_bytes() == b"neu\n"
    assert not temporary.exists()
    # Genau eine Wiederholung: der erste Versuch muss wirklich gescheitert sein.
    assert waits == [REPLACE_RETRY_SECONDS]


@windows_only
def test_a_target_that_never_releases_passes_the_error_through(tmp_path: Path) -> None:
    """Nach dem letzten Versuch wird durchgereicht, nicht geschluckt."""
    target, temporary = _pair(tmp_path)
    waits: list[float] = []

    with target.open("rb"), pytest.raises(OSError) as caught:
        replace_atomically(temporary, target, sleep=waits.append)

    assert caught.value.winerror in {5, 32}
    assert len(waits) == REPLACE_ATTEMPTS - 1
    assert target.read_bytes() == b"alt\n"


@windows_only
def test_a_shared_reader_never_blocks_the_writer(tmp_path: Path) -> None:
    """Die gehärtete Leseseite: kein Konflikt, also auch keine Wiederholung."""
    target, temporary = _pair(tmp_path)

    def must_not_wait(seconds: float) -> None:
        raise AssertionError(f"unerwartete Wiederholung nach {seconds} s")

    with open_shared(target) as stream:
        assert stream.read() == b"alt\n"
        replace_atomically(temporary, target, sleep=must_not_wait)

    assert target.read_bytes() == b"neu\n"


@windows_only
def test_read_bytes_shared_matches_a_plain_read(tmp_path: Path) -> None:
    target = tmp_path / "status.json"
    target.write_bytes(b'{"a": 1}\n')
    assert read_bytes_shared(target) == target.read_bytes()


def test_a_missing_file_still_raises_oserror(tmp_path: Path) -> None:
    """Die Leseseite bleibt in ihrem Fehlerverhalten austauschbar."""
    with pytest.raises(OSError):
        read_bytes_shared(tmp_path / "gibt-es-nicht.json")


def test_only_the_two_sharing_errors_are_retried(tmp_path: Path) -> None:
    """FileExistsError gehört dem Aufrufer und darf nicht in der Wiederholung hängen."""
    target, temporary = _pair(tmp_path)
    waits: list[float] = []

    with pytest.raises(FileExistsError):
        replace_atomically(temporary, target, create_only=True, sleep=waits.append)

    assert waits == []
    assert target.read_bytes() == b"alt\n"


@windows_only
def test_create_only_never_overwrites_a_locked_existing_target(tmp_path: Path) -> None:
    """Die create-only-Garantie hält auch dann, wenn das Ziel gesperrt ist.

    Windows meldet ein vorhandenes Ziel als ``FileExistsError`` (183),
    unabhängig davon, ob es jemand offen hält — gemessen in beiden Lagen.  183
    steht nicht in der Wiederholungsmenge, also wird weder gewartet noch
    eskaliert.
    """
    target, temporary = _pair(tmp_path)
    waits: list[float] = []

    with target.open("rb"), pytest.raises(FileExistsError) as caught:
        replace_atomically(temporary, target, create_only=True, sleep=waits.append)

    assert caught.value.winerror == 183
    assert waits == []
    assert target.read_bytes() == b"alt\n"
    assert temporary.read_bytes() == b"neu\n"


def test_create_only_never_reaches_the_posix_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zweite, unabhängige Barriere: der Zweig mit ReplaceIfExists ist gesperrt.

    ``_windows_posix_replace`` setzt ``FILE_RENAME_REPLACE_IF_EXISTS`` und würde
    ein vorhandenes Ziel verdrängen.  Für ``create_only`` darf er deshalb nie
    laufen, auch nicht, falls Windows eines Tages 5 oder 32 statt 183 meldet.
    """
    target, temporary = _pair(tmp_path)

    def must_not_run(*_args: object) -> bool:
        raise AssertionError("POSIX-Eskalation darf bei create_only nicht laufen")

    monkeypatch.setattr("matrix_auto_cutter.atomic._windows_posix_replace", must_not_run)
    with pytest.raises(FileExistsError):
        replace_atomically(temporary, target, create_only=True, sleep=lambda _seconds: None)
    assert target.read_bytes() == b"alt\n"


def test_at_least_one_attempt_is_required(tmp_path: Path) -> None:
    target, temporary = _pair(tmp_path)
    with pytest.raises(ValueError):
        replace_atomically(temporary, target, attempts=0)


@windows_only
def test_the_render_status_is_written_while_a_shared_reader_polls(tmp_path: Path) -> None:
    """Regression zu render.py:715 — Statuswechsel gelingt trotz offener Zieldatei."""
    proposal_path = tmp_path / "cut-proposal.json"
    assert write_render_status(proposal_path, _status("render_running", "läuft")) is True
    status_path = render_status_path(proposal_path)

    with open_shared(status_path) as stream:
        assert stream.read()
        assert write_render_status(proposal_path, _status("render_succeeded", "fertig")) is True

    loaded = load_render_status(proposal_path)
    assert loaded is not None
    assert loaded.state == "render_succeeded"


@windows_only
def test_the_render_status_survives_a_reader_that_releases_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch ein ungeteilt geöffneter Leser tötet den Schreiber nicht mehr.

    Das ist der gemeldete Traceback von render.py:715 in ganzer Länge: ein
    Leser, den die Härtung nicht erreicht — Virenscanner, Explorer-Vorschau —
    hält die Zieldatei fest und gibt sie erst später frei.
    """
    proposal_path = tmp_path / "cut-proposal.json"
    write_render_status(proposal_path, _status("render_running", "läuft"))
    holder = render_status_path(proposal_path).open("rb")
    original = render.replace_atomically

    def release_while_waiting(temporary: object, target: object, **kwargs: object) -> None:
        kwargs["sleep"] = lambda _seconds: holder.close()
        original(cast(Any, temporary), cast(Any, target), **cast(Any, kwargs))

    monkeypatch.setattr(render, "replace_atomically", release_while_waiting)
    try:
        assert write_render_status(proposal_path, _status("render_succeeded", "fertig")) is True
    finally:
        holder.close()

    loaded = load_render_status(proposal_path)
    assert loaded is not None and loaded.state == "render_succeeded"


def test_a_locked_status_file_only_warns_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status ist Anzeige, nicht Ergebnis: ein Schreibfehler beendet keinen Render."""

    def always_denied(*_args: object, **_kwargs: object) -> bool:
        raise PermissionError(13, "Zugriff verweigert")

    monkeypatch.setattr(render, "_atomic_write", always_denied)
    status = _status("render_running", "läuft")
    with pytest.warns(RuntimeWarning, match="Renderstatus"):
        written = write_render_status(tmp_path / "cut-proposal.json", status)
    assert written is False


def test_a_locked_runner_status_only_warns_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dasselbe für status.json — der Runner darf daran nicht beim Start sterben."""

    def always_denied(*_args: object, **_kwargs: object) -> bool:
        raise PermissionError(13, "Zugriff verweigert")

    monkeypatch.setattr(product_runner, "_atomic_bytes", always_denied)
    monkeypatch.setattr(product_runner, "_model_bytes", lambda _status: b"{}\n")

    class _StatusPathOnly:
        status_path = tmp_path / "status.json"

    write = product_runner.ProductRunner._write_status_file
    with pytest.warns(RuntimeWarning, match="Runnerstatus"):
        assert write(cast(Any, _StatusPathOnly()), cast(Any, object())) is False
