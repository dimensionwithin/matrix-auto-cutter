"""Tests fuer Stufe 3b (Auftrag shorts-stufe-3b-modul): Mausverfolgung als Versatzkurve.

Ausschliesslich SYNTHETISCHE Daten - die Zahlen der echten Aufnahme
2026-08-19 17-26-15 stehen im Bericht
``artefakte/repeat/shorts-stufe-3b-modul/BERICHT-2026-08-21.md``, nicht hier.

Reihenfolge wie im Auftrag: die Rueckfaelle zuerst (Punkt 8 - der Normalfall,
nur 7 von 27 gerenderten Aufnahmen haben ueberhaupt ein Cursorprotokoll),
danach Lesen, Median, Geometrie und zuletzt die Kurve selbst.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from matrix_auto_cutter.shorts.chart_crop import CROP_WIDTH, X_OFFSET_DEFAULT, _validate_offset
from matrix_auto_cutter.shorts.cursor_track import (
    AUFNAHMESTART_VERSATZ_MS,
    AUSTRITT_LINKS,
    AUSTRITT_RECHTS,
    AUSTRITT_VERZOEGERUNG_MS,
    CURSOR_KOPFZEILE,
    FAHRT_MAX_MS,
    FAHRT_MIN_MS,
    GRUND_BERECHNET,
    GRUND_KEIN_ANKER,
    GRUND_KEIN_MEDIAN,
    GRUND_KEIN_PROTOKOLL,
    GRUND_SPANNE_AUSSERHALB_GERENDERT,
    GRUND_SPANNE_NICHT_GEDECKT,
    MEDIAN_FENSTER_MS,
    MINDESTVERWEILDAUER_MS,
    RESERVE_PX,
    TRITTZONE_RAND_PX,
    VERSATZ_SPANNE_PX,
    X_OFFSET_MAX_3B,
    X_OFFSET_MIN_3B,
    CursorProtokollError,
    CursorZeile,
    anfangsversatz,
    auf_geraden_versatz,
    austrittsrichtung,
    desktop_zu_leinwand_x,
    desktop_zu_leinwand_y,
    fahrtdauer_frames,
    gleitender_median,
    lies_cursorprotokoll,
    parse_cursorprotokoll,
    quellframe_zu_wanduhrzeit,
    trittzone,
    ueberblendung,
    versatzkurve,
    zeile_ist_im_bild,
    zielversatz,
)
from matrix_auto_cutter.shorts.frame_map import KeepSegment, ms_to_frame

FPS = 60
MESZ = timezone(timedelta(hours=2))
T0 = datetime(2026, 8, 19, 17, 26, 15, tzinfo=MESZ)

# Ein einziges Keep-Segment: die gerenderte Achse ist dann die Quellachse,
# und ein gerendertes Frame f liegt bei T0 + f/60 s. Das haelt die Tests der
# Zustandsfuehrung frei von der Frageabbildung, die frame_map.py eigenstaendig
# testet.
EIN_SEGMENT = (KeepSegment(0, 60_000),)


def protokoll(verlauf, *, von_ms=-2000, bis_ms=40_000, takt_ms=100):
    """Baue ein synthetisches Protokoll: ``verlauf(ms) -> x``, y konstant."""
    zeilen = []
    ms = von_ms
    while ms <= bis_ms:
        zeilen.append(
            CursorZeile(zeit=T0 + timedelta(milliseconds=ms), x=verlauf(ms), y=700)
        )
        ms += takt_ms
    return tuple(zeilen)


def konstant(x):
    """Ein Verlauf, der ueberall denselben x-Wert liefert."""
    return lambda ms: x


def sprung(x_vor, x_nach, bei_ms):
    """Ein Verlauf, der bei ``bei_ms`` einmal von ``x_vor`` auf ``x_nach`` springt."""
    return lambda ms: x_vor if ms < bei_ms else x_nach


def kurve(zeilen, spanne=(0, 600), *, segmente=EIN_SEGMENT, anker=T0, szenenfenster=None):
    """Kurzform fuer versatzkurve mit den Vorgaben dieser Testdatei."""
    return versatzkurve(
        kandidatenspanne=spanne,
        segmente=segmente,
        zeilen=zeilen,
        anker=anker,
        szenenfenster=szenenfenster,
        fps=FPS,
    )


# --- Punkt 8: die Rueckfaelle. Zuerst gebaut, zuerst geprueft. ---------------------


def test_rueckfall_ohne_cursorprotokoll_ist_konstant_416_mit_benanntem_grund():
    ergebnis = kurve(None, (0, 503))
    assert ergebnis.grund == GRUND_KEIN_PROTOKOLL
    assert ergebnis.werte == (X_OFFSET_DEFAULT,) * 503
    assert set(ergebnis.werte) == {416}
    assert ergebnis.fahrten == ()
    assert ergebnis.ist_rueckfall is True


def test_rueckfall_bei_leerem_protokoll():
    ergebnis = kurve(())
    assert ergebnis.grund == GRUND_KEIN_PROTOKOLL
    assert set(ergebnis.werte) == {X_OFFSET_DEFAULT}


def test_rueckfall_ohne_anker():
    ergebnis = kurve(protokoll(konstant(1300)), anker=None)
    assert ergebnis.grund == GRUND_KEIN_ANKER
    assert set(ergebnis.werte) == {X_OFFSET_DEFAULT}


def test_rueckfall_wenn_protokoll_die_spanne_nicht_abdeckt():
    zu_kurz = protokoll(konstant(1300), von_ms=0, bis_ms=3000)
    ergebnis = kurve(zu_kurz, (0, 600))
    assert ergebnis.grund == GRUND_SPANNE_NICHT_GEDECKT
    assert set(ergebnis.werte) == {X_OFFSET_DEFAULT}


def test_rueckfall_wenn_protokoll_vor_der_spanne_beginnt_aber_zu_spaet():
    spaet = protokoll(konstant(1300), von_ms=5000, bis_ms=40_000)
    ergebnis = kurve(spaet, (0, 600))
    assert ergebnis.grund == GRUND_SPANNE_NICHT_GEDECKT


def test_rueckfall_ohne_einen_einzigen_gueltigen_median():
    """Cursor die ganze Spanne ueber auf dem zweiten Monitor: x durchweg negativ."""
    ergebnis = kurve(protokoll(konstant(-1500)))
    assert ergebnis.grund == GRUND_KEIN_MEDIAN
    assert set(ergebnis.werte) == {X_OFFSET_DEFAULT}


def test_rueckfall_wenn_die_spanne_ueber_die_gerenderte_achse_hinausragt():
    ergebnis = versatzkurve(
        kandidatenspanne=(90, 120),
        segmente=(KeepSegment(0, 100),),
        zeilen=protokoll(konstant(1300)),
        anker=T0,
        fps=FPS,
    )
    assert ergebnis.grund == GRUND_SPANNE_AUSSERHALB_GERENDERT
    assert len(ergebnis.werte) == 30


def test_rueckfallkurve_hat_immer_die_laenge_der_spanne():
    for laenge in (1, 17, 503, 1053):
        assert len(kurve(None, (100, 100 + laenge)).werte) == laenge


def test_unsinnige_spanne_ist_ein_aufruffehler_kein_rueckfall():
    with pytest.raises(ValueError):
        kurve(None, (600, 600))
    with pytest.raises(ValueError):
        kurve(None, (600, 500))


# --- Punkt 1: das Cursorprotokoll lesen, fail-closed ------------------------------

GUTES_PROTOKOLL = (
    "zeit,x,y\n"
    "2026-08-19T17:26:15.7515437+02:00,-2128,1166\n"
    "2026-08-19T17:26:15.8745582+02:00,-1647,1346\n"
    "2026-08-19T17:26:15.9989661+02:00,1481,1408\n"
)


def test_parse_liest_zeilen_mit_offset_und_ganzzahlen():
    zeilen = parse_cursorprotokoll(GUTES_PROTOKOLL)
    assert len(zeilen) == 3
    assert zeilen[0].x == -2128
    assert zeilen[0].y == 1166
    assert zeilen[2].x == 1481
    assert zeilen[0].zeit.utcoffset() == timedelta(hours=2)
    assert zeilen[0].zeit < zeilen[1].zeit < zeilen[2].zeit


def test_parse_akzeptiert_kopfzeile_ohne_datenzeilen():
    assert parse_cursorprotokoll(CURSOR_KOPFZEILE + "\n") == ()


def test_parse_akzeptiert_bom_und_crlf():
    zeilen = parse_cursorprotokoll("﻿zeit,x,y\r\n2026-08-19T17:26:15+02:00,10,20\r\n")
    assert len(zeilen) == 1
    assert zeilen[0].x == 10


def test_parse_verwirft_falsche_kopfzeile():
    with pytest.raises(CursorProtokollError, match="Kopfzeile"):
        parse_cursorprotokoll("zeit,x\n2026-08-19T17:26:15+02:00,10\n")


def test_parse_verwirft_leere_datei():
    with pytest.raises(CursorProtokollError, match="leere Datei"):
        parse_cursorprotokoll("")


def test_parse_verwirft_zeile_mit_falscher_feldzahl():
    with pytest.raises(CursorProtokollError, match="Zeile 2: 2 Felder"):
        parse_cursorprotokoll("zeit,x,y\n2026-08-19T17:26:15+02:00,10\n")


def test_parse_verwirft_nicht_ganzzahliges_x():
    with pytest.raises(CursorProtokollError, match="x ist keine Ganzzahl"):
        parse_cursorprotokoll("zeit,x,y\n2026-08-19T17:26:15+02:00,10.5,20\n")


def test_parse_verwirft_nicht_ganzzahliges_y():
    with pytest.raises(CursorProtokollError, match="y ist keine Ganzzahl"):
        parse_cursorprotokoll("zeit,x,y\n2026-08-19T17:26:15+02:00,10,zwanzig\n")


def test_parse_verwirft_zeitstempel_ohne_zeitzonen_offset():
    with pytest.raises(CursorProtokollError, match="ohne Zeitzonen-Offset"):
        parse_cursorprotokoll("zeit,x,y\n2026-08-19T17:26:15,10,20\n")


def test_parse_verwirft_kaputten_zeitstempel():
    with pytest.raises(CursorProtokollError, match="ISO-8601"):
        parse_cursorprotokoll("zeit,x,y\ngestern abend,10,20\n")


def test_parse_verwirft_ruecklaeufige_zeit():
    text = (
        "zeit,x,y\n"
        "2026-08-19T17:26:16+02:00,10,20\n"
        "2026-08-19T17:26:15+02:00,10,20\n"
    )
    with pytest.raises(CursorProtokollError, match="liegt vor"):
        parse_cursorprotokoll(text)


def test_parse_verwirft_leere_zeile_mittendrin_statt_sie_zu_ueberspringen():
    text = "zeit,x,y\n2026-08-19T17:26:15+02:00,10,20\n\n2026-08-19T17:26:16+02:00,10,20\n"
    with pytest.raises(CursorProtokollError, match="leere Zeile"):
        parse_cursorprotokoll(text)


def test_parse_nennt_die_quelle_in_der_meldung():
    with pytest.raises(CursorProtokollError, match="meine-datei.csv"):
        parse_cursorprotokoll("falsch\n", quelle="meine-datei.csv")


def test_lies_cursorprotokoll_von_der_platte(tmp_path):
    pfad = tmp_path / "cursor-test.csv"
    pfad.write_text(GUTES_PROTOKOLL, encoding="utf-8")
    assert len(lies_cursorprotokoll(pfad)) == 3


def test_lies_cursorprotokoll_nennt_den_pfad_bei_fehler(tmp_path):
    pfad = tmp_path / "kaputt.csv"
    pfad.write_text("zeit,x\n", encoding="utf-8")
    with pytest.raises(CursorProtokollError, match="kaputt.csv"):
        lies_cursorprotokoll(pfad)


# --- Teil 2: die Zeitbruecke ------------------------------------------------------


def test_aufnahmestart_versatz_ist_noch_nicht_gemessen():
    """Der Wert wird im Auftrag shorts-anker-kalibrierung gemessen, nicht geraten."""
    assert AUFNAHMESTART_VERSATZ_MS == 0


def test_quellframe_zu_wanduhrzeit_rechnet_frames_in_sekunden():
    assert quellframe_zu_wanduhrzeit(0, T0) == T0
    assert quellframe_zu_wanduhrzeit(60, T0) == T0 + timedelta(seconds=1)
    assert quellframe_zu_wanduhrzeit(48_000, T0) == T0 + timedelta(seconds=800)


def test_quellframe_zu_wanduhrzeit_ist_ganzzahlig_und_driftet_nicht():
    """Ueber 48574 Frames darf sich kein Gleitkommafehler aufsummieren."""
    for frame in (1, 7, 12_345, 48_574):
        erwartet = T0 + timedelta(microseconds=frame * 1_000_000 // 60)
        assert quellframe_zu_wanduhrzeit(frame, T0) == erwartet


def test_quellframe_zu_wanduhrzeit_verlangt_anker_mit_offset():
    with pytest.raises(ValueError, match="Zeitzonen-Offset"):
        quellframe_zu_wanduhrzeit(0, datetime(2026, 8, 19, 17, 26, 15))


def test_quellframe_zu_wanduhrzeit_verwirft_negatives_frame_und_fps_null():
    with pytest.raises(ValueError):
        quellframe_zu_wanduhrzeit(-1, T0)
    with pytest.raises(ValueError):
        quellframe_zu_wanduhrzeit(1, T0, fps=0)


# --- N1: der Ortsbezug, Bildschirmspalte -> Leinwandspalte ------------------------


def test_abbildung_streckt_die_desktopspalte_auf_die_leinwandspalte():
    """Die Quelle steht bei pos.x 0 mit scale.x 1,107 - eine Streckung, keine Gleichheit."""
    assert desktop_zu_leinwand_x(0) == 0
    assert desktop_zu_leinwand_x(1000) == 1107
    assert desktop_zu_leinwand_x(2000) == 2214


def test_rechte_grenze_ist_der_von_obs_weggeschnittene_streifen():
    """N1d: ab Desktopspalte 2313 liegt der Zeiger ausserhalb der 2560er Leinwand."""
    assert desktop_zu_leinwand_x(2311) == 2558
    assert desktop_zu_leinwand_x(2312) == 2559  # letzte Leinwandspalte
    assert desktop_zu_leinwand_x(2313) == 2560  # erste weggeschnittene


def test_zeiger_im_weggeschnittenen_streifen_ist_ein_austritt_nach_rechts():
    """N1d: NICHT wie 'Cursor nicht im Bild' - das ist links."""
    zeile = CursorZeile(T0, 2400, 700)
    assert zeile_ist_im_bild(zeile)  # auf Bildschirm 1, nur nicht im Ausschnitt
    median = gleitender_median((zeile,), T0)
    assert median is not None and median > 2559
    assert austrittsrichtung(X_OFFSET_MIN_3B, median) == AUSTRITT_RECHTS
    assert zielversatz(median, AUSTRITT_RECHTS) == X_OFFSET_MAX_3B


def test_zeile_ausserhalb_der_leinwandhoehe_ist_nicht_im_bild():
    """N1d: oben die Browser-Leiste, unten die Taskleiste."""
    assert desktop_zu_leinwand_y(73) == -1
    assert desktop_zu_leinwand_y(74) == 0
    assert desktop_zu_leinwand_y(1373) == 1439
    assert desktop_zu_leinwand_y(1374) == 1440
    assert not zeile_ist_im_bild(CursorZeile(T0, 800, 73))
    assert zeile_ist_im_bild(CursorZeile(T0, 800, 74))
    assert zeile_ist_im_bild(CursorZeile(T0, 800, 1373))
    assert not zeile_ist_im_bild(CursorZeile(T0, 800, 1374))
    assert not zeile_ist_im_bild(CursorZeile(T0, -800, 700))


def test_median_verwirft_zeilen_ausserhalb_der_leinwandhoehe():
    zeilen = (
        CursorZeile(T0, 300, 1400),  # Taskleiste - nicht im Bild
        CursorZeile(T0 + timedelta(milliseconds=50), 800, 700),
        CursorZeile(T0 + timedelta(milliseconds=100), 300, 10),  # Browser-Leiste
    )
    assert gleitender_median(zeilen, T0 + timedelta(milliseconds=50)) == 885


def test_median_ist_none_wenn_alle_zeilen_ausserhalb_der_hoehe_liegen():
    zeilen = tuple(
        CursorZeile(T0 + timedelta(milliseconds=ms), 800, 1400) for ms in (0, 50, 100)
    )
    assert gleitender_median(zeilen, T0 + timedelta(milliseconds=50)) is None


# --- Punkt 2: der gleitende Median ------------------------------------------------


def test_median_fenster_ist_in_millisekunden_nicht_in_zeilen():
    assert MEDIAN_FENSTER_MS == 375


def test_median_mittelt_ueber_das_zeitfenster_nicht_ueber_eine_zeilenzahl():
    """Bei 100-ms-Takt liegen 4 Zeilen im 375-ms-Fenster, bei 124-ms-Takt nur 3."""
    dicht = protokoll(lambda ms: 1000 + ms, von_ms=0, bis_ms=1000, takt_ms=100)
    duenn = protokoll(lambda ms: 1000 + ms, von_ms=0, bis_ms=1000, takt_ms=124)
    zeit = T0 + timedelta(milliseconds=500)
    assert gleitender_median(dicht, zeit) is not None
    assert gleitender_median(duenn, zeit) is not None
    # Beide Fenster decken dieselben 375 ms ab, also fast denselben Median -
    # eine feste Zeilenzahl haette hier zwei verschieden breite Fenster ergeben.
    assert abs(gleitender_median(dicht, zeit) - gleitender_median(duenn, zeit)) < 100


def test_median_ignoriert_negative_x():
    zeilen = (
        CursorZeile(T0, -2000, 700),
        CursorZeile(T0 + timedelta(milliseconds=50), 800, 700),
        CursorZeile(T0 + timedelta(milliseconds=100), -1500, 700),
    )
    # 800 ist eine DESKTOPspalte; der Median kommt als LEINWANDspalte heraus.
    assert gleitender_median(zeilen, T0 + timedelta(milliseconds=50)) == 885


def test_median_ist_none_wenn_das_fenster_kein_x_ab_null_enthaelt():
    zeilen = protokoll(konstant(-1200), von_ms=0, bis_ms=1000)
    assert gleitender_median(zeilen, T0 + timedelta(milliseconds=500)) is None


def test_median_ist_none_wenn_das_fenster_leer_ist():
    zeilen = protokoll(konstant(900), von_ms=0, bis_ms=200)
    assert gleitender_median(zeilen, T0 + timedelta(seconds=30)) is None


def test_median_bei_gerader_anzahl_ist_das_abgerundete_mittel():
    zeilen = (
        CursorZeile(T0, 100, 700),
        CursorZeile(T0 + timedelta(milliseconds=50), 201, 700),
    )
    # Desktopmittel (100 + 201) // 2 = 150, abgebildet auf Leinwandspalte 166.
    assert gleitender_median(zeilen, T0 + timedelta(milliseconds=25)) == 166


def test_median_bei_ungerader_anzahl_ist_der_mittlere_wert():
    zeilen = (
        CursorZeile(T0, 100, 700),
        CursorZeile(T0 + timedelta(milliseconds=50), 900, 700),
        CursorZeile(T0 + timedelta(milliseconds=100), 200, 700),
    )
    # Mittlerer Desktopwert 200, abgebildet auf Leinwandspalte 221.
    assert gleitender_median(zeilen, T0 + timedelta(milliseconds=50)) == 221


def test_median_laesst_sich_auf_ein_segment_beschneiden():
    """Mit von/bis filtert die Funktion nie ueber eine Keep-Segment-Naht hinweg."""
    zeilen = (
        CursorZeile(T0, 100, 700),
        CursorZeile(T0 + timedelta(milliseconds=100), 900, 700),
    )
    zeit = T0 + timedelta(milliseconds=50)
    assert gleitender_median(zeilen, zeit) == 553  # Desktop 500
    assert gleitender_median(zeilen, zeit, bis=T0 + timedelta(milliseconds=50)) == 110
    assert gleitender_median(zeilen, zeit, von=T0 + timedelta(milliseconds=60)) == 996


def test_median_verwirft_nicht_positives_fenster():
    with pytest.raises(ValueError):
        gleitender_median(protokoll(konstant(800)), T0, fenster_ms=0)


# --- Punkt 3 und 4: Geometrie und Startwerte --------------------------------------


def test_versatzbereich_dieser_stufe_ist_enger_als_der_erlaubte():
    assert X_OFFSET_MIN_3B == 482
    assert X_OFFSET_MAX_3B == 832
    assert VERSATZ_SPANNE_PX == 350


def test_linker_anschlag_liegt_rechts_der_avatarbox():
    """482 ist die erste Spalte rechts der AVATAR-Box (Leinwandspalten 0..481)."""
    assert X_OFFSET_MIN_3B == 482
    assert X_OFFSET_MIN_3B > 481
    # Der Rueckfall bleibt dagegen beim heutigen, abgenommenen Bild.
    assert X_OFFSET_DEFAULT == 416
    assert X_OFFSET_MIN_3B != X_OFFSET_DEFAULT


def test_benannte_startwerte_stehen_wie_im_auftrag():
    assert TRITTZONE_RAND_PX == 100
    assert AUSTRITT_VERZOEGERUNG_MS == 300
    assert MINDESTVERWEILDAUER_MS == 1000
    assert RESERVE_PX == 150
    assert FAHRT_MIN_MS == 350
    assert FAHRT_MAX_MS == 700


def test_auf_geraden_versatz_rundet_ab_und_klemmt():
    assert auf_geraden_versatz(500) == 500
    assert auf_geraden_versatz(501) == 500
    assert auf_geraden_versatz(0) == X_OFFSET_MIN_3B
    assert auf_geraden_versatz(-9999) == X_OFFSET_MIN_3B
    assert auf_geraden_versatz(9999) == X_OFFSET_MAX_3B
    assert auf_geraden_versatz(415) == X_OFFSET_MIN_3B


def test_auf_geraden_versatz_liefert_immer_einen_gueltigen_ausschnittversatz():
    for roh in range(-2000, 3000, 7):
        assert _validate_offset(auf_geraden_versatz(roh), context="test") is not None


def test_trittzone_laesst_beidseitig_den_rand_frei():
    assert trittzone(416) == (516, 2043)
    assert trittzone(0) == (TRITTZONE_RAND_PX, CROP_WIDTH - 1 - TRITTZONE_RAND_PX)


def test_austrittsrichtung_erkennt_beide_seiten_und_das_halten():
    assert austrittsrichtung(416, 1200) is None
    assert austrittsrichtung(416, 516) is None
    assert austrittsrichtung(416, 2043) is None
    assert austrittsrichtung(416, 515) == AUSTRITT_LINKS
    assert austrittsrichtung(416, 2044) == AUSTRITT_RECHTS


def test_undefinierter_median_gilt_als_austritt_nach_links():
    """Punkt 5: der zweite Monitor liegt links."""
    assert austrittsrichtung(416, None) == AUSTRITT_LINKS
    assert austrittsrichtung(832, None) == AUSTRITT_LINKS


def test_zielversatz_setzt_die_reserve_an_die_austrittskante():
    """Beide Mediane sind so gewaehlt, dass das Ziel ohne Rundung schon gerade ist."""
    abstand = TRITTZONE_RAND_PX + RESERVE_PX
    ziel = zielversatz(900, AUSTRITT_LINKS)
    assert ziel == auf_geraden_versatz(900 - abstand)
    assert 900 - ziel == abstand  # Median steht 250 px von der LINKEN Kante
    ziel = zielversatz(2201, AUSTRITT_RECHTS)
    assert (ziel + CROP_WIDTH - 1) - 2201 == abstand  # 250 px von der RECHTEN Kante


def test_zielversatz_weicht_durch_die_gerade_rundung_um_hoechstens_ein_pixel_ab():
    abstand = TRITTZONE_RAND_PX + RESERVE_PX
    for median in range(700, 2400):
        links = zielversatz(median, AUSTRITT_LINKS)
        if X_OFFSET_MIN_3B < links < X_OFFSET_MAX_3B:
            assert 0 <= (median - links) - abstand <= 1
        rechts = zielversatz(median, AUSTRITT_RECHTS)
        if X_OFFSET_MIN_3B < rechts < X_OFFSET_MAX_3B:
            assert 0 <= abstand - ((rechts + CROP_WIDTH - 1) - median) <= 1


def test_zielversatz_ist_nicht_die_mitte():
    mitte = auf_geraden_versatz(900 - (CROP_WIDTH - 1) // 2)
    assert zielversatz(900, AUSTRITT_LINKS) != mitte


def test_zielversatz_bei_undefiniertem_median_ist_der_linke_anschlag():
    assert zielversatz(None, AUSTRITT_LINKS) == X_OFFSET_MIN_3B
    assert zielversatz(None, AUSTRITT_RECHTS) == X_OFFSET_MIN_3B


def test_anfangsversatz_zentriert_NICHT_sondern_bleibt_am_linken_anschlag():
    """N3: Liegt der Median am linken Anschlag schon in der Trittzone, bleibt es dabei."""
    links, rechts = trittzone(X_OFFSET_MIN_3B)
    assert (links, rechts) == (582, 2109)
    for median in (582, 1300, 1600, 2109):
        assert anfangsversatz(median) == X_OFFSET_MIN_3B
    # Ausdruecklich NICHT die Mitte - die zentrierte Rahmung ist weggefallen.
    assert auf_geraden_versatz(1600 - (CROP_WIDTH - 1) // 2) == 736
    assert anfangsversatz(1600) == X_OFFSET_MIN_3B != 736


def test_anfangsversatz_setzt_einmal_nach_wenn_der_median_nicht_in_der_trittzone_liegt():
    """N3: dann gilt zielversatz - hart, ohne Fahrt, am kleinstmoeglichen Ort."""
    assert anfangsversatz(2110) == zielversatz(2110, AUSTRITT_RECHTS)
    assert anfangsversatz(2110) > X_OFFSET_MIN_3B
    # Der Zeiger jenseits der Leinwand (weggeschnittene Watchlist) dockt rechts an.
    assert anfangsversatz(2600) == X_OFFSET_MAX_3B


def test_anfangsversatz_ohne_median_ist_der_linke_anschlag():
    assert anfangsversatz(None) == X_OFFSET_MIN_3B


def test_fahrtdauer_ist_linear_ueber_416_px_nicht_ueber_832():
    assert fahrtdauer_frames(0) == ms_to_frame(FAHRT_MIN_MS, FPS)
    assert fahrtdauer_frames(VERSATZ_SPANNE_PX) == ms_to_frame(FAHRT_MAX_MS, FPS)
    assert fahrtdauer_frames(832) == fahrtdauer_frames(VERSATZ_SPANNE_PX)
    halbe = fahrtdauer_frames(VERSATZ_SPANNE_PX // 2)
    assert fahrtdauer_frames(0) < halbe < fahrtdauer_frames(VERSATZ_SPANNE_PX)


def test_fahrtdauer_ist_richtungsunabhaengig_und_nie_null():
    assert fahrtdauer_frames(-300) == fahrtdauer_frames(300)
    assert fahrtdauer_frames(0, fps=1) >= 1


def test_ueberblendung_hat_ableitung_null_an_beiden_enden():
    assert ueberblendung(0.0) == 0.0
    assert ueberblendung(1.0) == 1.0
    assert ueberblendung(0.5) == pytest.approx(0.5)
    h = 1e-6
    assert (ueberblendung(h) - ueberblendung(0.0)) / h == pytest.approx(0.0, abs=1e-4)
    assert (ueberblendung(1.0) - ueberblendung(1.0 - h)) / h == pytest.approx(0.0, abs=1e-4)


def test_ueberblendung_ist_monoton_und_geklemmt():
    werte = [ueberblendung(i / 100) for i in range(101)]
    assert werte == sorted(werte)
    assert ueberblendung(-5.0) == 0.0
    assert ueberblendung(5.0) == 1.0


# --- Punkt 4 bis 7: die Kurve -----------------------------------------------------


def test_kurve_hat_die_laenge_der_spanne_und_nur_gueltige_werte():
    ergebnis = kurve(protokoll(konstant(1300)), (0, 600))
    assert len(ergebnis.werte) == 600
    assert ergebnis.grund == GRUND_BERECHNET
    for wert in ergebnis.werte:
        assert _validate_offset(wert, context="test") == wert
        assert X_OFFSET_MIN_3B <= wert <= X_OFFSET_MAX_3B


def test_ruhiger_cursor_in_der_trittzone_erzeugt_keine_einzige_fahrt():
    ergebnis = kurve(protokoll(konstant(1300)))
    assert ergebnis.fahrten == ()
    assert len(set(ergebnis.werte)) == 1


def test_kurve_geht_richtig_gerahmt_auf_statt_hineinzufahren():
    """Punkt 6: das erste Frame steht schon richtig, es faehrt nicht erst hin."""
    ergebnis = kurve(protokoll(konstant(1300)))
    assert ergebnis.werte[0] == anfangsversatz(1300)
    assert ergebnis.werte[0] == ergebnis.werte[1]


def test_anfangsversatz_ist_der_anschlag_wenn_die_spanne_negativ_beginnt():
    """Beide bekannten Protokolle beginnen mit negativem x - das kommt oft vor."""
    verlauf = sprung(-1500, 1300, bei_ms=5000)
    ergebnis = kurve(protokoll(verlauf), (0, 600))
    assert ergebnis.grund == GRUND_BERECHNET
    assert ergebnis.werte[0] == X_OFFSET_MIN_3B


def test_kurzer_wischer_unter_der_verzoegerung_loest_keine_fahrt_aus():
    def wischer(ms):
        return 300 if 2000 <= ms < 2000 + AUSTRITT_VERZOEGERUNG_MS - 100 else 1300

    ergebnis = kurve(protokoll(wischer))
    assert ergebnis.fahrten == ()


def test_anhaltender_austritt_nach_links_faehrt_genau_einmal():
    """Der Kandidat geht rechts angedockt auf - erst von dort ist Weg nach links da."""
    ergebnis = kurve(protokoll(sprung(2400, 700, bei_ms=2000)))
    assert len(ergebnis.fahrten) == 1
    fahrt = ergebnis.fahrten[0]
    assert fahrt.richtung == AUSTRITT_LINKS
    assert fahrt.von == ergebnis.werte[0] == X_OFFSET_MAX_3B
    assert fahrt.nach < fahrt.von
    assert ergebnis.werte[-1] == fahrt.nach


def test_am_linken_anschlag_loest_ein_linksaustritt_keine_fahrt_aus():
    """N2: das Bild dockt an und geht nicht darueber hinaus - es gibt nichts zu fahren."""
    ergebnis = kurve(protokoll(sprung(1300, 500, bei_ms=2000)))
    assert ergebnis.werte[0] == X_OFFSET_MIN_3B
    assert ergebnis.fahrten == ()
    assert set(ergebnis.werte) == {X_OFFSET_MIN_3B}


def test_anhaltender_austritt_nach_rechts_faehrt_nach_rechts():
    ergebnis = kurve(protokoll(sprung(1300, 2400, bei_ms=2000)))
    assert len(ergebnis.fahrten) == 1
    fahrt = ergebnis.fahrten[0]
    assert fahrt.richtung == AUSTRITT_RECHTS
    assert fahrt.nach > fahrt.von
    assert fahrt.nach == zielversatz(desktop_zu_leinwand_x(2400), AUSTRITT_RECHTS)


def test_fahrt_beginnt_erst_nach_der_austrittsverzoegerung():
    ergebnis = kurve(protokoll(sprung(2400, 700, bei_ms=2000)))
    fahrt = ergebnis.fahrten[0]
    austritt_frame = ms_to_frame(2000, FPS)
    verzoegerung = ms_to_frame(AUSTRITT_VERZOEGERUNG_MS, FPS)
    assert fahrt.start_frame >= austritt_frame + verzoegerung - 1


def test_fahrt_dauert_so_lange_wie_die_weglaenge_es_vorgibt():
    ergebnis = kurve(protokoll(sprung(1300, 2400, bei_ms=2000)))
    fahrt = ergebnis.fahrten[0]
    assert fahrt.dauer_frames == fahrtdauer_frames(fahrt.nach - fahrt.von)


def test_fahrt_ist_an_beiden_enden_weich():
    """Erster und letzter Schritt der Fahrt sind kleiner als der groesste Schritt."""
    ergebnis = kurve(protokoll(sprung(1300, 2400, bei_ms=2000)))
    fahrt = ergebnis.fahrten[0]
    a = fahrt.start_frame
    schritte = [
        abs(ergebnis.werte[i] - ergebnis.werte[i - 1])
        for i in range(a, a + fahrt.dauer_frames)
    ]
    assert schritte[0] < max(schritte)
    assert schritte[-1] < max(schritte)


def test_cursor_nicht_im_bild_faehrt_an_den_linken_anschlag_und_bleibt_dort():
    """Punkt 5: kein Zuruecksbringen zur Mitte, kein Hin-und-her-Schnappen."""
    ergebnis = kurve(protokoll(sprung(2400, -1800, bei_ms=2000)), (0, 900))
    assert len(ergebnis.fahrten) == 1
    assert ergebnis.fahrten[0].richtung == AUSTRITT_LINKS
    assert ergebnis.fahrten[0].nach == X_OFFSET_MIN_3B
    assert ergebnis.werte[-1] == X_OFFSET_MIN_3B
    ende = ergebnis.fahrten[0].start_frame + ergebnis.fahrten[0].dauer_frames
    assert set(ergebnis.werte[ende:]) == {X_OFFSET_MIN_3B}


def test_am_anschlag_loest_ein_weiterer_linksaustritt_keine_neue_fahrt_aus():
    ergebnis = kurve(protokoll(konstant(-1800)), (0, 900), anker=T0)
    # Ohne gueltigen Median in der ganzen Spanne greift der Rueckfall - hier
    # dagegen mit gueltigem Anfang, danach dauerhaft weg:
    ergebnis = kurve(protokoll(sprung(500, -1800, bei_ms=1000)), (0, 900))
    assert len([f for f in ergebnis.fahrten if f.von == f.nach]) == 0
    assert ergebnis.werte[-1] == X_OFFSET_MIN_3B


def test_mindestverweildauer_verhindert_eine_sofortige_zweite_fahrt():
    def treppe(ms):
        if ms < 2000:
            return 2400
        if ms < 3000:
            return 700
        return 2400

    ergebnis = kurve(protokoll(treppe), (0, 900))
    assert len(ergebnis.fahrten) >= 2
    erste, zweite = ergebnis.fahrten[0], ergebnis.fahrten[1]
    # Gezaehlt wird ab dem LETZTEN Frame der ersten Fahrt, nicht ab dem ersten
    # Frame danach: die Mindestverweildauer beginnt, wenn das Bild steht.
    letztes_frame_erste = erste.start_frame + erste.dauer_frames - 1
    assert zweite.start_frame >= letztes_frame_erste + ms_to_frame(MINDESTVERWEILDAUER_MS, FPS)


# --- N4: eine Fahrt beginnt nicht, wenn sie nicht fertig wird ---------------------


def _kurze_spanne_ohne_fahrt(laenge):
    """Austritt nach rechts kurz vor Schluss - Weg 350 px, also 42 Frames Fahrtdauer."""
    return kurve(protokoll(sprung(1300, 2400, bei_ms=2000)), (0, laenge))


def test_fahrt_beginnt_nicht_wenn_die_kandidatenspanne_vorher_endet():
    """N4: sonst bricht der Short mitten in der Bewegung ab und zerstoert die Schleife."""
    lang = _kurze_spanne_ohne_fahrt(600)
    assert len(lang.fahrten) == 1
    start, dauer = lang.fahrten[0].start_frame, lang.fahrten[0].dauer_frames

    # Genau lang genug: die Fahrt passt vollstaendig hinein.
    knapp = _kurze_spanne_ohne_fahrt(start + dauer)
    assert len(knapp.fahrten) == 1
    assert knapp.fahrten[0].dauer_frames == dauer
    assert knapp.werte[-1] == lang.fahrten[0].nach

    # Ein Frame zu kurz: die Fahrt beginnt gar nicht erst, der Versatz haelt.
    zu_kurz = _kurze_spanne_ohne_fahrt(start + dauer - 1)
    assert zu_kurz.fahrten == ()
    assert set(zu_kurz.werte) == {X_OFFSET_MIN_3B}


def test_fahrt_beginnt_nicht_wenn_das_szenenfenster_vorher_endet():
    """N4: dasselbe am Rand eines Charts-Szenenfensters."""
    lang = _kurze_spanne_ohne_fahrt(600)
    start, dauer = lang.fahrten[0].start_frame, lang.fahrten[0].dauer_frames

    passt = kurve(protokoll(sprung(1300, 2400, bei_ms=2000)), (0, 600),
                  szenenfenster=((0, start + dauer),))
    assert len(passt.fahrten) == 1

    zu_kurz = kurve(protokoll(sprung(1300, 2400, bei_ms=2000)), (0, 600),
                    szenenfenster=((0, start + dauer - 1),))
    assert zu_kurz.fahrten == ()
    assert set(zu_kurz.werte) == {X_OFFSET_MIN_3B}


def test_kein_framesprung_ueber_25_px_ausserhalb_von_naehten():
    for verlauf in (
        sprung(1300, 500, bei_ms=2000),
        sprung(1300, 2400, bei_ms=2000),
        sprung(2400, -1800, bei_ms=2000),
    ):
        ergebnis = kurve(protokoll(verlauf), (0, 900))
        for i in range(1, len(ergebnis.werte)):
            assert abs(ergebnis.werte[i] - ergebnis.werte[i - 1]) <= 25


# --- Punkt 7: Naehte und Szenenraender --------------------------------------------

# Zwei Keep-Segmente: gerendertes Frame 0..299 -> Quellframe 0..299,
# gerendertes Frame 300..899 -> Quellframe 900..1499. Die Naht liegt bei
# gerendertem Frame 300, der Sprung auf der Quellachse betraegt 10 Sekunden.
ZWEI_SEGMENTE = (KeepSegment(0, 300), KeepSegment(900, 1500))


def test_naht_setzt_den_versatz_hart_neu_ohne_fahrt():
    def verlauf(ms):
        return 700 if ms < 8000 else 2300

    ergebnis = kurve(protokoll(verlauf), (0, 600), segmente=ZWEI_SEGMENTE)
    assert ergebnis.naehte == (300,)
    assert ergebnis.werte[299] != ergebnis.werte[300]
    assert ergebnis.werte[300] == anfangsversatz(desktop_zu_leinwand_x(2300))


def test_keine_fahrt_laeuft_ueber_eine_naht():
    def verlauf(ms):
        return 700 if ms < 8000 else 2300

    ergebnis = kurve(protokoll(verlauf), (0, 600), segmente=ZWEI_SEGMENTE)
    for fahrt in ergebnis.fahrten:
        for naht in ergebnis.naehte:
            assert not (fahrt.start_frame < naht < fahrt.start_frame + fahrt.dauer_frames)


def test_naht_ohne_gueltigen_median_haelt_den_bisherigen_versatz():
    def verlauf(ms):
        return 1300 if ms < 8000 else -1800

    ergebnis = kurve(protokoll(verlauf), (0, 400), segmente=ZWEI_SEGMENTE)
    assert ergebnis.naehte == (300,)
    assert ergebnis.werte[300] == ergebnis.werte[299]


def test_median_filtert_nicht_ueber_die_naht_hinweg():
    """Direkt vor der Naht darf kein x aus dem Segment hinter der Naht einfliessen."""

    def verlauf(ms):
        return 700 if ms < 8000 else 2300

    ergebnis = kurve(protokoll(verlauf), (0, 600), segmente=ZWEI_SEGMENTE)
    assert ergebnis.werte[299] == anfangsversatz(700)


def test_ausserhalb_eines_szenenfensters_friert_der_versatz_ein():
    ergebnis = kurve(
        protokoll(sprung(1300, 500, bei_ms=2000)),
        (0, 600),
        szenenfenster=[(0, 100)],
    )
    assert ergebnis.eingefrorene_frames == 500
    assert set(ergebnis.werte[100:]) == {ergebnis.werte[99]}
    assert ergebnis.fahrten == ()


def test_ohne_szenenfenster_gilt_die_ganze_spanne():
    ergebnis = kurve(protokoll(konstant(1300)))
    assert ergebnis.eingefrorene_frames == 0


def test_szenenfenster_deckt_die_ganze_spanne_ab():
    ergebnis = kurve(protokoll(konstant(1300)), (0, 600), szenenfenster=[(0, 600)])
    assert ergebnis.eingefrorene_frames == 0
