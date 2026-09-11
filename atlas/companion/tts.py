"""Text-to-Speech synthesis and audio conversion for ATLAS Companion hardware.

Converts response text to 16 kHz mono 8-bit unsigned PCM audio for the ESP32 GPIO25 DAC
using the verified Windows System.Speech / SAPI engine and scipy resampling.
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Tuple

import numpy as np
import scipy.signal

logger = logging.getLogger("atlas.companion.tts")


def clean_text_for_tts(text: str) -> str:
    """Clean markdown, emoji, and formatting from text for natural speech synthesis."""
    if not text:
        return ""

    # Remove markdown code blocks and inline code
    t = re.sub(r"```[\s\S]*?```", " ", text)
    t = re.sub(r"`[^`]*`", " ", t)

    # Remove markdown links [text](url) -> text
    t = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", t)

    # Remove citations like [INCIDENT-001] or [OBS-123]
    t = re.sub(r"\[[A-Z0-9_\-]+\]", " ", t)

    # Remove bullet markers, headers, horizontal rules
    t = re.sub(r"^[\s*\-#>]+\s*", "", t, flags=re.MULTILINE)

    # Remove bold, italics
    t = re.sub(r"[*_~]{1,3}", "", t)

    # Expand technical abbreviations for smooth pronunciation
    t = re.sub(r"\bCOM5\b", "C-O-M 5", t)
    t = re.sub(r"\bESP32\b", "E-S-P 32", t)
    t = re.sub(r"\bLLM\b", "L-L-M", t)
    t = re.sub(r"\bID\b", "I-D", t)
    t = re.sub(r"\bKPI\b", "K-P-I", t)
    t = re.sub(r"\bDAC\b", "D-A-C", t)
    t = re.sub(r"\bPCM\b", "P-C-M", t)

    # Remove emojis and non-ASCII decorative symbols
    t = t.encode("ascii", "ignore").decode("ascii")

    # Normalize whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


MAX_ESP32_AUDIO_BYTES = 50000  # Firmware audio buffer limit is ~60-64KB; 50KB (~3.1s) ensures safe playback


def split_text_into_speech_chunks(text: str, max_chars: int = 180) -> list[str]:
    """Split text into sentence/clause-bounded chunks suitable for ESP32 SRAM audio buffer limits."""
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        return []

    # Split on sentence and clause boundaries: . ! ? ; , : \n
    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?,;:\n])\s+", cleaned) if s.strip()]
    if not raw_sentences:
        raw_sentences = [cleaned]

    chunks = []
    current = ""
    for s in raw_sentences:
        if len(s) > max_chars:
            words = s.split()
            w_cur = ""
            for w in words:
                if len(w_cur) + len(w) + 1 <= max_chars:
                    w_cur = (w_cur + " " + w).strip()
                else:
                    if w_cur:
                        chunks.append(w_cur)
                    w_cur = w
            if w_cur:
                chunks.append(w_cur)
            continue

        if not current:
            current = s
        elif len(current) + len(s) + 1 <= max_chars:
            current = current + " " + s
        else:
            chunks.append(current)
            current = s

    if current:
        chunks.append(current)

    return chunks or [cleaned]


def text_to_speech_packets(
    text: str,
    max_chunk_bytes: int = MAX_ESP32_AUDIO_BYTES,
) -> list[Tuple[bytes, float]]:
    """Convert text to speech packets guaranteed to never exceed ESP32 SRAM buffer limits.

    Returns a list of (pcm_u8_bytes, duration_seconds) tuples.
    """
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        return []

    chunks = split_text_into_speech_chunks(cleaned, max_chars=180)
    packets: list[Tuple[bytes, float]] = []

    for c in chunks:
        pcm, dur = text_to_16k_u8_pcm(c)
        if len(pcm) <= max_chunk_bytes:
            packets.append((pcm, dur))
        else:
            # Fallback byte slicing for any unexpectedly long waveform
            for offset in range(0, len(pcm), max_chunk_bytes):
                slice_bytes = pcm[offset : offset + max_chunk_bytes]
                slice_dur = len(slice_bytes) / 16000.0
                packets.append((slice_bytes, slice_dur))

    return packets




def synthesize_speech_wav(text: str) -> bytes:
    """Synthesize text to 22050 Hz 16-bit mono WAV using Windows SAPI / System.Speech."""
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        raise ValueError("Text is empty after cleaning.")

    # Method 1: SAPI via win32com (Fast in-process COM)
    try:
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

        import win32com.client
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            filestream = win32com.client.Dispatch("SAPI.SpFileStream")
            # 3 = SSFMCreateForWrite
            filestream.Open(tmp_path, 3, False)
            voice.AudioOutputStream = filestream
            voice.Speak(cleaned)
            filestream.Close()

            with open(tmp_path, "rb") as f:
                wav_bytes = f.read()
            if len(wav_bytes) > 44:
                return wav_bytes
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("win32com SAPI synthesis failed, falling back to PowerShell System.Speech: %s", exc)

    # Method 2: Fallback via PowerShell System.Speech
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_ps:
        tmp_ps_path = tmp_ps.name

    try:
        ps_script = f"""
Add-Type -AssemblyName System.Speech
$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speak.SetOutputToWaveFile('{tmp_ps_path}')
$speak.Speak(@'
{cleaned}
'@)
$speak.Dispose()
"""
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        if res.returncode != 0:
            raise RuntimeError(f"PowerShell TTS error: {res.stderr.strip()}")

        with open(tmp_ps_path, "rb") as f:
            wav_bytes = f.read()

        if len(wav_bytes) <= 44:
            raise RuntimeError("Generated WAV file is empty or invalid.")

        return wav_bytes
    finally:
        try:
            if os.path.exists(tmp_ps_path):
                os.remove(tmp_ps_path)
        except Exception:
            pass


def convert_wav_to_16k_u8_pcm(wav_bytes: bytes) -> Tuple[bytes, float]:
    """Convert input WAV bytes to 16,000 Hz mono unsigned 8-bit PCM.

    Returns:
        tuple (raw_unsigned_8bit_pcm_bytes, duration_seconds)
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        n_channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        framerate = w.getframerate()
        n_frames = w.getnframes()
        raw_audio = w.readframes(n_frames)

    if sampwidth == 2:
        samples = np.frombuffer(raw_audio, dtype=np.int16)
    elif sampwidth == 1:
        # Convert uint8 (0..255) to int16
        u8_samples = np.frombuffer(raw_audio, dtype=np.uint8)
        samples = ((u8_samples.astype(np.float32) - 128.0) * 256.0).astype(np.int16)
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth} bytes (must be 1 or 2 bytes).")

    # If multi-channel, average to mono
    if n_channels > 1:
        samples = samples.reshape(-1, n_channels).mean(axis=1).astype(np.int16)

    # Resample to 16,000 Hz if needed
    target_rate = 16000
    if framerate != target_rate:
        target_num_samples = int(len(samples) * target_rate / framerate)
        resampled = scipy.signal.resample(samples.astype(np.float32), target_num_samples)
        resampled_16 = np.clip(resampled, -32768, 32767)
    else:
        resampled_16 = samples.astype(np.float32)

    # Convert 16-bit signed PCM (-32768 to 32767) to 8-bit unsigned PCM (0 to 255)
    # Midpoint 128 corresponds to 0 signal (GPIO25 DAC silence level)
    u8_pcm = np.clip(((resampled_16 + 32768.0) / 256.0), 0, 255).astype(np.uint8)
    pcm_bytes = u8_pcm.tobytes()
    duration = len(pcm_bytes) / float(target_rate)

    return pcm_bytes, round(duration, 3)


def text_to_16k_u8_pcm(text: str) -> Tuple[bytes, float]:
    """End-to-end conversion from response text directly to 16kHz unsigned 8-bit PCM."""
    wav_data = synthesize_speech_wav(text)
    return convert_wav_to_16k_u8_pcm(wav_data)
