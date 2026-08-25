r"""Auftrag shorts-auswahl: aus Kandidaten plus Urteilen eine Bauliste machen.

Zwischen Urteil (``judge_server.py``) und Bau (``build.py``) fehlte ein
Werkzeug: ``build.py`` liest ausschliesslich ``kandidaten.json`` und kennt
keine Urteile - ohne dieses Modul wuerde ein Aufruf alle Kandidaten bauen,
auch die verworfenen. ``waehle_kandidaten`` trifft die Auswahl (nur
``urteil == "ja"`` wird gebaut), ``pruefe_uebereinstimmung`` sichert davor
ab, dass ein Urteil wirklich zu dem Kandidaten gehoert, den es zu meinen
scheint - ein falsch zugeordnetes Urteil ist schlimmer als gar keins.

``bauliste.json`` traegt bewusst dasselbe Schema wie ``kandidaten.json``
(Wurzel ``kandidaten``, unveraenderte ``index``-Werte) - so kann sie
``build.py`` unveraendert als ``KANDIDATEN_PATH`` uebergeben werden, und die
von ``build.py`` gebildeten Ordnernamen (``kandidat-{index:02d}``) bleiben
auf die urspruenglichen Kandidaten bezogen.

``labels/repeat/trefferquote.json`` haelt fest, wie oft Kandidaten
angenommen wurden - das laesst sich aus ``bauliste.json`` allein nicht mehr
rekonstruieren, sobald das naechste Mal ueberschrieben wird, deshalb wird
hier angehaengt statt ersetzt.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.shorts.candidates import (
    Candidate,
    CandidatesSchemaError,
    load_candidates,
)
from matrix_auto_cutter.shorts.judge_server import Urteil, load_urteile

BAULISTE_FILE_NAME = "bauliste.json"
TREFFERQUOTE_PFAD = Path("labels/repeat/trefferquote.json")
TREFFERQUOTE_SCHEMA_VERSION = "1.0"

_ZIELBEREICH_MIN_MS = 8000
_ZIELBEREICH_MAX_MS = 15000

_CODE_KANDIDATEN_UNLESBAR = 2
_CODE_KEINE_URTEILSDATEI = 2
_CODE_URTEILE_KEIN_JSON = 3
_CODE_KEINE_ANNAHMEN = 4
_CODE_URTEILE_ABWEICHUNG = 5


def juengste_urteilsdatei(job_dir: Path) -> Path | None:
    """Die nach Aenderungszeit juengste ``urteile*.json`` im Auftragsordner, falls vorhanden.

    Dasselbe Suchmuster und Auswahlkriterium wie
    :func:`matrix_auto_cutter.shorts.judge_server._existing_urteile_files` /
    ``start_session_urteile`` (``job_dir.glob("urteile*.json")``, Auswahl nach
    ``st_mtime_ns``) - nur lesend, ohne eine neue Sitzungsdatei anzulegen.
    """
    kandidaten = [path for path in job_dir.glob("urteile*.json") if path.is_file()]
    if not kandidaten:
        return None
    return max(kandidaten, key=lambda path: path.stat().st_mtime_ns)


def waehle_kandidaten(
    kandidaten: list[Candidate], urteile: dict[int, Urteil]
) -> tuple[list[Candidate], list[Candidate], list[Candidate]]:
    """Teile die Kandidaten nach Urteil: (angenommen, abgelehnt, ohne_urteil).

    Angenommen heisst ausschliesslich ``urteil == "ja"``. Ein Kandidat ohne
    Eintrag in ``urteile`` (oder mit einem Eintrag ohne gesetztes ``urteil``)
    zaehlt als ohne Urteil - ``"nein"`` und ``"spaeter"`` zaehlen als
    abgelehnt, keines der beiden als Annahme.
    """
    angenommen: list[Candidate] = []
    abgelehnt: list[Candidate] = []
    ohne_urteil: list[Candidate] = []
    for candidate in kandidaten:
        urteil = urteile.get(candidate.index)
        if urteil is None or urteil.urteil is None:
            ohne_urteil.append(candidate)
        elif urteil.urteil == "ja":
            angenommen.append(candidate)
        else:
            abgelehnt.append(candidate)
    return angenommen, abgelehnt, ohne_urteil


def pruefe_uebereinstimmung(
    kandidaten: list[Candidate], urteile: dict[int, Urteil]
) -> list[str]:
    """Melde jede Abweichung zwischen einem Urteil und dem Kandidaten, den es meint.

    Je Urteil: existiert der Index ueberhaupt unter den Kandidaten, und
    stimmen ``start_ms``, ``end_ms`` und ``titel`` ueberein? Das ist die
    wichtigste Pruefung des Moduls - ein Urteil, das (z. B. nach einer neu
    erzeugten ``kandidaten.json``) auf einen anderen Kandidaten zeigt als
    gemeint, darf nicht stillschweigend uebernommen werden.
    """
    by_index = {candidate.index: candidate for candidate in kandidaten}
    meldungen: list[str] = []
    for index in sorted(urteile):
        urteil = urteile[index]
        candidate = by_index.get(index)
        if candidate is None:
            meldungen.append(f"Urteil {index}: kein Kandidat mit diesem Index vorhanden")
            continue
        if urteil.start_ms != candidate.start_ms:
            meldungen.append(
                f"Kandidat {index}: 'start_ms' weicht ab - Urteil {urteil.start_ms}, "
                f"Kandidat {candidate.start_ms}"
            )
        if urteil.end_ms != candidate.end_ms:
            meldungen.append(
                f"Kandidat {index}: 'end_ms' weicht ab - Urteil {urteil.end_ms}, "
                f"Kandidat {candidate.end_ms}"
            )
        if urteil.titel != candidate.titel:
            meldungen.append(
                f"Kandidat {index}: 'titel' weicht ab - Urteil {urteil.titel!r}, "
                f"Kandidat {candidate.titel!r}"
            )
    return meldungen


def _candidate_payload(candidate: Candidate) -> dict[str, object]:
    return {
        "index": candidate.index,
        "start_ms": candidate.start_ms,
        "end_ms": candidate.end_ms,
        "titel": candidate.titel,
        "begruendung": candidate.begruendung,
        "sicherheit": candidate.sicherheit,
        "enthaelt": list(candidate.enthaelt),
    }


def baue_bauliste_payload(
    *,
    angenommen: list[Candidate],
    abgelehnt_anzahl: int,
    ohne_urteil_anzahl: int,
    stammt_aus: str,
    urteile_aus: str,
) -> dict[str, object]:
    """Baue den Inhalt von ``bauliste.json`` - dasselbe Schema wie ``kandidaten.json``.

    Die urspruenglichen ``index``-Werte der angenommenen Kandidaten bleiben
    unveraendert erhalten (keine Neunummerierung), damit ``build.py`` daraus
    dieselben ``kandidat-{index:02d}``-Ordner bildet wie ein direkter Bau
    ueber ``kandidaten.json``.
    """
    return {
        "kandidaten": [_candidate_payload(candidate) for candidate in angenommen],
        "stammt_aus": stammt_aus,
        "urteile_aus": urteile_aus,
        "angenommen": len(angenommen),
        "abgelehnt": abgelehnt_anzahl,
        "ohne_urteil": ohne_urteil_anzahl,
    }


def _write_json_atomically(path: Path, payload: dict[str, object]) -> None:
    """Atomares Schreiben nach dem Muster von ``transcript.write_transcript``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.tmp.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomically(temporary, path, create_only=False)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def schreibe_bauliste(pfad: Path, payload: dict[str, object]) -> None:
    """Schreibe ``bauliste.json`` atomar - billig, wird bei jedem Lauf ueberschrieben."""
    _write_json_atomically(pfad, payload)


def _sicherheit_paar(
    kandidaten: list[Candidate], angenommen_indizes: set[int]
) -> dict[str, dict[str, int]]:
    paare: dict[str, dict[str, int]] = {
        stufe: {"ja": 0, "nein": 0} for stufe in ("hoch", "mittel", "niedrig")
    }
    for candidate in kandidaten:
        zweig = "ja" if candidate.index in angenommen_indizes else "nein"
        paare[candidate.sicherheit][zweig] += 1
    return paare


def _polarisierend_paar(
    kandidaten: list[Candidate],
    angenommen_indizes: set[int],
    polarisierend_je_index: dict[int, bool],
) -> dict[str, int]:
    paar = {"wahr": 0, "falsch": 0}
    for candidate in kandidaten:
        if candidate.index not in angenommen_indizes:
            continue
        schluessel = "wahr" if polarisierend_je_index.get(candidate.index, False) else "falsch"
        paar[schluessel] += 1
    return paar


def _lies_kandidaten_rohdaten(pfad: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Lies ``kandidaten.json`` roh - fuer Wurzelfelder und Kandidaten-Zusatzfelder.

    ``load_candidates``/``parse_candidates`` schneiden sowohl Wurzelfelder wie
    ``modell``, ``kriterien_fassung``, ``lauf``, ``video_name`` als auch
    Kandidaten-Zusatzfelder wie ``polarisierend`` weg - sie sind nicht Teil
    des Kandidaten-Kontrakts (``candidates.py``). Fuer die Trefferquote
    werden sie hier zusaetzlich roh gelesen, denselben Weg entlang wie
    ``polarisierend``. Ein Lese-/Parsefehler kann an dieser Stelle nicht mehr
    auftreten, ohne dass ``load_candidates`` vorher schon gescheitert waere -
    trotzdem still auf leere Werte zurueckfallen statt ein zweites Mal zu
    werfen, das ist nicht der Fehlerpfad dieses Werkzeugs.
    """
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        roh = None
    liste = roh.get("kandidaten") if isinstance(roh, dict) else None
    kandidaten_roh = (
        [item for item in liste if isinstance(item, dict)] if isinstance(liste, list) else []
    )
    wurzelfelder: dict[str, object] = {
        "modell": "unbekannt",
        "kriterien_fassung": "unbekannt",
        "video_name": "unbekannt",
        "lauf": None,
    }
    if isinstance(roh, dict):
        for feld in ("modell", "kriterien_fassung", "video_name"):
            wert = roh.get(feld)
            if isinstance(wert, str) and wert.strip():
                wurzelfelder[feld] = wert
        wurzelfelder["lauf"] = roh.get("lauf")
    return kandidaten_roh, wurzelfelder


def trefferquote_eintrag(
    *,
    video_name: str,
    lauf: int | str | None,
    modell: str,
    kriterien_fassung: str,
    kandidaten: list[Candidate],
    angenommen: list[Candidate],
    abgelehnt: list[Candidate],
    ohne_urteil: list[Candidate],
    polarisierend_je_index: dict[int, bool] | None = None,
) -> dict[str, object]:
    """Baue einen Eintrag fuer ``trefferquote.json`` - reine Zaehlung, keine Wertung.

    Die Sicherheits- und Polarisierend-Paare zaehlen ``ja``/``wahr`` gegen
    ``angenommen`` - alles andere (abgelehnt UND ohne Urteil) gilt als
    Gegenstueck. ``polarisierend_je_index`` kommt aus der rohen
    ``kandidaten.json`` (das Feld ist in :class:`Candidate` nicht enthalten,
    siehe Modul-Docstring von ``candidates.py`` zu Zusatzfeldern) und wird
    nur fuer die tatsaechlich angenommenen Kandidaten ausgewertet.
    """
    angenommen_indizes = {candidate.index for candidate in angenommen}
    gesamt = len(kandidaten)
    quote = round(len(angenommen) / gesamt, 3) if gesamt else 0.0
    im_ziel_ja = sum(
        1
        for candidate in angenommen
        if _ZIELBEREICH_MIN_MS <= candidate.duration_ms <= _ZIELBEREICH_MAX_MS
    )
    return {
        "video_name": video_name,
        "lauf": lauf,
        "modell": modell,
        "kriterien_fassung": kriterien_fassung,
        "kandidaten_gesamt": gesamt,
        "angenommen": len(angenommen),
        "abgelehnt": len(abgelehnt),
        "ohne_urteil": len(ohne_urteil),
        "quote": quote,
        "sicherheit": _sicherheit_paar(kandidaten, angenommen_indizes),
        "polarisierend": _polarisierend_paar(
            kandidaten, angenommen_indizes, polarisierend_je_index or {}
        ),
        "im_zielbereich_ja": im_ziel_ja,
        "im_zielbereich_nein": len(angenommen) - im_ziel_ja,
    }


def schreibe_trefferquote(pfad: Path, eintrag: dict[str, object]) -> None:
    """Haenge ``eintrag`` an ``trefferquote.json`` an - atomar, nie ueberschreibend.

    Existiert bereits ein Eintrag mit derselben Kombination aus
    ``video_name`` und ``lauf``, passiert nichts - der Aufrufer (``main``)
    prueft das vorab und meldet es, damit ein Doppellauf nicht zweimal
    zaehlt.
    """
    if pfad.is_file():
        try:
            bestehend = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            bestehend = {}
    else:
        bestehend = {}
    eintraege = bestehend.get("eintraege") if isinstance(bestehend, dict) else None
    if not isinstance(eintraege, list):
        eintraege = []
    for vorhanden in eintraege:
        if (
            isinstance(vorhanden, dict)
            and vorhanden.get("video_name") == eintrag.get("video_name")
            and vorhanden.get("lauf") == eintrag.get("lauf")
        ):
            return
    eintraege.append(eintrag)
    payload: dict[str, object] = {
        "schema_version": TREFFERQUOTE_SCHEMA_VERSION,
        "eintraege": eintraege,
    }
    _write_json_atomically(pfad, payload)


def _hat_bestehenden_eintrag(pfad: Path, *, video_name: str, lauf: int | str | None) -> bool:
    if not pfad.is_file():
        return False
    try:
        bestehend = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    eintraege = bestehend.get("eintraege") if isinstance(bestehend, dict) else None
    if not isinstance(eintraege, list):
        return False
    return any(
        isinstance(vorhanden, dict)
        and vorhanden.get("video_name") == video_name
        and vorhanden.get("lauf") == lauf
        for vorhanden in eintraege
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: aus Kandidaten plus Urteilen eine Bauliste machen (und Trefferquote fortschreiben)."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Shorts-Auswahl: aus kandidaten.json plus Urteilen eine bauliste.json machen"
    )
    parser.add_argument("job_path", type=Path)
    parser.add_argument("--kandidaten", type=Path, default=None)
    parser.add_argument("--urteile", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--keine-trefferquote", action="store_true")
    args = parser.parse_args(argv)

    job_dir: Path = args.job_path
    kandidaten_path = args.kandidaten or (job_dir / "kandidaten.json")
    urteile_path = args.urteile
    output_path = args.output or (job_dir / BAULISTE_FILE_NAME)

    try:
        kandidaten = load_candidates(kandidaten_path)
    except CandidatesSchemaError as exc:
        print(f"ANGEHALTEN [kandidaten_unlesbar]: {exc}")
        return _CODE_KANDIDATEN_UNLESBAR
    except OSError as exc:
        print(f"ANGEHALTEN [kandidaten_unlesbar]: {kandidaten_path} nicht lesbar: {exc}")
        return _CODE_KANDIDATEN_UNLESBAR

    if urteile_path is None:
        urteile_path = juengste_urteilsdatei(job_dir)
        if urteile_path is None:
            print(f"ANGEHALTEN [keine_urteilsdatei]: keine urteile*.json in {job_dir} gefunden")
            return _CODE_KEINE_URTEILSDATEI
    elif not urteile_path.is_file():
        print(f"ANGEHALTEN [keine_urteilsdatei]: {urteile_path} nicht gefunden")
        return _CODE_KEINE_URTEILSDATEI

    try:
        json.loads(urteile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ANGEHALTEN [urteile_kein_json]: {urteile_path} nicht als JSON lesbar: {exc}")
        return _CODE_URTEILE_KEIN_JSON
    urteile = load_urteile(urteile_path)

    abweichungen = pruefe_uebereinstimmung(kandidaten, urteile)
    if abweichungen:
        print("ANGEHALTEN [urteile_abweichung]: Urteile passen nicht zu den Kandidaten")
        for zeile in abweichungen:
            print(zeile)
        return _CODE_URTEILE_ABWEICHUNG

    angenommen, abgelehnt, ohne_urteil = waehle_kandidaten(kandidaten, urteile)
    if not angenommen:
        print("ANGEHALTEN [keine_annahmen]: null angenommene Kandidaten")
        return _CODE_KEINE_ANNAHMEN

    payload = baue_bauliste_payload(
        angenommen=angenommen,
        abgelehnt_anzahl=len(abgelehnt),
        ohne_urteil_anzahl=len(ohne_urteil),
        stammt_aus=kandidaten_path.name,
        urteile_aus=urteile_path.name,
    )
    schreibe_bauliste(output_path, payload)

    if not args.keine_trefferquote:
        kandidaten_roh, wurzelfelder = _lies_kandidaten_rohdaten(kandidaten_path)
        video_name = str(wurzelfelder["video_name"])
        lauf = wurzelfelder["lauf"]
        assert isinstance(lauf, int | str) or lauf is None
        if _hat_bestehenden_eintrag(TREFFERQUOTE_PFAD, video_name=video_name, lauf=lauf):
            print(
                f"Trefferquote-Eintrag fuer video_name={video_name!r} lauf={lauf!r} "
                f"existiert bereits in {TREFFERQUOTE_PFAD} - nichts angehaengt"
            )
        else:
            polarisierend_je_index: dict[int, bool] = {}
            for roh_eintrag in kandidaten_roh:
                roh_index = roh_eintrag.get("index")
                if isinstance(roh_index, int):
                    polarisierend_je_index[roh_index] = bool(
                        roh_eintrag.get("polarisierend", False)
                    )
            eintrag = trefferquote_eintrag(
                video_name=video_name,
                lauf=lauf,
                modell=str(wurzelfelder["modell"]),
                kriterien_fassung=str(wurzelfelder["kriterien_fassung"]),
                kandidaten=kandidaten,
                angenommen=angenommen,
                abgelehnt=abgelehnt,
                ohne_urteil=ohne_urteil,
                polarisierend_je_index=polarisierend_je_index,
            )
            schreibe_trefferquote(TREFFERQUOTE_PFAD, eintrag)

    print(
        f"{len(angenommen)} von {len(kandidaten)} angenommen, {len(abgelehnt)} abgelehnt, "
        f"{len(ohne_urteil)} ohne Urteil -> {output_path}"
    )
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
