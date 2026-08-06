"""Tests for the standalone cutcli.py entry point. No real ffmpeg/ffprobe is started."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

import matrix_auto_cutter.repeat.cutcli as cutcli_module
import matrix_auto_cutter.repeat.process as process_module
from matrix_auto_cutter.repeat.cut import KeptSegment
from matrix_auto_cutter.repeat.cutcli import build_ffmpeg_argv, build_filtergraph, main
from matrix_auto_cutter.repeat.process import ProcessResult

_URTEILE_ONE_VERSPRECHER = [
    {
        "datei": "stem",
        "eintragsnummer": 1,
        "erste_passage": {"start_ms": 1_000, "end_ms": 2_000},
        "zweite_passage": {"start_ms": 2_000, "end_ms": 3_000},
        "scores": {"utterance": None, "boundary": 0.9},
        "detektoren": ["boundary"],
        "urteil": "versprecher",
        "notiz": "",
    }
]


class _FakeRunner:
    def __init__(
        self,
        duration_stdout: str = "10.0",
        out_duration_stdout: str | None = None,
        ffmpeg_exit: int = 0,
        ffmpeg_timed_out: bool = False,
        orphan_pids: str = "",
        probe_timed_out: bool = False,
    ) -> None:
        self.calls: list[list[str]] = []
        self.duration_stdout = duration_stdout
        self.out_duration_stdout = (
            out_duration_stdout if out_duration_stdout is not None else duration_stdout
        )
        self.ffmpeg_exit = ffmpeg_exit
        self.ffmpeg_timed_out = ffmpeg_timed_out
        self.orphan_pids = orphan_pids
        self.probe_timed_out = probe_timed_out
        self._probe_calls = 0

    def __call__(self, argv: list[str], timeout_ms: int) -> ProcessResult:
        self.calls.append(argv)
        if argv[0] == "powershell":
            return ProcessResult(0, self.orphan_pids, "", False, 1)
        if "-show_entries" in argv:
            self._probe_calls += 1
            if self.probe_timed_out:
                return ProcessResult(-9, "", "timeout", True, 1)
            stdout = self.duration_stdout if self._probe_calls == 1 else self.out_duration_stdout
            return ProcessResult(0, stdout, "", False, 1)
        if "-filter_complex" in argv:
            if self.ffmpeg_timed_out:
                return ProcessResult(-9, "", "timeout", True, 1)
            out_path = Path(argv[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if self.ffmpeg_exit == 0:
                out_path.write_bytes(b"mp4-bytes")
                return ProcessResult(0, "", "", False, 1)
            return ProcessResult(self.ffmpeg_exit, "", "boom", False, 1)
        raise AssertionError(f"unexpected argv: {argv}")


def _patch_runner(monkeypatch: Any, runner: _FakeRunner) -> None:
    monkeypatch.setattr(cutcli_module, "NativeProcessRunner", lambda: runner)


def _write_urteile(tmp_path: Path, urteile: list[dict] = _URTEILE_ONE_VERSPRECHER) -> Path:
    path = tmp_path / "urteile.json"
    path.write_text(json.dumps(urteile), encoding="utf-8")
    return path


def _write_source(tmp_path: Path) -> Path:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-bytes")
    return source


# --- build_filtergraph / build_ffmpeg_argv (pure) ---------------------------------


def test_build_filtergraph_single_segment() -> None:
    graph = build_filtergraph([KeptSegment(0, 1_000)])
    assert graph == (
        "[0:v]trim=start=0.000:end=1.000,setpts=PTS-STARTPTS[v0];"
        "[0:a]atrim=start=0.000:end=1.000,asetpts=PTS-STARTPTS[a0];"
        "[v0][a0]concat=n=1:v=1:a=1[vout][aout]"
    )


def test_build_filtergraph_multiple_segments() -> None:
    graph = build_filtergraph([KeptSegment(0, 1_000), KeptSegment(2_000, 5_000)])
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vout][aout]" in graph
    assert "start=2.000:end=5.000" in graph


_ONE_SEGMENT = [KeptSegment(0, 1_000)]


def test_build_ffmpeg_argv_omits_audio_bitrate_by_default() -> None:
    argv = build_ffmpeg_argv(
        "ffmpeg", Path("in.mp4"), Path("out.mp4"), _ONE_SEGMENT, "libx264", 18, "slow", None
    )
    assert "-b:a" not in argv
    assert "-n" in argv
    assert "-c:v" in argv and "libx264" in argv
    assert argv[-1] == str(Path("out.mp4"))


def test_build_ffmpeg_argv_includes_audio_bitrate_when_given() -> None:
    argv = build_ffmpeg_argv(
        "ffmpeg", Path("in.mp4"), Path("out.mp4"), _ONE_SEGMENT, "libx264", 18, "slow", "192k"
    )
    assert "-b:a" in argv
    assert argv[argv.index("-b:a") + 1] == "192k"


def test_build_ffmpeg_argv_never_uses_shell_string() -> None:
    argv = build_ffmpeg_argv(
        "ffmpeg", Path("in.mp4"), Path("out.mp4"), _ONE_SEGMENT, "libx264", 18, "slow", None
    )
    assert all(isinstance(a, str) for a in argv)


# --- main() guard rails -------------------------------------------------------------


def test_out_equals_source_returns_exit_2(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    exit_code = main(["--source", str(source), "--urteile", str(urteile), "--out", str(source)])
    assert exit_code == 2


def test_out_already_exists_returns_exit_3(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"
    out.write_bytes(b"already here")
    exit_code = main(["--source", str(source), "--urteile", str(urteile), "--out", str(out)])
    assert exit_code == 3


def test_source_not_found_returns_exit_4(tmp_path: Path, monkeypatch: Any) -> None:
    _patch_runner(monkeypatch, _FakeRunner())
    urteile = _write_urteile(tmp_path)
    exit_code = main(
        [
            "--source",
            str(tmp_path / "missing.mp4"),
            "--urteile",
            str(urteile),
            "--out",
            str(tmp_path / "out.mp4"),
        ]
    )
    assert exit_code == 4


def test_ffprobe_failure_returns_exit_5(tmp_path: Path, monkeypatch: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)

    class _FailingProbe:
        def __call__(self, argv: list[str], timeout_ms: int) -> ProcessResult:
            return ProcessResult(1, "", "probe boom", False, 1)

    _patch_runner(monkeypatch, _FailingProbe())
    exit_code = main(
        ["--source", str(source), "--urteile", str(urteile), "--out", str(tmp_path / "out.mp4")]
    )
    assert exit_code == 5


def test_ffprobe_timeout_returns_exit_7(tmp_path: Path, monkeypatch: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    _patch_runner(monkeypatch, _FakeRunner(probe_timed_out=True))
    exit_code = main(
        ["--source", str(source), "--urteile", str(urteile), "--out", str(tmp_path / "out.mp4")]
    )
    assert exit_code == 7


def test_empty_cut_plan_returns_exit_8(tmp_path: Path, monkeypatch: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(
        tmp_path,
        [
            {
                "datei": "stem",
                "eintragsnummer": 1,
                "erste_passage": {"start_ms": 0, "end_ms": 5_000},
                "zweite_passage": {"start_ms": 5_000, "end_ms": 10_000},
                "scores": {"utterance": None, "boundary": 0.9},
                "detektoren": ["boundary"],
                "urteil": "versprecher",
                "schnitt": "beide",
                "notiz": "",
            }
        ],
    )
    _patch_runner(monkeypatch, _FakeRunner(duration_stdout="10.0"))
    exit_code = main(
        ["--source", str(source), "--urteile", str(urteile), "--out", str(tmp_path / "out.mp4")]
    )
    assert exit_code == 8


# --- dry-run --------------------------------------------------------------------


def test_dry_run_does_not_encode(tmp_path: Path, monkeypatch: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    runner = _FakeRunner(duration_stdout="10.0")
    _patch_runner(monkeypatch, runner)
    out = tmp_path / "out.mp4"
    exit_code = main(
        ["--source", str(source), "--urteile", str(urteile), "--out", str(out), "--dry-run"]
    )
    assert exit_code == 0
    assert not out.exists()
    assert not any("-filter_complex" in call for call in runner.calls)


def test_dry_run_reports_metrics(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    _patch_runner(monkeypatch, _FakeRunner(duration_stdout="10.0"))
    exit_code = main(
        [
            "--source",
            str(source),
            "--urteile",
            str(urteile),
            "--out",
            str(tmp_path / "out.mp4"),
            "--dry-run",
        ]
    )
    assert exit_code == 0
    out_text = capsys.readouterr().out
    assert "Anzahl Stellen" in out_text
    assert "Anzahl Schnitte nach Zusammenfuehrung" in out_text
    assert "Entfernte Dauer" in out_text
    assert "Dauer vorher" in out_text
    assert "Dauer nachher" in out_text
    assert "ffmpeg-Befehlszeile:" in out_text
    assert "filter_complex" in out_text


# --- real (fake-process) encode runs ---------------------------------------------


def test_successful_run_creates_output(tmp_path: Path, monkeypatch: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"
    _patch_runner(monkeypatch, _FakeRunner(duration_stdout="10.0", out_duration_stdout="9.0"))
    exit_code = main(["--source", str(source), "--urteile", str(urteile), "--out", str(out)])
    assert exit_code == 0
    assert out.exists()


def test_ffmpeg_failure_returns_exit_6(tmp_path: Path, monkeypatch: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    _patch_runner(monkeypatch, _FakeRunner(ffmpeg_exit=1))
    exit_code = main(
        ["--source", str(source), "--urteile", str(urteile), "--out", str(tmp_path / "out.mp4")]
    )
    assert exit_code == 6


def test_ffmpeg_timeout_returns_exit_7(tmp_path: Path, monkeypatch: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    _patch_runner(monkeypatch, _FakeRunner(ffmpeg_timed_out=True))
    exit_code = main(
        ["--source", str(source), "--urteile", str(urteile), "--out", str(tmp_path / "out.mp4")]
    )
    assert exit_code == 7


def test_duration_mismatch_warns(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"
    # kept duration is 9000ms (10000 - 1000 removed); probe the OUTPUT as far off (5.0s).
    _patch_runner(monkeypatch, _FakeRunner(duration_stdout="10.0", out_duration_stdout="5.0"))
    exit_code = main(["--source", str(source), "--urteile", str(urteile), "--out", str(out)])
    assert exit_code == 0
    err_text = capsys.readouterr().err
    assert "Warnung" in err_text
    assert "weicht" in err_text


def test_matching_duration_has_no_warning(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"
    _patch_runner(monkeypatch, _FakeRunner(duration_stdout="10.0", out_duration_stdout="9.0"))
    exit_code = main(["--source", str(source), "--urteile", str(urteile), "--out", str(out)])
    assert exit_code == 0
    err_text = capsys.readouterr().err
    assert "Warnung" not in err_text


def test_orphaned_ffmpeg_process_warns(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"
    _patch_runner(
        monkeypatch,
        _FakeRunner(duration_stdout="10.0", out_duration_stdout="9.0", orphan_pids="1234\n5678\n"),
    )
    exit_code = main(["--source", str(source), "--urteile", str(urteile), "--out", str(out)])
    assert exit_code == 0
    err_text = capsys.readouterr().err
    assert "verwaiste ffmpeg" in err_text
    assert "1234" in err_text
    assert "5678" in err_text


def test_no_orphan_warning_when_none_found(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"
    _patch_runner(monkeypatch, _FakeRunner(duration_stdout="10.0", out_duration_stdout="9.0"))
    exit_code = main(["--source", str(source), "--urteile", str(urteile), "--out", str(out)])
    assert exit_code == 0
    err_text = capsys.readouterr().err
    assert "verwaiste ffmpeg" not in err_text


def test_out_probe_failure_after_encode_warns_but_still_succeeds(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"

    class _FailOnSecondProbe:
        def __init__(self) -> None:
            self._probe_calls = 0

        def __call__(self, argv: list[str], timeout_ms: int) -> ProcessResult:
            if argv[0] == "powershell":
                return ProcessResult(0, "", "", False, 1)
            if "-show_entries" in argv:
                self._probe_calls += 1
                if self._probe_calls == 1:
                    return ProcessResult(0, "10.0", "", False, 1)
                return ProcessResult(1, "", "probe boom", False, 1)
            out_path = Path(argv[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"mp4-bytes")
            return ProcessResult(0, "", "", False, 1)

    _patch_runner(monkeypatch, _FailOnSecondProbe())
    exit_code = main(["--source", str(source), "--urteile", str(urteile), "--out", str(out)])
    assert exit_code == 0
    err_text = capsys.readouterr().err
    assert "Nachpruefung" in err_text


def test_orphan_check_nonzero_exit_is_treated_as_no_orphans(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """Get-Process erroring out (e.g. powershell missing) must not block the encode."""
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"

    class _PowershellFailsRunner(_FakeRunner):
        def __call__(self, argv: list[str], timeout_ms: int) -> ProcessResult:
            if argv[0] == "powershell":
                return ProcessResult(1, "", "not found", False, 1)
            return super().__call__(argv, timeout_ms)

    _patch_runner(
        monkeypatch, _PowershellFailsRunner(duration_stdout="10.0", out_duration_stdout="9.0")
    )
    exit_code = main(["--source", str(source), "--urteile", str(urteile), "--out", str(out)])
    assert exit_code == 0
    err_text = capsys.readouterr().err
    assert "verwaiste ffmpeg" not in err_text


def test_module_entry_point_runs_main_under_dunder_main(tmp_path: Path, monkeypatch: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"
    runner = _FakeRunner(duration_stdout="10.0", out_duration_stdout="9.0")
    monkeypatch.setattr(process_module, "NativeProcessRunner", lambda: runner)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prog",
            "--source",
            str(source),
            "--urteile",
            str(urteile),
            "--out",
            str(out),
            "--dry-run",
        ],
    )
    sys.modules.pop("matrix_auto_cutter.repeat.cutcli", None)
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("matrix_auto_cutter.repeat.cutcli", run_name="__main__")
    assert excinfo.value.code == 0


def test_custom_encoder_flags_are_used(tmp_path: Path, monkeypatch: Any) -> None:
    source = _write_source(tmp_path)
    urteile = _write_urteile(tmp_path)
    out = tmp_path / "out.mp4"
    runner = _FakeRunner(duration_stdout="10.0", out_duration_stdout="9.0")
    _patch_runner(monkeypatch, runner)
    exit_code = main(
        [
            "--source",
            str(source),
            "--urteile",
            str(urteile),
            "--out",
            str(out),
            "--video-codec",
            "h264_nvenc",
            "--crf",
            "23",
            "--preset",
            "fast",
            "--audio-bitrate",
            "192k",
        ]
    )
    assert exit_code == 0
    encode_call = next(call for call in runner.calls if "-filter_complex" in call)
    assert "h264_nvenc" in encode_call
    assert "23" in encode_call
    assert "fast" in encode_call
    assert "192k" in encode_call
