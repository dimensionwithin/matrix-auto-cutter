"""Construction and canonical validation of the unchanged Phase-1 SourceIdentity."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import PureWindowsPath

from matrix_auto_cutter.models import SourceBinding, SourceIdentity
from matrix_auto_cutter.phase2.source_confirmation.contracts import (
    ConfirmationErrorCategory,
    ConfirmationErrorCode,
    ConfirmationFailure,
)
from matrix_auto_cutter.phase2.source_confirmation.evidence import (
    FormatEvidence,
    StreamEvidence,
)
from matrix_auto_cutter.phase2.source_hash import HashCompleted, receipt_from_completed


def source_identity_digest(identity: SourceIdentity) -> str:
    """Return the deterministic domain-separated digest of Phase-1 canonical bytes."""
    validated = SourceIdentity.model_validate_json(identity.model_dump_json())
    if validated != identity:
        raise ValueError("SourceIdentity failed canonical value comparison")
    return hashlib.sha256(
        b"matrix-auto-cutter/source-identity/1.0\0" + validated.model_dump_json().encode("utf-8")
    ).hexdigest()


def _exact_scaled_integer(value: Decimal, multiplier: int, field: str) -> int:
    scaled = value * multiplier
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(f"{field} is not exactly representable at its required unit")
    return int(integral)


def build_source_identity(
    source_path: str,
    completed: HashCompleted,
    format_evidence: FormatEvidence,
    video: StreamEvidence,
    audio: StreamEvidence,
    binding: SourceBinding,
) -> SourceIdentity | ConfirmationFailure:
    """Build the exact ten-field Phase-1 identity from current authoritative evidence."""
    try:
        receipt = receipt_from_completed(completed)
        if format_evidence.duration is None or Decimal(format_evidence.duration.value) <= 0:
            raise ValueError("positive exact format duration is required")
        if (
            video.stream_type != "video"
            or audio.stream_type != "audio"
            or video.nb_frames is None
            or video.nb_frames <= 0
            or video.start_time is None
            or audio.start_time is None
            or video.avg_frame_rate is None
            or video.r_frame_rate is None
            or (video.avg_frame_rate.numerator, video.avg_frame_rate.denominator) != (60, 1)
            or (video.r_frame_rate.numerator, video.r_frame_rate.denominator) != (60, 1)
        ):
            raise ValueError("selected streams cannot satisfy the Phase-1 identity contract")
        identity = SourceIdentity(
            file_name=PureWindowsPath(source_path).name,
            size_bytes=receipt.bytes_read,
            sha256=receipt.sha256,
            duration_ms=_exact_scaled_integer(
                Decimal(format_evidence.duration.value),
                1_000,
                "duration",
            ),
            video_frame_count=video.nb_frames,
            fps_num=60,
            fps_den=1,
            video_start_time_ns=_exact_scaled_integer(
                Decimal(video.start_time.value),
                1_000_000_000,
                "video start time",
            ),
            audio_start_time_ns=_exact_scaled_integer(
                Decimal(audio.start_time.value),
                1_000_000_000,
                "audio start time",
            ),
            binding=binding,
        )
        if SourceIdentity.model_validate_json(identity.model_dump_json()) != identity:
            raise ValueError("SourceIdentity did not survive canonical Phase-1 validation")
        source_identity_digest(identity)
        return identity
    except (ArithmeticError, TypeError, ValueError) as exc:
        return ConfirmationFailure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            "identity.build",
            str(exc)[:512],
            cause=exc,
        )
