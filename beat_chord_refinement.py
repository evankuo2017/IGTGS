"""
依節拍對 maj / maj7 / min / min7 和絃片段做 ChordRefiner 二次推理，並產出 refine 報告。
"""
from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import torch

from grid_builder import synchronize_chords, to_beat_info

_log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
REFINER_DIR = BASE_DIR / "chord_refiner"
# 可透過環境變數指定權重路徑
DEFAULT_REFINER_WEIGHTS = REFINER_DIR / "best_chord_model.pth"

REFINE_QUALITIES = frozenset({"maj", "maj7", "min", "min7"})
# 若 refiner 最佳類別 softmax 信心值嚴格小於此值，維持原和絃（視為未 refine）
REFINER_CONFIDENCE_MIN = 0.5


def _prepare_refiner_imports() -> None:
    rd = str(REFINER_DIR)
    if rd not in sys.path:
        sys.path.insert(0, rd)


def parse_root_quality(chord_name: str) -> tuple[str | None, str | None]:
    """從 'C:maj'、'Bb:min7' 等形式解析 root 與 quality；無法解析則回傳 (None, None)。"""
    if not chord_name or chord_name in ("N", "N/C", "N.C.", ""):
        return None, None
    if ":" in chord_name:
        root, quality = chord_name.split(":", 1)
        return root.strip() or None, quality.strip() or None
    return chord_name.strip(), "maj"


def beats_to_chord_segments(
    beat_times: list[float],
    per_beat_chords: list[str],
    duration: float,
) -> list[dict[str, Any]]:
    """將每拍一個和絃合併為連續時間段（與 chord_service 條目格式一致）。"""
    if not beat_times or len(per_beat_chords) != len(beat_times):
        return []
    segments: list[dict[str, Any]] = []
    for i, chord in enumerate(per_beat_chords):
        start = float(beat_times[i])
        end = float(beat_times[i + 1]) if i + 1 < len(beat_times) else float(duration)
        if segments and segments[-1]["chord"] == chord:
            segments[-1]["end"] = end
        else:
            segments.append(
                {
                    "start": start,
                    "end": end,
                    "chord": chord,
                    "confidence": 1.0,
                }
            )
    return segments


@lru_cache(maxsize=1)
def _load_refiner_model_at_path(weights_str: str) -> tuple[Any, torch.device]:
    """僅在權重檔存在時呼叫，避免快取「無檔案」狀態。"""
    _prepare_refiner_imports()
    import config as refiner_config  # type: ignore
    from model import ChordRefinerCNN  # type: ignore

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = ChordRefinerCNN(num_classes=len(refiner_config.CHORD_LIST)).to(device)
    load_kw: dict[str, Any] = {"map_location": device}
    try:
        model.load_state_dict(torch.load(weights_str, **load_kw, weights_only=True))
    except TypeError:
        model.load_state_dict(torch.load(weights_str, **load_kw))
    model.eval()
    return model, device


def get_refiner_model() -> tuple[Any, torch.device, str] | None:
    """載入 ChordRefiner；權重不存在或 import 失敗時回傳 None。"""
    weights_path = Path(os.environ.get("IGTGS_CHORD_REFINER_WEIGHTS", str(DEFAULT_REFINER_WEIGHTS)))
    if not weights_path.is_file():
        _log.warning("Chord refiner weights not found: %s", weights_path)
        return None
    w = str(weights_path.resolve())
    try:
        model, device = _load_refiner_model_at_path(w)
        return model, device, w
    except ImportError as exc:
        _log.warning("Chord refiner import failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        _log.warning("Chord refiner load failed: %s", exc)
        return None


def refine_beat_segment(
    audio_path: str,
    t_start: float,
    t_end: float,
    model: Any,
    device: torch.device,
) -> tuple[str, float, dict[str, float]] | None:
    """載入 [t_start, t_end) 片段並做一次 refiner 推理。"""
    try:
        duration = max(0.0, t_end - t_start)
        if duration <= 1e-4:
            return None
        y, sr = librosa.load(
            audio_path,
            sr=None,
            mono=True,
            offset=float(t_start),
            duration=float(duration),
        )
        if y.size == 0:
            return None
        _prepare_refiner_imports()
        from predict import predict_single_window  # type: ignore

        return predict_single_window(np.asarray(y, dtype=np.float32), int(sr), model, device)
    except Exception as exc:  # noqa: BLE001
        _log.debug("Refiner segment failed [%s, %s): %s", t_start, t_end, exc)
        return None


def refine_chords_with_beats(
    audio_path: str,
    beat_data: dict[str, Any],
    chord_data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    對「每一拍」在初辨為 maj/maj7/min/min7 時截取該拍音訊，以 refiner 取信心最高者更新 quality。

    回傳 (更新後的 chord_data, refine 報告 dict)。
    """
    chords_in = list(chord_data.get("chords") or [])
    duration = float(
        chord_data.get("duration")
        or beat_data.get("duration")
        or (chords_in[-1]["end"] if chords_in else 0.0)
    )

    beats = to_beat_info(beat_data)
    if not beats:
        return chord_data, _empty_refine_report("no_beats", duration)

    beat_times = [float(b["time"]) for b in beats]
    synced = synchronize_chords(chords_in, beats)
    per_beat_original = [s["chord"] for s in synced]

    loaded = get_refiner_model()
    if loaded is None:
        report = _empty_refine_report("model_unavailable", duration)
        report["beats"] = [
            _beat_refine_entry(
                i,
                beat_times[i],
                per_beat_original[i],
                per_beat_original[i],
                refined=False,
                skip_reason="model_unavailable",
            )
            for i in range(len(beats))
        ]
        return chord_data, report

    model, device, weights_str = loaded
    per_beat_final = list(per_beat_original)
    beat_entries: list[dict[str, Any]] = []

    for i in range(len(beats)):
        t0 = beat_times[i]
        t1 = beat_times[i + 1] if i + 1 < len(beat_times) else duration
        orig = per_beat_original[i]
        root, quality = parse_root_quality(orig)

        if root is None or quality is None or quality not in REFINE_QUALITIES:
            beat_entries.append(
                _beat_refine_entry(
                    i,
                    t0,
                    orig,
                    orig,
                    refined=False,
                    skip_reason="quality_not_target",
                )
            )
            continue

        result = refine_beat_segment(audio_path, t0, t1, model, device)
        if result is None:
            beat_entries.append(
                _beat_refine_entry(
                    i,
                    t0,
                    orig,
                    orig,
                    refined=False,
                    skip_reason="segment_load_or_infer_failed",
                )
            )
            continue

        best_label, confidence, prob_map = result
        if confidence < REFINER_CONFIDENCE_MIN:
            beat_entries.append(
                _beat_refine_entry(
                    i,
                    t0,
                    orig,
                    orig,
                    refined=False,
                    skip_reason="low_confidence",
                    refiner_label=best_label,
                    confidence=confidence,
                    probabilities=prob_map,
                )
            )
            continue

        final_chord = f"{root}:{best_label}"
        per_beat_final[i] = final_chord
        beat_entries.append(
            _beat_refine_entry(
                i,
                t0,
                orig,
                final_chord,
                refined=True,
                skip_reason=None,
                refiner_label=best_label,
                confidence=confidence,
                probabilities=prob_map,
            )
        )

    new_segments = beats_to_chord_segments(beat_times, per_beat_final, duration)
    new_chord_data = dict(chord_data)
    new_chord_data["chords"] = new_segments
    new_chord_data["total_chords"] = len(new_segments)
    new_chord_data["chord_refiner_applied"] = True

    report = {
        "success": True,
        "modelWeightsPath": weights_str,
        "audioDuration": duration,
        "targetQualities": sorted(REFINE_QUALITIES),
        "confidenceThreshold": REFINER_CONFIDENCE_MIN,
        "beats": beat_entries,
    }
    return new_chord_data, report


def _empty_refine_report(reason: str, duration: float) -> dict[str, Any]:
    return {
        "success": False,
        "error": reason,
        "modelWeightsPath": os.environ.get("IGTGS_CHORD_REFINER_WEIGHTS", str(DEFAULT_REFINER_WEIGHTS)),
        "audioDuration": duration,
        "targetQualities": sorted(REFINE_QUALITIES),
        "confidenceThreshold": REFINER_CONFIDENCE_MIN,
        "beats": [],
    }


def _segment_index_for_time(segments: list[dict[str, Any]], t: float) -> int | None:
    """合併後的 chord segment 時間軸上，找 t 落在哪一段（最後一段 end 含等號）。"""
    if not segments:
        return None
    for i, seg in enumerate(segments):
        s = float(seg.get("start", 0.0))
        e = float(seg.get("end", 0.0))
        if i < len(segments) - 1:
            if s <= t < e:
                return i
        elif s <= t <= e:
            return i
    return None


def align_chord_refine_report(analysis_payload: dict[str, Any], refine_report: dict[str, Any]) -> None:
    """
    將 chordRefine 與前端格子（gridVisualIndex）及合併後 raw.chordData.chords 對齊，
    寫入 refinedGridVisualIndices、chordSegmentsAligned、各 beat 的 gridVisualIndex／chordSegmentIndex。
    """
    if not isinstance(refine_report, dict):
        return

    grid = analysis_payload.get("chordGridData") or {}
    mapping = grid.get("originalAudioMapping") or []
    audio_to_visual: dict[int, int] = {}
    for m in mapping:
        if m.get("audioIndex") is None:
            continue
        audio_to_visual[int(m["audioIndex"])] = int(m["visualIndex"])

    raw = analysis_payload.get("raw") or {}
    chord_block = raw.get("chordData") or {}
    chord_list = list(chord_block.get("chords") or [])

    beat_entries: list[dict[str, Any]] = list(refine_report.get("beats") or [])
    refined_visual: set[int] = set()

    for b in beat_entries:
        bi = b.get("beatIndex")
        if bi is not None and int(bi) in audio_to_visual:
            b["gridVisualIndex"] = audio_to_visual[int(bi)]
        else:
            b["gridVisualIndex"] = None

        t = float(b.get("time") or 0.0)
        seg_i = _segment_index_for_time(chord_list, t)
        b["chordSegmentIndex"] = seg_i

        if b.get("refined") and b.get("gridVisualIndex") is not None:
            refined_visual.add(int(b["gridVisualIndex"]))

    refine_report["refinedGridVisualIndices"] = sorted(refined_visual)

    beat_indices_by_seg: dict[int, list[int]] = {}
    for b in beat_entries:
        si = b.get("chordSegmentIndex")
        if si is None:
            continue
        beat_indices_by_seg.setdefault(int(si), []).append(int(b["beatIndex"]))

    aligned: list[dict[str, Any]] = []
    for i, seg in enumerate(chord_list):
        bis_raw = beat_indices_by_seg.get(i, [])
        bis_sorted = sorted(set(bis_raw))
        refined_bis = sorted(
            {
                int(b["beatIndex"])
                for b in beat_entries
                if b.get("chordSegmentIndex") == i and b.get("refined")
            }
        )
        aligned.append(
            {
                "segmentIndex": i,
                "start": seg.get("start"),
                "end": seg.get("end"),
                "chord": seg.get("chord"),
                "beatIndices": bis_sorted,
                "refinedBeatIndices": refined_bis,
                "segmentHadRefine": len(refined_bis) > 0,
            }
        )
    refine_report["chordSegmentsAligned"] = aligned


def _beat_refine_entry(
    beat_index: int,
    time: float,
    original_chord: str,
    final_chord: str,
    *,
    refined: bool,
    skip_reason: str | None,
    refiner_label: str | None = None,
    confidence: float | None = None,
    probabilities: dict[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "beatIndex": beat_index,
        "time": round(time, 6),
        "originalChord": original_chord,
        "finalChord": final_chord,
        "refined": refined,
        "skipReason": skip_reason,
        "refinerLabel": refiner_label,
        "confidence": confidence,
        "probabilities": probabilities,
    }
