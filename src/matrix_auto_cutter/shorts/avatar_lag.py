r"""Tonabgleich-Lagmessung zwischen Bildschirm- und Avataraufnahme (Auftrag 11, Eingriff 1).

``numpy`` ist Produktabhaengigkeit, ``scipy`` bewusst nicht - siehe
``artefakte\repeat\shorts-lagmessung\LAGMESSUNG-2026-08-11.md`` Punkt B: der
ffmpeg-Filter ``axcorrelate`` liefert ohne erhebliches
Reverse-Engineering kein auswertbares Ergebnis, ist also kein Ersatz.
Methode und Vorzeichenkonvention sind aus
``artefakte\repeat\shorts-stufe-1\lag_measure.py`` uebernommen (dort mit
``scipy.signal.correlate``/``scipy.io.wavfile`` gebaut und zweifach gegen
echte Laeufe bestaetigt); hier per FFT-Kreuzkorrelation (``numpy.fft``) und
dem Standardmodul ``wave`` neu gebaut - siehe
:func:`test_fft_cross_correlate_matches_scipy_reference` in den Tests, die
das gegen einen unabhaengig erzeugten Referenzwert prueft.

VORZEICHENKONVENTION (unveraendert):
    lag_ms > 0  =>  Avatarspur liegt X ms SPAETER als Bildschirmspur
    lag_ms < 0  =>  Avatarspur liegt X ms FRUEHER als Bildschirmspur
Alle bisher gemessenen Laeufe ergeben lag_ms < 0 (Avatar beginnt spaeter).
"""

from __future__ import annotations

import os
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

SAMPLE_RATE_HZ = 8000
DEFAULT_WINDOW_START_S = 15.0
DEFAULT_WINDOW_DURATION_S = 60.0
WEAK_PEAK_RATIO = 3.0


@dataclass(frozen=True, slots=True)
class LagMeasurement:
    """Erfolgreiche Messung samt Messguete."""

    lag_ms: float
    lag_samples: int
    peak_ratio: float
    weak_peak: bool


@dataclass(frozen=True, slots=True)
class LagMeasurementFailed:
    """Fail-closed Auskunft - kein geschaetzter Wert, kein Rueckfall auf null."""

    reason: str


def _extract_mono_wav(
    ffmpeg_path: Path,
    source: Path,
    *,
    start_s: float,
    duration_s: float,
    out_wav: Path,
    timeout_seconds: int,
) -> None:
    """Extrahiere ein mono, 8-kHz-PCM-Fenster der Tonspur per ffmpeg."""
    arguments = [
        str(ffmpeg_path),
        "-y",
        "-ss",
        str(start_s),
        "-t",
        str(duration_s),
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE_HZ),
        "-c:a",
        "pcm_s16le",
        str(out_wav),
    ]
    result = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout.decode("utf-8", errors="replace")[-2000:])


def _read_wav_mono_float(path: Path) -> np.ndarray:
    """Lies eine 16-bit-PCM-WAV-Datei als normierten Float-Vektor - kein scipy noetig."""
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError("erwartet 16-bit PCM WAV")
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 0:
        data = data / peak
    return data


def fft_cross_correlate_full(avatar: np.ndarray, screen: np.ndarray) -> np.ndarray:
    """Volle Kreuzkorrelation, aequivalent zu ``scipy.signal.correlate(mode="full")``.

    Reine FFT-Umsetzung ohne scipy: Beide Signale werden auf eine
    Zweierpotenz >= ``len(avatar) + len(screen) - 1`` nullgepolstert, damit
    die zirkulare FFT-Korrelation ohne Ueberlappungsfehler der linearen
    entspricht. Ergebnisindex ``i`` entspricht Lag ``i - (len(screen) - 1)``,
    dieselbe Ordnung wie ``numpy.correlate``/``scipy.signal.correlate``.
    """
    avatar_len, screen_len = len(avatar), len(screen)
    full_len = avatar_len + screen_len - 1
    fft_size = 1
    while fft_size < full_len:
        fft_size *= 2
    avatar_fft = np.fft.rfft(avatar, fft_size)
    screen_fft = np.fft.rfft(screen, fft_size)
    circular = np.fft.irfft(avatar_fft * np.conj(screen_fft), fft_size)
    negative_lags = circular[fft_size - (screen_len - 1) :] if screen_len > 1 else circular[:0]
    nonnegative_lags = circular[:avatar_len]
    return np.concatenate([negative_lags, nonnegative_lags])


def xcorr_lag_samples(avatar: np.ndarray, screen: np.ndarray) -> tuple[int, float, float] | None:
    """lag>0 heisst: Avatar folgt der Bildschirmspur (Avatar spaeter). ``None`` bei Stille."""
    a = avatar - np.mean(avatar)
    b = screen - np.mean(screen)
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    corr = fft_cross_correlate_full(a, b)
    idx = int(np.argmax(corr))
    lag = idx - (len(b) - 1)
    peak = float(corr[idx])
    med = float(np.median(np.abs(corr)))
    ratio = peak / med if med != 0 else float("inf")
    return lag, peak, ratio


def verify_sign_convention() -> bool:
    """Vorzeichenkontrolle an einem synthetischen Fall mit bekanntem Versatz."""
    rng = np.random.default_rng(42)
    n = 20000
    screen = rng.standard_normal(n)
    known_delay = 137
    avatar = np.zeros(n)
    avatar[known_delay:] = screen[: n - known_delay]
    result = xcorr_lag_samples(avatar[500 : n - 500], screen[500 : n - 500])
    return result is not None and result[0] == known_delay


def measure_lag(
    ffmpeg_path: Path,
    screen_path: Path,
    avatar_path: Path,
    *,
    start_s: float = DEFAULT_WINDOW_START_S,
    duration_s: float = DEFAULT_WINDOW_DURATION_S,
    timeout_seconds: int = 120,
) -> LagMeasurement | LagMeasurementFailed:
    """Messe den Lag ``L`` zwischen Bildschirm- und Avataraufnahme.

    Fail-closed: ein zu kurzes Fenster, Stille oder ein schwacher
    Korrelationsgipfel liefern ``LagMeasurementFailed`` statt eines
    geschaetzten Werts. Die Vorzeichenkonvention wird vor jeder Messung
    selbst geprueft (:func:`verify_sign_convention`) - schlaegt sie fehl,
    wird der Messung nicht vertraut.
    """
    if not verify_sign_convention():
        return LagMeasurementFailed("Vorzeichenkontrolle der Kreuzkorrelation schlug fehl")
    with TemporaryDirectory() as tmp:
        screen_wav = Path(tmp) / "screen.wav"
        avatar_wav = Path(tmp) / "avatar.wav"
        try:
            _extract_mono_wav(
                ffmpeg_path,
                screen_path,
                start_s=start_s,
                duration_s=duration_s,
                out_wav=screen_wav,
                timeout_seconds=timeout_seconds,
            )
            _extract_mono_wav(
                ffmpeg_path,
                avatar_path,
                start_s=start_s,
                duration_s=duration_s,
                out_wav=avatar_wav,
                timeout_seconds=timeout_seconds,
            )
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            return LagMeasurementFailed(f"ffmpeg-Extraktion fehlgeschlagen: {exc}")
        screen = _read_wav_mono_float(screen_wav)
        avatar = _read_wav_mono_float(avatar_wav)
    if len(screen) < SAMPLE_RATE_HZ or len(avatar) < SAMPLE_RATE_HZ:
        return LagMeasurementFailed("Fenster zu kurz nach Extraktion")
    result = xcorr_lag_samples(avatar, screen)
    if result is None:
        return LagMeasurementFailed("Stille - keine Korrelation moeglich")
    lag_samples, _peak, ratio = result
    if ratio < WEAK_PEAK_RATIO:
        return LagMeasurementFailed(
            f"Korrelationsgipfel zu schwach (peak_ratio={ratio:.2f} < {WEAK_PEAK_RATIO})"
        )
    lag_ms = lag_samples * 1000.0 / SAMPLE_RATE_HZ
    return LagMeasurement(lag_ms=lag_ms, lag_samples=lag_samples, peak_ratio=ratio, weak_peak=False)
