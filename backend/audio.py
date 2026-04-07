"""
audio.py — Hardened Audio Pipeline (v6.1)

Changes from v6.0:
  - Accepts webm / ogg / mp3 from browsers + converts to WAV via ffmpeg (if available)
  - Falls back gracefully if ffmpeg is absent (advises WAV)
  - Silence detection via RMS energy on raw PCM data
  - Stricter duration check using WAV header (not file size estimate)
  - Max upload: 5 MB hard cap before any processing
  - Clear Tamil-friendly error messages

Pipeline (POST /api/process)
-----------------------------
  1. Size check              → reject > 5 MB
  2. Format detect           → WAV → validate directly
                             → webm/ogg/mp3 → ffmpeg convert → validate
  3. WAV header parse        → sample rate, channels, duration
  4. Duration check          → reject > 5 s
  5. Silence check           → reject if RMS < threshold
  6. Transcribe              → Groq Whisper (optional)
"""

from __future__ import annotations

import io
import os
import struct
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException

# ─── Limits ───────────────────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 5 * 1024 * 1024          # 5 MB hard cap (before conversion)
MAX_DURATION_S   = 5.0                        # seconds
MIN_DURATION_S   = 0.3                        # reject sub-300 ms clips
RMS_SILENCE_THR  = 50                         # 16-bit PCM RMS below this → silence

# ─── Format Detection ─────────────────────────────────────────────────────────

_WAV_MAGIC   = b"RIFF"
_WEBM_MAGIC  = b"\x1a\x45\xdf\xa3"
_OGG_MAGIC   = b"OggS"
_MP3_MAGIC   = (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


def _detect_format(data: bytes) -> str:
    """Return 'wav' | 'webm' | 'ogg' | 'mp3' | 'unknown'."""
    if data[:4] == _WAV_MAGIC:
        return "wav"
    if data[:4] == _WEBM_MAGIC:
        return "webm"
    if data[:4] == _OGG_MAGIC:
        return "ogg"
    if any(data[:3] == m or data[:2] == m for m in _MP3_MAGIC):
        return "mp3"
    return "unknown"


# ─── WAV Header Parser ────────────────────────────────────────────────────────

def _parse_wav_header(data: bytes) -> dict:
    """
    Extract sample_rate, num_channels, bits_per_sample, duration_s from WAV bytes.
    Raises ValueError on malformed headers.
    """
    if len(data) < 44:
        raise ValueError("WAV file too small to contain a valid header.")
    # Standard PCM WAV header layout (44 bytes)
    riff_id   = data[0:4]
    wave_id   = data[8:12]
    if riff_id != b"RIFF" or wave_id != b"WAVE":
        raise ValueError("Not a valid WAVE file.")
    num_channels,   = struct.unpack_from("<H", data, 22)
    sample_rate,    = struct.unpack_from("<I", data, 24)
    bits_per_sample,= struct.unpack_from("<H", data, 34)
    data_size,      = struct.unpack_from("<I", data, 40)
    if sample_rate == 0 or num_channels == 0 or bits_per_sample == 0:
        raise ValueError("WAV header contains zero values.")
    bytes_per_sample = bits_per_sample // 8
    total_samples    = data_size // (num_channels * bytes_per_sample)
    duration_s       = total_samples / sample_rate
    return {
        "sample_rate":     sample_rate,
        "num_channels":    num_channels,
        "bits_per_sample": bits_per_sample,
        "duration_s":      duration_s,
    }


# ─── Silence Detection ────────────────────────────────────────────────────────

def _compute_rms(wav_data: bytes) -> float:
    """Compute RMS of raw 16-bit PCM samples (data chunk starts at byte 44)."""
    pcm = wav_data[44:]
    if len(pcm) < 2:
        return 0.0
    num_samples = len(pcm) // 2
    total = 0
    for i in range(0, num_samples * 2, 2):
        sample = struct.unpack_from("<h", pcm, i)[0]
        total += sample * sample
    rms = (total / num_samples) ** 0.5
    return rms


# ─── Format Conversion via ffmpeg ─────────────────────────────────────────────

def _has_ffmpeg() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _convert_to_wav(data: bytes, src_format: str) -> bytes:
    """
    Use ffmpeg to convert webm/ogg/mp3 → 16kHz mono WAV.
    Raises HTTPException if ffmpeg is unavailable or conversion fails.
    """
    if not _has_ffmpeg():
        raise HTTPException(
            status_code=422,
            detail=(
                f"Received {src_format.upper()} audio. "
                "Please send WAV format (16 kHz mono). "
                "Tip: record directly as WAV on your app."
            ),
        )
    in_tmp  = Path(tempfile.mktemp(suffix=f".{src_format}"))
    out_tmp = Path(tempfile.mktemp(suffix=".wav"))
    try:
        in_tmp.write_bytes(data)
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(in_tmp),
                "-ar", "16000",    # 16 kHz
                "-ac", "1",        # mono
                "-f", "wav",
                str(out_tmp),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=422,
                detail="Audio conversion failed. Please send a clean WAV file.",
            )
        return out_tmp.read_bytes()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Audio conversion error: {exc}") from exc
    finally:
        for p in (in_tmp, out_tmp):
            if p.exists():
                p.unlink()


# ─── Public: validate_audio ───────────────────────────────────────────────────

def validate_audio(data: bytes) -> bytes:
    """
    Full audio validation pipeline.

    1. Hard size cap
    2. Format detect + optional convert
    3. WAV header parse → duration + sample-rate check
    4. Silence detection

    Returns validated WAV bytes (possibly converted).
    Raises HTTPException on any violation.
    """
    # Step 1: Size cap
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio file too large (max 5 MB). Please record a shorter clip.",
        )

    # Step 2: Format detection + conversion
    fmt = _detect_format(data)
    if fmt == "unknown":
        raise HTTPException(
            status_code=422,
            detail="Unrecognised audio format. Please send WAV, WebM, OGG, or MP3.",
        )
    if fmt != "wav":
        data = _convert_to_wav(data, fmt)

    # Step 3: WAV header validation
    try:
        hdr = _parse_wav_header(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Corrupt WAV file: {exc}") from exc

    if hdr["duration_s"] < MIN_DURATION_S:
        raise HTTPException(
            status_code=422,
            detail="Recording too short. Please record at least 0.5 seconds.",
        )
    if hdr["duration_s"] > MAX_DURATION_S:
        raise HTTPException(
            status_code=422,
            detail=f"Recording too long ({hdr['duration_s']:.1f}s). Maximum is {MAX_DURATION_S}s.",
        )

    # Step 4: Silence detection
    rms = _compute_rms(data)
    if rms < RMS_SILENCE_THR:
        raise HTTPException(
            status_code=422,
            detail="Audio appears to be silent. Please speak clearly into the microphone.",
        )

    return data


# ─── Backwards-compat alias ───────────────────────────────────────────────────

def validate_wav(data: bytes) -> None:
    """Thin wrapper kept for backwards compatibility. Use validate_audio() instead."""
    validate_audio(data)


# ─── Transcription ────────────────────────────────────────────────────────────

def transcribe(data: bytes) -> str:
    """
    Transcribe validated WAV bytes via Groq Whisper.

    Requires GROQ_API_KEY in env. Raises HTTP 422 if key absent.
    """
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=422,
            detail=(
                "No transcription service configured. "
                "Please send text to POST /api/analyze instead of audio."
            ),
        )
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="groq package not installed. pip install groq, or use text input.",
        )

    suffix = ".wav"
    tmp = Path(tempfile.mktemp(suffix=suffix))
    try:
        tmp.write_bytes(data)
        with open(tmp, "rb") as f:
            resp = client.audio.transcriptions.create(
                file=(tmp.name, f.read()),
                model="whisper-large-v3",
                response_format="text",
                language="ta",
            )
        text = str(resp).strip()
        if not text:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Transcription returned empty. "
                    "Puriyala, marubadi sollunga."   # Tamil fallback
                ),
            )
        return text
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Transcription service error: {exc}",
        ) from exc
    finally:
        if tmp.exists():
            tmp.unlink()
