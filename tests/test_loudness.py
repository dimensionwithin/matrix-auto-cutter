from __future__ import annotations

import pytest

from matrix_auto_cutter.loudness import (
    ACCEPT_LRA_FLOOR_LU,
    ACCEPT_TP_CEILING_DBTP,
    COMP_THRESHOLD_DB,
    LIMITER_DB,
    MIN_SOURCE_I_LUFS,
    OUTPUT_SAMPLE_RATE,
    SAMPLES_PER_VIDEO_FRAME,
    TARGET_I_LUFS,
    LoudnessMeasurement,
    acceptance_warnings,
    compressor_filter,
    measurement_chain,
    measurement_from_report,
    normalization_type,
    parse_report,
    protocol_line,
    render_chain,
    source_level_warning,
)

MEASURED = LoudnessMeasurement(
    input_i=-30.96,
    input_lra=8.0,
    input_tp=-0.55,
    input_thresh=-41.76,
    target_offset=0.54,
)

REPORT = b"""[Parsed_loudnorm_0 @ 0000021216852280]
{
\t"input_i" : "-30.96",
\t"input_tp" : "-0.55",
\t"input_lra" : "8.00",
\t"input_thresh" : "-41.76",
\t"output_i" : "-13.98",
\t"normalization_type" : "linear",
\t"target_offset" : "0.54"
}
[out#0/null @ 0000021216851f40] video:0KiB audio:221888KiB
"""


def test_compressor_sits_above_the_measured_speech_rms() -> None:
    assert compressor_filter() == (
        "acompressor=threshold=-24dB:ratio=4:attack=5:release=120:knee=6dB"
    )
    assert COMP_THRESHOLD_DB == -24.0


def test_measurement_chain_shares_the_compressor_but_only_measures() -> None:
    chain = measurement_chain()
    assert chain.startswith(compressor_filter() + ",")
    assert chain.endswith("loudnorm=I=-14:TP=-1:LRA=11:print_format=json")
    assert "measured_" not in chain
    assert "alimiter" not in chain
    assert f"I={TARGET_I_LUFS:g}" in chain


def test_render_chain_reuses_the_measurement_and_asks_for_linear() -> None:
    chain = render_chain(MEASURED, 5209)
    assert chain.startswith(compressor_filter() + ",")
    assert (
        "loudnorm=I=-14:TP=-1:LRA=11:measured_I=-30.96:measured_LRA=8:measured_TP=-0.55"
        ":measured_thresh=-41.76:offset=0.54:linear=true:print_format=json"
    ) in chain
    assert (
        f"aresample={OUTPUT_SAMPLE_RATE},alimiter=limit={LIMITER_DB:g}dB:level=false" in chain
    )


def test_render_chain_without_measurement_is_the_single_pass_fallback() -> None:
    chain = render_chain(None, 5209)
    assert "measured_" not in chain and "linear=true" not in chain
    assert "alimiter=limit=-1.5dB:level=false" in chain


@pytest.mark.parametrize("frames", [1, 60, 61, 5209, 5712])
def test_the_chain_ends_clamped_to_the_exact_output_length(frames: int) -> None:
    # end_pts, nicht end_sample: loudnorm laesst die Zeitachse laenger als das
    # Material, die Samplezahl stimmt bereits. end_sample wuerde nie greifen.
    assert SAMPLES_PER_VIDEO_FRAME == 800
    for chain in (render_chain(MEASURED, frames), render_chain(None, frames)):
        assert chain.endswith(f",apad,atrim=end_pts={frames * 800}")
        assert "end_sample" not in chain
        # Die Klemmung sitzt hinter aresample, sonst zaehlte sie in 192 kHz.
        assert chain.index(f"aresample={OUTPUT_SAMPLE_RATE}") < chain.index("atrim=")


@pytest.mark.parametrize(
    ("input_i", "expected"),
    [
        (-30.96, None),
        (-35.85, None),
        (MIN_SOURCE_I_LUFS, None),
        (-45.01, "ungewöhnlich leise"),
        (-49.64, "ungewöhnlich leise"),
    ],
)
def test_an_unusually_quiet_source_is_named_before_the_render(
    input_i: float, expected: str | None
) -> None:
    measured = LoudnessMeasurement(
        input_i=input_i, input_lra=13.9, input_tp=-24.33, input_thresh=-60.34, target_offset=1.11
    )
    warning = source_level_warning(measured)
    if expected is None:
        assert warning is None
    else:
        assert warning is not None and expected in warning and f"{input_i:.2f}" in warning


def test_parse_report_reads_the_last_json_block() -> None:
    report = parse_report(REPORT)
    assert report is not None
    assert report["input_i"] == "-30.96"
    assert report["normalization_type"] == "linear"


@pytest.mark.parametrize(
    "output",
    [
        b"no braces at all",
        b"} closing brace without an opening one",
        b"{ this is not json }",
    ],
)
def test_parse_report_reports_unreadable_output(output: bytes) -> None:
    assert parse_report(output) is None


def test_measurement_from_report_accepts_the_real_values() -> None:
    report = parse_report(REPORT)
    assert report is not None
    assert measurement_from_report(report) == MEASURED


@pytest.mark.parametrize(
    "report",
    [
        {"input_i": "-30.96"},
        {
            "input_i": "not a number",
            "input_lra": "8.00",
            "input_tp": "-0.55",
            "input_thresh": "-41.76",
            "target_offset": "0.54",
        },
        {
            "input_i": "-inf",
            "input_lra": "0.00",
            "input_tp": "-inf",
            "input_thresh": "-70.00",
            "target_offset": "0.00",
        },
        {
            "input_i": "-120.00",
            "input_lra": "8.00",
            "input_tp": "-0.55",
            "input_thresh": "-41.76",
            "target_offset": "0.54",
        },
    ],
)
def test_measurement_from_report_refuses_what_loudnorm_would_refuse(
    report: dict[str, str],
) -> None:
    assert measurement_from_report(report) is None


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ({"normalization_type": "linear"}, "linear"),
        ({"normalization_type": "dynamic"}, "dynamic"),
        ({"normalization_type": "nonsense"}, None),
        ({}, None),
    ],
)
def test_normalization_type_is_bounded(report: dict[str, str], expected: str | None) -> None:
    assert normalization_type(report) == expected


def _achieved(i: float, tp: float, lra: float) -> LoudnessMeasurement:
    return LoudnessMeasurement(
        input_i=i, input_lra=lra, input_tp=tp, input_thresh=-25.89, target_offset=0.1
    )


@pytest.mark.parametrize(
    ("achieved", "reason"),
    [
        # Der abgenommene Lauf 2026-08-09 12-09-50 und die beiden Messläufe
        # müssen ohne Warnung durchgehen.
        (_achieved(-14.34, -1.19, 6.60), None),
        (_achieved(-14.60, -1.00, 4.80), None),
        (_achieved(-15.30, -1.21, 13.20), None),
        (_achieved(-15.51, -1.19, 6.60), "weicht um -1.51 dB"),
        (_achieved(-12.49, -1.19, 6.60), "weicht um +1.51 dB"),
        (_achieved(-14.34, -0.49, 6.60), "True Peak -0.49 dBTP liegt über -0.5 dBTP"),
        (_achieved(-14.34, -1.19, 3.99), "Lautheitsumfang 3.99 LU liegt unter 4 LU"),
    ],
)
def test_acceptance_bounds_pass_the_real_runs_and_catch_the_outliers(
    achieved: LoudnessMeasurement, reason: str | None
) -> None:
    warnings = acceptance_warnings(achieved)
    if reason is None:
        assert warnings == ()
    else:
        assert len(warnings) == 1 and reason in warnings[0]


def test_the_limiter_value_itself_would_never_pass_as_a_bound() -> None:
    # Der True Peak liegt systematisch über dem Limiterwert; eine Schranke dort
    # würde bei jedem Lauf feuern.
    assert ACCEPT_TP_CEILING_DBTP > LIMITER_DB
    assert acceptance_warnings(_achieved(-14.34, LIMITER_DB + 0.3, 6.60)) == ()


def test_several_missed_bounds_are_all_named() -> None:
    warnings = acceptance_warnings(_achieved(-20.0, -0.1, ACCEPT_LRA_FLOOR_LU - 1))
    assert len(warnings) == 3


@pytest.mark.parametrize(
    ("measured", "applied", "expected"),
    [
        (
            _achieved(-14.34, -1.19, 6.60),
            "dynamic",
            "Lautheit: I -14.34 LUFS · TP -1.19 dBTP · LRA 6.60 LU · loudnorm dynamic",
        ),
        (None, None, "Lautheit: nicht messbar · loudnorm unbekannt"),
    ],
)
def test_protocol_line_states_the_result_without_judging_it(
    measured: LoudnessMeasurement | None, applied: str | None, expected: str
) -> None:
    assert protocol_line(measured, applied) == expected
