from __future__ import annotations

import os
from collections import Counter
from typing import Any

import numpy as np

from audio_utils import get_audio_duration, validate_audio_file
from utils.logging import log_debug, log_error, log_info


class MadmomDetector:
    """Madmom 節拍偵測本體，從原本 detectors 模組內搬到同一支腳本。"""

    def __init__(self) -> None:
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available

        try:
            import madmom

            self._available = True
            log_debug(
                f"Madmom availability: {self._available}, version: {getattr(madmom, '__version__', 'unknown')}"
            )
            return True
        except ImportError as exc:
            log_error(f"Madmom import failed: {exc}")
            self._available = False
            return False

    def detect_beats(self, file_path: str) -> dict[str, Any]:
        if not self.is_available():
            return {
                "success": False,
                "error": "Madmom is not available",
                "model_used": "madmom",
                "model_name": "Madmom",
            }

        import time

        from madmom.features.beats import DBNBeatTrackingProcessor, RNNBeatProcessor
        import librosa

        start_time = time.time()

        try:
            log_info(f"Running madmom detection on: {file_path}")

            beat_activation = RNNBeatProcessor()(file_path)
            beat_times = DBNBeatTrackingProcessor(fps=100)(beat_activation)

            downbeats4 = beat_times[::4]
            downbeats3 = beat_times[::3]
            downbeat_times = downbeats4

            bpm = 120.0
            if len(beat_times) > 1:
                intervals = np.diff(beat_times)
                median_interval = np.median(intervals)
                bpm = 60.0 / median_interval if median_interval > 0 else 120.0

            audio, sample_rate = librosa.load(file_path, sr=None)
            duration = librosa.get_duration(y=audio, sr=sample_rate)
            processing_time = time.time() - start_time

            log_info(
                f"Madmom detection successful: {len(beat_times)} beats, "
                f"{len(downbeat_times)} default-downbeats (4/4), candidates: 3/4={len(downbeats3)}, 4/4={len(downbeats4)}"
            )

            return {
                "success": True,
                "beats": beat_times.tolist() if hasattr(beat_times, "tolist") else list(beat_times),
                "downbeats": downbeat_times.tolist()
                if hasattr(downbeat_times, "tolist")
                else list(downbeat_times),
                "downbeat_candidates": {
                    "3": downbeats3.tolist() if hasattr(downbeats3, "tolist") else list(downbeats3),
                    "4": downbeats4.tolist() if hasattr(downbeats4, "tolist") else list(downbeats4),
                },
                "downbeat_candidates_meta": {
                    "default": 4,
                    "strategy": "heuristic_slices_from_beats",
                },
                "total_beats": len(beat_times),
                "total_downbeats": len(downbeat_times),
                "bpm": float(bpm),
                "time_signature": "4/4",
                "duration": float(duration),
                "model_used": "madmom",
                "model_name": "Madmom",
                "processing_time": processing_time,
            }
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Madmom detection error: {exc}"
            log_error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "model_used": "madmom",
                "model_name": "Madmom",
                "processing_time": time.time() - start_time,
            }


class BeatDetectionService:
    def __init__(self) -> None:
        self.detector_name = "madmom"
        self.detector = MadmomDetector()
        self.size_limit_mb = 200

    def get_available_detectors(self) -> list[str]:
        return [self.detector_name] if self.detector.is_available() else []

    def detect_beats(self, file_path: str, detector: str = "madmom", force: bool = False) -> dict[str, Any]:
        start_message = f"Processing audio file: {file_path}"
        try:
            if detector not in {self.detector_name, "auto"}:
                raise ValueError(f"Unsupported beat detector: {detector}")
            if not os.path.exists(file_path):
                return {"success": False, "error": f"Audio file not found: {file_path}"}
            if not validate_audio_file(file_path):
                return {"success": False, "error": "Invalid or corrupted audio file"}

            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if not force and file_size_mb > self.size_limit_mb:
                return {
                    "success": False,
                    "error": f"File too large for {self.detector_name} ({file_size_mb:.1f}MB > {self.size_limit_mb}MB)",
                }

            log_info(f"{start_message} ({file_size_mb:.1f}MB)")
            result = self.detector.detect_beats(file_path)
            result["file_size_mb"] = file_size_mb
            result["detector_selected"] = self.detector_name
            result["detector_requested"] = detector
            result["force_used"] = force

            if "duration" not in result or result["duration"] == 0:
                result["duration"] = get_audio_duration(file_path)

            if result.get("success"):
                self._log_measure_statistics(file_path, result)
            else:
                log_error(f"Beat detection failed: {result.get('error', 'Unknown error')}")

            return result
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Beat detection service error: {exc}"
            log_error(error_msg)
            return {"success": False, "error": error_msg}

    def _log_measure_statistics(self, file_path: str, result: dict[str, Any]) -> None:
        try:
            file_id = os.path.basename(file_path)
            time_sig = result.get("time_signature", "4/4")
            beats_per_measure = None
            if isinstance(time_sig, str) and "/" in time_sig:
                beats_per_measure = int(str(time_sig).split("/")[0])
            elif isinstance(time_sig, (int, float)):
                beats_per_measure = int(time_sig)

            beats = result.get("beats") or []
            downbeats = result.get("downbeats") or []
            measure_counts: list[int] = []
            is_madmom_heuristic = (
                str(result.get("model_used")) == "madmom"
                and isinstance(result.get("downbeat_candidates_meta"), dict)
                and result.get("downbeat_candidates_meta", {}).get("strategy") == "heuristic_slices_from_beats"
            )

            if not is_madmom_heuristic and isinstance(beats, list) and isinstance(downbeats, list) and len(downbeats) >= 2:
                beat_index = 0
                total_beats = len(beats)
                for idx in range(len(downbeats) - 1):
                    start = float(downbeats[idx])
                    end = float(downbeats[idx + 1])
                    while beat_index < total_beats and float(beats[beat_index]) < start:
                        beat_index += 1
                    count = 0
                    while beat_index < total_beats and float(beats[beat_index]) < end:
                        count += 1
                        beat_index += 1
                    if 2 <= count <= 12:
                        measure_counts.append(int(count))

            distribution = Counter(measure_counts) if measure_counts else {}
            confidence = None
            if distribution:
                dominant_beats = max(distribution.items(), key=lambda kv: kv[1])[0]
                confidence = distribution[dominant_beats] / max(1, len(measure_counts))

            if is_madmom_heuristic:
                log_debug(
                    f"[Beat-Per-Measure] file={file_id} skipped_for=madmom_heuristic_slices distribution=derived"
                )
            elif confidence is not None:
                log_info(
                    f"[Beat-Per-Measure] file={file_id} time_signature={time_sig} "
                    f"beats_per_measure={beats_per_measure} measures={len(measure_counts)} "
                    f"distribution={dict(distribution)} confidence={confidence:.2f}"
                )
            else:
                log_info(
                    f"[Beat-Per-Measure] file={file_id} time_signature={time_sig} "
                    f"beats_per_measure={beats_per_measure} measures={len(measure_counts)} "
                    f"distribution={dict(distribution)}"
                )
        except Exception as exc:  # noqa: BLE001
            log_debug(f"Beat-per-measure logging skipped due to error: {exc}")
