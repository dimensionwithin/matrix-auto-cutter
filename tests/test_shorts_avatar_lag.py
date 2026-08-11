"""Tests für die produktive Lagmessung (Auftrag 11, Eingriff 1).

Reine Rechenlogik (FFT-Kreuzkorrelation, WAV-Lesen, Vorzeichenkontrolle) wird
ohne echtes ffmpeg getestet. :func:`measure_lag` selbst wird mit einem
gefälschten ``subprocess.run`` getestet - kein echtes Video, kein echtes
ffmpeg läuft in diesen Tests. Die Reproduktion der drei bekannten Läufe aus
``LAGMESSUNG-2026-08-11.md`` steht im Abnahme-Bericht
(``NACHBESSERUNG-STUFE-1-2026-08-11.md``), nicht hier - sie braucht echtes
Audiomaterial.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from matrix_auto_cutter.shorts import avatar_lag as al


def _write_mono_wav(
    path: Path, samples: np.ndarray, *, sample_rate: int = al.SAMPLE_RATE_HZ
) -> None:
    clipped = np.clip(samples, -1.0, 1.0)
    ints = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(ints.tobytes())


# --- fft_cross_correlate_full: gegen die direkte Definition geprüft --------------


def test_fft_cross_correlate_matches_naive_definition() -> None:
    rng = np.random.default_rng(7)
    a = rng.standard_normal(50)
    b = rng.standard_normal(30)
    fast = al.fft_cross_correlate_full(a, b)
    naive = np.array([np.correlate(a, b, mode="full")[i] for i in range(len(a) + len(b) - 1)])
    assert np.allclose(fast, naive, atol=1e-8)


def test_xcorr_lag_samples_recovers_known_delay() -> None:
    rng = np.random.default_rng(3)
    n = 5000
    screen = rng.standard_normal(n)
    known_delay = 42
    avatar = np.zeros(n)
    avatar[known_delay:] = screen[: n - known_delay]
    result = al.xcorr_lag_samples(avatar[200 : n - 200], screen[200 : n - 200])
    assert result is not None
    lag, _peak, ratio = result
    assert lag == known_delay
    assert ratio > al.WEAK_PEAK_RATIO


def test_xcorr_lag_samples_none_on_silence() -> None:
    silence = np.zeros(1000)
    other = np.zeros(1000)
    assert al.xcorr_lag_samples(silence, other) is None


def test_verify_sign_convention_passes() -> None:
    assert al.verify_sign_convention() is True


# --- _read_wav_mono_float: WAV-Lesen ohne scipy ------------------------------------


def test_read_wav_mono_float_roundtrip(tmp_path: Path) -> None:
    samples = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float64)
    path = tmp_path / "test.wav"
    _write_mono_wav(path, samples)
    read_back = al._read_wav_mono_float(path)
    assert len(read_back) == len(samples)
    assert np.max(np.abs(read_back)) == pytest.approx(1.0, abs=1e-3)


def test_read_wav_mono_float_stereo_averaged(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    left = np.full(100, 0.5)
    right = np.full(100, -0.5)
    interleaved = np.empty(200, dtype=np.float64)
    interleaved[0::2] = left
    interleaved[1::2] = right
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(al.SAMPLE_RATE_HZ)
        handle.writeframes((interleaved * 32767.0).astype("<i2").tobytes())
    read_back = al._read_wav_mono_float(path)
    assert len(read_back) == 100
    # (0.5 + -0.5) / 2 == 0 -> Peak 0, keine Normierung noetig.
    assert np.allclose(read_back, 0.0, atol=1e-6)


def test_read_wav_mono_float_rejects_non_16bit(tmp_path: Path) -> None:
    path = tmp_path / "bad.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(4)
        handle.setframerate(al.SAMPLE_RATE_HZ)
        handle.writeframes(struct.pack("<i", 0))
    with pytest.raises(ValueError, match="16-bit"):
        al._read_wav_mono_float(path)


# --- measure_lag: Orchestrierung mit gefaelschtem ffmpeg ---------------------------


def _fake_ffmpeg_writing(screen_samples: np.ndarray, avatar_samples: np.ndarray) -> type:
    class _FakeCompleted:
        returncode = 0
        stdout = b""

    call_count = {"n": 0}

    def fake_run(arguments: list[str], **kwargs: object) -> _FakeCompleted:
        del kwargs
        out_path = Path(arguments[-1])
        call_count["n"] += 1
        samples = screen_samples if call_count["n"] == 1 else avatar_samples
        _write_mono_wav(out_path, samples)
        return _FakeCompleted()

    return fake_run  # type: ignore[return-value]


def test_measure_lag_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(11)
    n = al.SAMPLE_RATE_HZ * 20
    screen = rng.standard_normal(n)
    delay_samples = 400  # 50 ms bei 8 kHz
    avatar = np.zeros(n)
    avatar[delay_samples:] = screen[: n - delay_samples]

    monkeypatch.setattr(al.subprocess, "run", _fake_ffmpeg_writing(screen, avatar))

    result = al.measure_lag(
        Path("ffmpeg.exe"),
        tmp_path / "screen.mp4",
        tmp_path / "avatar.mp4",
        start_s=0.0,
        duration_s=20.0,
    )
    assert isinstance(result, al.LagMeasurement)
    assert result.lag_ms == pytest.approx(50.0, abs=0.2)
    assert result.weak_peak is False


def test_measure_lag_fails_on_silence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    n = al.SAMPLE_RATE_HZ * 20
    silence = np.zeros(n)
    monkeypatch.setattr(al.subprocess, "run", _fake_ffmpeg_writing(silence, silence))

    result = al.measure_lag(
        Path("ffmpeg.exe"),
        tmp_path / "screen.mp4",
        tmp_path / "avatar.mp4",
        start_s=0.0,
        duration_s=20.0,
    )
    assert isinstance(result, al.LagMeasurementFailed)
    assert "Stille" in result.reason


def test_measure_lag_fails_on_ffmpeg_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFailed:
        returncode = 1
        stdout = b"boom"

    monkeypatch.setattr(al.subprocess, "run", lambda *a, **k: _FakeFailed())

    result = al.measure_lag(Path("ffmpeg.exe"), tmp_path / "screen.mp4", tmp_path / "avatar.mp4")
    assert isinstance(result, al.LagMeasurementFailed)
    assert "ffmpeg-Extraktion" in result.reason


def test_measure_lag_fails_on_weak_peak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Deterministisch statt statistisch: xcorr_lag_samples liefert einen Gipfel
    # unterhalb der Schwelle - Zufallsrauschen ergibt wegen der Extremwertstatistik
    # bei grossen Fenstern fast immer einen ratio-Wert oberhalb der Schwelle, waere
    # also ein flakiger Test.
    n = al.SAMPLE_RATE_HZ * 20
    silence_like = np.zeros(n)
    monkeypatch.setattr(al.subprocess, "run", _fake_ffmpeg_writing(silence_like, silence_like))
    monkeypatch.setattr(al, "verify_sign_convention", lambda: True)
    monkeypatch.setattr(al, "xcorr_lag_samples", lambda avatar, screen: (5, 1.0, 1.5))

    result = al.measure_lag(
        Path("ffmpeg.exe"),
        tmp_path / "screen.mp4",
        tmp_path / "avatar.mp4",
        start_s=0.0,
        duration_s=20.0,
    )
    assert isinstance(result, al.LagMeasurementFailed)
    assert "schwach" in result.reason


def test_measure_lag_fails_on_short_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    short = np.zeros(100)
    monkeypatch.setattr(al.subprocess, "run", _fake_ffmpeg_writing(short, short))

    result = al.measure_lag(
        Path("ffmpeg.exe"),
        tmp_path / "screen.mp4",
        tmp_path / "avatar.mp4",
        start_s=0.0,
        duration_s=1.0,
    )
    assert isinstance(result, al.LagMeasurementFailed)
    assert "kurz" in result.reason
