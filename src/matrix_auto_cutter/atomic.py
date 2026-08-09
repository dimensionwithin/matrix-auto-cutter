"""Atomarer, deterministischer Export von ``protection-ranges.json``.

Zusätzlich liegt hier die eine gemeinsame Stelle, an der ein fertig
geschriebenes Temporär über sein Ziel gezogen wird.  Unter Windows scheitert
``os.replace``/``os.rename`` mit ``ERROR_ACCESS_DENIED`` (5) oder
``ERROR_SHARING_VIOLATION`` (32), solange ein anderer Prozess die *Zieldatei*
geöffnet hält.  Genau das passiert im Betrieb: das Review-Fenster liest
``render-status.json``, ``status.json`` und ``runner.log`` im Sekundentakt,
während Runner und Render sie ersetzen.

Beide Seiten sind hier abgedeckt.  :func:`replace_atomically` wiederholt den
Tausch eine begrenzte Zahl von Malen; :func:`open_shared` und
:func:`read_bytes_shared` öffnen lesend mit ``FILE_SHARE_DELETE``, sodass ein
Leser einen Schreiber gar nicht erst blockiert.  Ein Leser darf einen Schreiber
nie töten.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Literal

from pydantic import Field

from matrix_auto_cutter.errors import CoreError, ErrorCode, core_error
from matrix_auto_cutter.models import (
    CanonicalModel,
    FrameRateModel,
    MaterializedFrameRange,
    Sha256,
)

# Fünf Versuche im Abstand von 50 ms decken rund 200 ms Konflikt ab.  Das
# Review-Fenster hält eine Statusdatei nur für die Dauer eines ``read`` offen;
# länger als 200 ms dauert das auf einer lokalen Platte nicht.  Reicht es doch
# nicht, ist der Fehler echt und wird durchgereicht.
REPLACE_ATTEMPTS = 5
REPLACE_RETRY_SECONDS = 0.05

# ERROR_ACCESS_DENIED und ERROR_SHARING_VIOLATION.  Nur diese beiden werden
# wiederholt: ein fehlendes Verzeichnis oder eine volle Platte wird durch
# Warten nicht besser, und ``FileExistsError`` (183) muss den Aufrufer im
# ``create_only``-Fall unverändert erreichen.
_RETRYABLE_WINERRORS = frozenset({5, 32})


def _is_sharing_conflict(error: OSError) -> bool:
    """Erkenne genau die beiden Windows-Fehler, die ein zweiter Prozess auslöst."""
    return getattr(error, "winerror", None) in _RETRYABLE_WINERRORS


def _windows_posix_replace(temporary: Path, target: Path) -> bool:
    """Versuche das Ersetzen mit POSIX-Semantik; melde nur Erfolg oder Misserfolg.

    ``os.replace`` benutzt ``MoveFileExW``; das kann eine Zieldatei nicht
    überschreiben, solange irgendein Griff darauf offen ist — auch dann nicht,
    wenn dieser Griff ``FILE_SHARE_DELETE`` erlaubt.  ``FileRenameInfoEx`` mit
    ``FILE_RENAME_POSIX_SEMANTICS`` kann es: die alte Datei wird verdrängt, der
    Leser liest seinen Griff zu Ende, und der Name zeigt sofort auf die neue.

    An dieser Anlage gemessen: geteilter Leser plus ``os.replace`` scheitert mit
    5, ungeteilter Leser plus POSIX-Rename scheitert mit 32, geteilter Leser
    plus POSIX-Rename gelingt.  Beide Hälften werden gebraucht, und deshalb
    bleibt dies eine Eskalation und keine Ablösung: gibt es keinen Konflikt,
    läuft der billige Normalweg ohne ctypes und ohne zusätzlichen Dateigriff.

    Jeder Misserfolg ist ``False`` — die Wiederholung im Aufrufer entscheidet
    dann weiter.  Fehlende Unterstützung (ältere Windows, kein NTFS) sieht von
    außen genauso aus.
    """
    import ctypes
    from ctypes import wintypes

    delete_access = 0x00010000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_rename_info_ex = 22
    replace_if_exists = 0x00000001
    posix_semantics = 0x00000002
    invalid_handle = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )

    handle = kernel32.CreateFileW(
        str(temporary),
        delete_access,
        share_read_write_delete,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if handle == invalid_handle:
        return False
    try:
        name = str(target)

        class _RenameInfoEx(ctypes.Structure):
            _fields_ = (
                ("Flags", wintypes.DWORD),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", wintypes.DWORD),
                ("FileName", wintypes.WCHAR * (len(name) + 1)),
            )

        info = _RenameInfoEx(
            replace_if_exists | posix_semantics,
            None,
            len(name) * ctypes.sizeof(wintypes.WCHAR),
            name,
        )
        return bool(
            kernel32.SetFileInformationByHandle(
                handle, file_rename_info_ex, ctypes.byref(info), ctypes.sizeof(info)
            )
        )
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def replace_atomically(
    temporary: str | Path,
    target: str | Path,
    *,
    create_only: bool = False,
    attempts: int = REPLACE_ATTEMPTS,
    retry_seconds: float = REPLACE_RETRY_SECONDS,
    sleep: Callable[[float], object] = time.sleep,
) -> None:
    """Ziehe ``temporary`` über ``target`` und wiederhole nur bei Sperrkonflikten.

    ``create_only`` benutzt ``os.rename`` und lässt ``FileExistsError``
    unverändert nach oben; jeder andere Fehler außer den beiden Sperrfehlern
    wird sofort durchgereicht.
    """
    if attempts < 1:
        msg = "attempts muss mindestens 1 sein."
        raise ValueError(msg)
    for remaining in range(attempts - 1, -1, -1):
        try:
            if create_only:
                # Ein Ziel, das es noch nicht gibt, kann niemand offen halten;
                # ``FileExistsError`` muss den Aufrufer unverändert erreichen.
                os.rename(temporary, target)
            else:
                os.replace(temporary, target)
        except OSError as error:
            if not _is_sharing_conflict(error):
                raise
            if (
                not create_only
                and os.name == "nt"
                and _windows_posix_replace(Path(temporary), Path(target))
            ):
                return
            if remaining == 0:
                raise
            sleep(retry_seconds)
        else:
            return


def open_shared(path: str | Path) -> IO[bytes]:
    """Öffne binär lesend, ohne einem Schreiber das Ersetzen zu verbieten.

    Der von CPython benutzte Standardmodus erlaubt anderen Prozessen Lesen und
    Schreiben, aber nicht ``FILE_SHARE_DELETE`` — und ohne das scheitert
    ``os.replace`` auf der geöffneten Datei.  Auf allen anderen Systemen gibt es
    das Problem nicht, dort ist dies ein gewöhnliches ``open``.
    """
    if os.name != "nt":
        return Path(path).open("rb")
    return _open_shared_windows(Path(path))


def _open_shared_windows(path: Path) -> IO[bytes]:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    share_read_write_delete = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080
    invalid_handle = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    handle = kernel32.CreateFileW(
        str(path),
        generic_read,
        share_read_write_delete,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if handle == invalid_handle:
        # WinError trägt errno und winerror, bestehende ``except OSError``
        # Zweige der Aufrufer greifen dadurch unverändert.
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise
    return os.fdopen(descriptor, "rb")


def read_bytes_shared(path: str | Path) -> bytes:
    """Lies eine Datei vollständig, ohne einen gleichzeitigen Ersetzer zu stören."""
    with open_shared(path) as stream:
        return stream.read()


class ProtectionRangesDocument(CanonicalModel):
    """Kanonisches Consumer-Artefakt des ersten Coding-Auftrags."""

    schema_version: Literal["1.0"] = "1.0"
    source_sha256: Sha256
    input_hash: Sha256
    configuration_hash: Sha256
    sidecar_schema_version: Literal["1.1", "1.2"] = "1.2"
    time_base: FrameRateModel = Field(default_factory=FrameRateModel)
    ranges: tuple[MaterializedFrameRange, ...]


class AtomicWriteResult(CanonicalModel):
    """Strukturiertes IO-Ergebnis ohne erwartbare rohe Exception."""

    status: Literal["written", "failed"]
    output_path: str
    error: CoreError | None = None


def _deterministic_json(document: ProtectionRangesDocument) -> bytes:
    return (document.model_dump_json(indent=2) + "\n").encode("utf-8")


def write_protection_ranges(
    output_path: str | Path,
    document: ProtectionRangesDocument,
) -> AtomicWriteResult:
    """Flushe im Zielverzeichnis und ersetze anschließend atomar."""
    target = Path(output_path)
    if target.name != "protection-ranges.json" or ".." in target.parts:
        return AtomicWriteResult(
            status="failed",
            output_path=str(target),
            error=core_error(
                ErrorCode.SIDECAR_OUTPUT,
                {"path": str(target), "reason": "invalid_output_target"},
            ),
        )
    temporary: Path | None = None
    primary_error: OSError | None = None
    cleanup_error: OSError | RuntimeError | None = None
    try:
        target.parent.mkdir(parents=False, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.tmp.",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_deterministic_json(document))
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomically(temporary, target)
    except OSError as exc:
        primary_error = exc
    finally:
        if temporary is not None:
            active_error = sys.exception()
            try:
                temporary.unlink(missing_ok=True)
            except (OSError, RuntimeError) as exc:
                cause = active_error or primary_error
                if cause is None:
                    raise
                cause.add_note(f"Sekundärer Tempdatei-Cleanupfehler: {exc}")
                cleanup_error = exc
    if primary_error is not None:
        context: dict[str, object] = {"path": str(target), "detail": str(primary_error)}
        if cleanup_error is not None:
            context["cleanup_detail"] = str(cleanup_error)
        return AtomicWriteResult(
            status="failed",
            output_path=str(target),
            error=core_error(
                ErrorCode.SIDECAR_OUTPUT,
                context,
                retryable=True,
            ),
        )
    return AtomicWriteResult(status="written", output_path=str(target))
