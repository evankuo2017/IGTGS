from __future__ import annotations

import os

from utils.logging import log_error


def get_audio_duration(audio_path: str) -> float:
    try:
        import librosa

        audio, sample_rate = librosa.load(audio_path, sr=None)
        return float(librosa.get_duration(y=audio, sr=sample_rate))
    except Exception as exc:  # noqa: BLE001
        log_error(f"Failed to get audio duration: {exc}")
        return 0.0


def validate_audio_file(audio_path: str) -> bool:
    try:
        import librosa

        audio, sample_rate = librosa.load(audio_path, sr=None, duration=1.0)
        return len(audio) > 0 and sample_rate > 0
    except ImportError:
        return os.path.exists(audio_path) and os.path.getsize(audio_path) > 0
    except Exception as exc:  # noqa: BLE001
        log_error(f"Audio file validation failed: {exc}")
        return False
