#!/usr/bin/env python3
"""
實驗腳本：對單一音檔跑 IGTGS 內建和弦辨識（Chord-CNN-LSTM + madmom 節拍），
再依「時間軸上每一個和弦 segment」檢查 quality 是否為 maj / maj7 / min / min7；
若是則截取該段音訊送 ChordRefiner，若 softmax 最大值（信心）>= 0.5 則以 argmax 類別更新 quality（根音不變）。

使用方式（請在 IGTGS 目錄下執行，或確保 PYTHONPATH 含專案根目錄）：
  python experiment.py /path/to/audio.wav
  python experiment.py /path/to/audio.m4a -o result.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# 與 app 相同：從 IGTGS 目錄載入模組
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from analysis_engine import analyze_audio_file  # noqa: E402
from beat_chord_refinement import (  # noqa: E402
    REFINER_CONFIDENCE_MIN,
    REFINE_QUALITIES,
    get_refiner_model,
    parse_root_quality,
    refine_beat_segment,
)

_log = logging.getLogger(__name__)


def run_segment_wise_refine(
    audio_path: str,
    chord_segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """
    對每個和弦 segment 嘗試 refiner。
    回傳 (更新後的 segments 列表, 每段詳細 log, refiner 權重路徑或 None)。
    """
    loaded = get_refiner_model()
    weights_path: str | None = loaded[2] if loaded else None
    model = loaded[0] if loaded else None
    device = loaded[1] if loaded else None

    refined_segments: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    for seg in chord_segments:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        orig = str(seg.get("chord", "") or "")
        conf = float(seg.get("confidence", 1.0))

        entry: dict[str, Any] = {
            "start": start,
            "end": end,
            "originalChord": orig,
            "cnnLstmConfidence": conf,
        }

        root, quality = parse_root_quality(orig)
        new_seg = dict(seg)
        new_seg["chord"] = orig

        if loaded is None or model is None or device is None:
            entry["finalChord"] = orig
            entry["refined"] = False
            entry["skipReason"] = "model_unavailable"
            details.append(entry)
            refined_segments.append(new_seg)
            continue

        if root is None or quality is None or quality not in REFINE_QUALITIES:
            entry["finalChord"] = orig
            entry["refined"] = False
            entry["skipReason"] = "quality_not_target"
            details.append(entry)
            refined_segments.append(new_seg)
            continue

        result = refine_beat_segment(audio_path, start, end, model, device)
        if result is None:
            entry["finalChord"] = orig
            entry["refined"] = False
            entry["skipReason"] = "segment_load_or_infer_failed"
            details.append(entry)
            refined_segments.append(new_seg)
            continue

        best_label, ref_confidence, prob_map = result
        entry["refinerLabel"] = best_label
        entry["confidence"] = ref_confidence
        entry["probabilities"] = prob_map

        # 與主程式一致：信心嚴格低於門檻則維持原和絃
        if ref_confidence < REFINER_CONFIDENCE_MIN:
            entry["finalChord"] = orig
            entry["refined"] = False
            entry["skipReason"] = "low_confidence"
            details.append(entry)
            refined_segments.append(new_seg)
            continue

        final_chord = f"{root}:{best_label}"
        new_seg["chord"] = final_chord
        entry["finalChord"] = final_chord
        entry["refined"] = True
        entry["skipReason"] = None
        details.append(entry)
        refined_segments.append(new_seg)

    return refined_segments, details, weights_path


def main() -> int:
    parser = argparse.ArgumentParser(description="和弦辨識 + 依 segment 套用 ChordRefiner 實驗")
    parser.add_argument("audio", type=Path, help="輸入音檔路徑")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="將 JSON 結果寫入檔案（未指定則印到 stdout）",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="印出除錯 log")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    audio_path = args.audio.expanduser().resolve()
    if not audio_path.is_file():
        print(f"找不到音檔：{audio_path}", file=sys.stderr)
        return 1

    _log.info("分析音檔（beat + chord）：%s", audio_path)
    beat_data, chord_data = analyze_audio_file(
        str(audio_path),
        beat_detector="madmom",
        chord_detector="chord-cnn-lstm",
        chord_dict="submission",
    )

    segments_in = list(chord_data.get("chords") or [])
    duration = float(chord_data.get("duration") or beat_data.get("duration") or 0.0)
    if segments_in and duration <= 0:
        duration = float(segments_in[-1].get("end") or 0.0)

    refined_segments, segment_details, weights_path = run_segment_wise_refine(
        str(audio_path),
        segments_in,
    )

    payload: dict[str, Any] = {
        "audioPath": str(audio_path),
        "duration": duration,
        "beatModel": beat_data.get("model_used") or "madmom",
        "chordModel": chord_data.get("model_used") or "chord-cnn-lstm",
        "refinerWeightsPath": weights_path,
        "confidenceThreshold": REFINER_CONFIDENCE_MIN,
        "targetQualities": sorted(REFINE_QUALITIES),
        "segmentCount": len(segments_in),
        "refinedCount": sum(1 for d in segment_details if d.get("refined")),
        "chordsOriginal": segments_in,
        "chordsAfterRefine": refined_segments,
        "segmentRefineLog": segment_details,
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        _log.info("已寫入：%s", args.output.resolve())
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
