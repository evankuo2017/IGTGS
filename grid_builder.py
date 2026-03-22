from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def to_beat_info(beat_data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_beats = beat_data.get("beats") or []
    time_signature_raw = beat_data.get("time_signature", 4)

    if isinstance(time_signature_raw, str) and "/" in time_signature_raw:
        time_signature = int(time_signature_raw.split("/")[0])
    else:
        time_signature = int(time_signature_raw or 4)

    beat_info: list[dict[str, Any]] = []
    for index, beat_time in enumerate(raw_beats):
        beat_info.append(
            {
                "time": float(beat_time),
                "strength": 0.8,
                "beatNum": (index % time_signature) + 1,
            }
        )
    return beat_info


def synchronize_chords(
    chords: list[dict[str, Any]],
    beats: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not chords or not beats:
        return []

    beat_to_chord: dict[int, str] = {}
    beat_index = 0

    for chord in chords:
        chord_start = float(chord.get("start", chord.get("time", 0.0)))
        chord_name = chord.get("chord", "N/C")
        if chord_name == "N":
            chord_name = "N/C"

        while (
            beat_index < len(beats) - 1
            and abs(beats[beat_index + 1]["time"] - chord_start)
            < abs(beats[beat_index]["time"] - chord_start)
        ):
            beat_index += 1

        beat_to_chord[beat_index] = chord_name

    synchronized: list[dict[str, Any]] = []
    last_chord = "N/C"
    for index, beat in enumerate(beats):
        chord_name = beat_to_chord.get(index, last_chord)
        synchronized.append(
            {
                "chord": chord_name,
                "beatIndex": index,
                "beatNum": beat["beatNum"],
            }
        )
        last_chord = chord_name

    return synchronized


def score_downbeat_alignment(
    chord_series: list[str],
    time_signature: int,
) -> tuple[float, int]:
    """評分：和弦變換落在下拍加權、非下拍扣分。與 ChordMiniApp 相同的啟發式。"""
    if not chord_series or len(chord_series) < 2:
        return 0.0, 0

    def is_valid(c: str) -> bool:
        return bool(c and c not in ("", "N.C.", "N/C", "N"))

    change_at: list[bool] = [False] * len(chord_series)
    for i in range(1, len(chord_series)):
        prev, curr = chord_series[i - 1], chord_series[i]
        if is_valid(prev) and is_valid(curr) and prev != curr:
            change_at[i] = True

    on_weight = 2
    off_penalty = 1
    best_score = float("-inf")
    best_shift = 0

    for shift in range(time_signature):
        on_down = 0
        off_down = 0
        for i in range(1, len(chord_series)):
            if not change_at[i]:
                continue
            pos = ((i - shift) % time_signature + time_signature) % time_signature
            if pos == 0:
                on_down += 1
            else:
                off_down += 1
        score = on_down * on_weight - off_down * off_penalty
        if score > best_score:
            best_score = score
            best_shift = shift

    return best_score if best_score != float("-inf") else 0.0, best_shift


def choose_meter_and_downbeats(
    beat_data: dict[str, Any],
    chord_data: dict[str, Any],
) -> dict[str, Any] | None:
    """
    依 ChordMiniApp 機制：用和弦變換與下拍對齊分數，在 3/4 與 4/4 候選間選擇。
    若有 downbeat_candidates 則回傳 {'downbeats': [...], 'time_signature': '3/4'|'4/4'}，否則回傳 None。
    """
    candidates = beat_data.get("downbeat_candidates") or {}
    if not candidates or "3" not in candidates or "4" not in candidates:
        return None

    raw_beats = beat_data.get("beats") or []
    chords = chord_data.get("chords") or []
    if not raw_beats or not chords:
        return None

    beats_for_sync = [
        {"time": float(t), "strength": 0.8, "beatNum": (i % 4) + 1}
        for i, t in enumerate(raw_beats)
    ]
    synced = synchronize_chords(chords, beats_for_sync)
    chord_series = [s["chord"] for s in synced]

    s3, _ = score_downbeat_alignment(chord_series, 3)
    s4, _ = score_downbeat_alignment(chord_series, 4)
    winner = 3 if s3 > s4 else 4
    downbeats = candidates.get(str(winner)) or []
    time_sig = f"{winner}/4"

    return {"downbeats": downbeats, "time_signature": time_sig}


def calculate_optimal_shift(chords: list[str], time_signature: int, padding_count: int = 0) -> int:
    if not chords:
        return 0

    best_shift = 0
    best_changes = -1

    for shift in range(time_signature):
        previous_downbeat_chord = ""
        chord_changes = 0

        for index, chord in enumerate(chords):
            visual_position = padding_count + shift + index
            beat_in_measure = (visual_position % time_signature) + 1
            is_downbeat = beat_in_measure == 1
            if not is_downbeat:
                continue

            is_valid = chord not in ("", "N.C.", "N/C", "N")
            is_change = is_valid and previous_downbeat_chord and chord != previous_downbeat_chord
            chord_starts_here = index == 0 or chords[index - 1] != chord

            if is_change and chord_starts_here:
                chord_changes += 1

            if is_valid:
                previous_downbeat_chord = chord

        if chord_changes > best_changes:
            best_shift = shift
            best_changes = chord_changes

    return best_shift


def calculate_padding_and_shift(
    first_detected_beat_time: float,
    bpm: float,
    time_signature: int,
    chords: list[str],
) -> dict[str, int]:
    padding_count = 0
    if first_detected_beat_time > 0.05 and bpm > 0:
        raw_padding_count = int((first_detected_beat_time / 60.0) * bpm)
        beat_duration = round(60.0 / bpm, 3)
        gap_ratio = first_detected_beat_time / beat_duration if beat_duration else 0
        padding_count = 1 if raw_padding_count == 0 and gap_ratio > 0.2 else raw_padding_count

        if padding_count >= time_signature:
            full_measures = padding_count // time_signature
            padding_count = padding_count - (full_measures * time_signature)

    shift_count = calculate_optimal_shift(chords, time_signature, padding_count) if chords else 0

    return {
        "paddingCount": max(0, int(padding_count)),
        "shiftCount": max(0, int(shift_count)),
        "totalPaddingCount": max(0, int(padding_count + shift_count)),
    }


def get_chord_grid_data(analysis_result: dict[str, Any]) -> dict[str, Any]:
    synchronized_chords = analysis_result.get("synchronizedChords") or []
    beats = analysis_result.get("beats") or []

    if not synchronized_chords:
        return {
            "chords": [],
            "beats": [],
            "hasPadding": True,
            "paddingCount": 0,
            "shiftCount": 0,
            "totalPaddingCount": 0,
            "originalAudioMapping": [],
        }

    time_signature = int(analysis_result.get("beatDetectionResult", {}).get("time_signature") or 4)
    bpm = float(analysis_result.get("beatDetectionResult", {}).get("bpm") or 120)
    first_detected_beat = float(beats[0]["time"]) if beats else 0.0

    chord_sequence = [item["chord"] for item in synchronized_chords]
    spacing = calculate_padding_and_shift(first_detected_beat, bpm, time_signature, chord_sequence)
    padding_count = spacing["paddingCount"]
    shift_count = spacing["shiftCount"]

    padding_chords = ["N.C."] * padding_count
    padding_timestamps = []
    if padding_count > 0:
      padding_duration = first_detected_beat
      padding_beat_duration = padding_duration / padding_count if padding_count else 0
      padding_timestamps = [index * padding_beat_duration for index in range(padding_count)]

    regular_chords = chord_sequence
    regular_beats = [float(beats[item["beatIndex"]]["time"]) for item in synchronized_chords]
    shift_nulls = [None] * shift_count

    final_chords = ([""] * shift_count) + padding_chords + regular_chords
    final_beats = shift_nulls + padding_timestamps + regular_beats

    original_audio_mapping = []
    for visual_index, item in enumerate(synchronized_chords):
        original_audio_mapping.append(
            {
                "chord": item["chord"],
                "timestamp": float(beats[item["beatIndex"]]["time"]),
                "visualIndex": visual_index + shift_count + padding_count,
                "audioIndex": item["beatIndex"],
            }
        )

    return {
        "chords": final_chords,
        "beats": final_beats,
        "hasPadding": True,
        "paddingCount": padding_count,
        "shiftCount": shift_count,
        "totalPaddingCount": padding_count + shift_count,
        "originalAudioMapping": original_audio_mapping,
    }


def build_measure_sections(chord_grid_data: dict[str, Any], time_signature: int) -> list[dict[str, Any]]:
    chords = chord_grid_data.get("chords") or []
    beats = chord_grid_data.get("beats") or []
    padding_count = int(chord_grid_data.get("paddingCount") or 0)
    shift_count = int(chord_grid_data.get("shiftCount") or 0)

    measures: list[dict[str, Any]] = []
    for start in range(0, len(chords), time_signature):
        cells = []
        slice_chords = chords[start : start + time_signature]
        slice_beats = beats[start : start + time_signature]

        for offset, chord in enumerate(slice_chords):
            absolute_index = start + offset
            cells.append(
                {
                    "index": absolute_index,
                    "chord": chord,
                    "time": slice_beats[offset] if offset < len(slice_beats) else None,
                    "isShift": absolute_index < shift_count,
                    "isPadding": shift_count <= absolute_index < shift_count + padding_count,
                    "beatInMeasure": offset + 1,
                }
            )

        while len(cells) < time_signature:
            cells.append(
                {
                    "index": start + len(cells),
                    "chord": "",
                    "time": None,
                    "isShift": False,
                    "isPadding": False,
                    "beatInMeasure": len(cells) + 1,
                }
            )

        measures.append(
            {
                "measureNumber": len(measures) + 1,
                "cells": cells,
            }
        )

    return measures


def build_frontend_analysis(
    title: str,
    source_type: str,
    beat_detector: str,
    chord_detector: str,
    beat_data: dict[str, Any],
    chord_data: dict[str, Any],
) -> dict[str, Any]:
    # 依 ChordMiniApp 機制：用和弦變換與下拍對齊分數，自動選擇 3/4 或 4/4
    chosen = choose_meter_and_downbeats(beat_data, chord_data)
    if chosen:
        beat_data = dict(beat_data)
        beat_data["downbeats"] = chosen["downbeats"]
        beat_data["time_signature"] = chosen["time_signature"]
        _log.info("Auto-selected meter (chord-downbeat alignment): → %s", chosen["time_signature"])

    beats = to_beat_info(beat_data)
    synchronized_chords = synchronize_chords(chord_data.get("chords") or [], beats)
    time_signature_raw = beat_data.get("time_signature", 4)
    time_signature = (
        int(str(time_signature_raw).split("/")[0])
        if isinstance(time_signature_raw, str) and "/" in time_signature_raw
        else int(time_signature_raw or 4)
    )

    analysis_result = {
        "chords": chord_data.get("chords") or [],
        "beats": beats,
        "downbeats": beat_data.get("downbeats") or [],
        "downbeats_with_measures": [],
        "beats_with_positions": beats,
        "synchronizedChords": synchronized_chords,
        "beatModel": beat_data.get("model_used") or beat_detector,
        "chordModel": chord_data.get("model_used") or chord_detector,
        "audioDuration": float(chord_data.get("duration") or beat_data.get("duration") or 0),
        "beatDetectionResult": {
            "time_signature": time_signature,
            "bpm": beat_data.get("bpm"),
            "beatShift": 0,
            "beat_time_range_start": float(beats[0]["time"]) if beats else 0.0,
            "beat_time_range_end": float(beats[-1]["time"]) if beats else 0.0,
        },
    }

    chord_grid_data = get_chord_grid_data(analysis_result)
    analysis_result["beatDetectionResult"]["paddingCount"] = chord_grid_data["paddingCount"]
    analysis_result["beatDetectionResult"]["shiftCount"] = chord_grid_data["shiftCount"]
    measures = build_measure_sections(chord_grid_data, time_signature)
    unique_chords = sorted(
        {
            item.get("chord", "")
            for item in chord_data.get("chords") or []
            if item.get("chord") and item.get("chord") not in {"N", "N/C", "N.C."}
        }
    )

    return {
        "title": title,
        "sourceType": source_type,
        "summary": {
            "bpm": beat_data.get("bpm"),
            "timeSignature": time_signature,
            "totalMeasures": len(measures),
            "totalChords": chord_data.get("total_chords") or len(chord_data.get("chords") or []),
            "audioDuration": round(float(chord_data.get("duration") or beat_data.get("duration") or 0), 1),
            "beatModel": beat_data.get("model_used") or beat_detector,
            "chordModel": chord_data.get("model_used") or chord_detector,
        },
        "analysisResult": analysis_result,
        "chordGridData": chord_grid_data,
        "measures": measures,
        "uniqueChords": unique_chords,
        "raw": {
            "beatData": beat_data,
            "chordData": chord_data,
        },
    }
