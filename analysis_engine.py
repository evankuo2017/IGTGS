from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
IGTGS_BACKEND_DIR = Path(
    os.environ.get("IGTGS_BACKEND_DIR", str(BASE_DIR / "igtgs_backend"))
).resolve()
FIXED_BEAT_DETECTOR = "madmom"
FIXED_CHORD_DETECTOR = "chord-cnn-lstm"


def _prepare_backend_imports() -> None:
    backend_dir = str(IGTGS_BACKEND_DIR)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from compat import apply_all  # type: ignore
    from utils.paths import setup_model_paths  # type: ignore

    apply_all()
    setup_model_paths()


@lru_cache(maxsize=1)
def get_services() -> tuple[Any, Any]:
    _prepare_backend_imports()

    from services.audio.beat_detection_service import BeatDetectionService  # type: ignore
    from services.audio.chord_recognition_service import ChordRecognitionService  # type: ignore

    beat_service = BeatDetectionService()
    chord_service = ChordRecognitionService()
    return beat_service, chord_service


def get_engine_status() -> dict[str, Any]:
    try:
        get_services()
        return {
            "success": True,
            "mode": "local-engine",
            "sourceDir": str(IGTGS_BACKEND_DIR),
            "availableBeatDetectors": [FIXED_BEAT_DETECTOR],
            "availableChordDetectors": [FIXED_CHORD_DETECTOR],
            "defaultBeatDetector": FIXED_BEAT_DETECTOR,
            "defaultChordDetector": FIXED_CHORD_DETECTOR,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "mode": "local-engine",
            "sourceDir": str(IGTGS_BACKEND_DIR),
            "error": str(exc),
        }


def analyze_audio_file(
    audio_path: str,
    beat_detector: str = FIXED_BEAT_DETECTOR,
    chord_detector: str = FIXED_CHORD_DETECTOR,
    chord_dict: str = "large_voca",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if beat_detector != FIXED_BEAT_DETECTOR:
        raise RuntimeError(f"Only {FIXED_BEAT_DETECTOR} is supported")
    if chord_detector != FIXED_CHORD_DETECTOR:
        raise RuntimeError(f"Only {FIXED_CHORD_DETECTOR} is supported")

    beat_service, chord_service = get_services()

    beat_data = beat_service.detect_beats(audio_path, detector=beat_detector, force=False)
    if not beat_data.get("success"):
        raise RuntimeError(beat_data.get("error") or "Beat detection failed")

    chord_data = chord_service.recognize_chords(
        audio_path,
        detector=chord_detector,
        chord_dict=chord_dict,
        force=False,
        use_spleeter=False,
    )
    if not chord_data.get("success"):
        raise RuntimeError(chord_data.get("error") or "Chord recognition failed")

    return beat_data, chord_data
