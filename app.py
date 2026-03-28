from __future__ import annotations

import mimetypes
import os
import re
import shutil
import tempfile
from uuid import uuid4
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from yt_dlp import YoutubeDL

from analysis_engine import analyze_audio_file, get_engine_status
from beat_chord_refinement import align_chord_refine_report, refine_chords_with_beats
from grid_builder import build_frontend_analysis


BASE_DIR = Path(__file__).resolve().parent
AUDIO_CACHE_DIR = BASE_DIR / "runtime" / "audio_cache"
FIXED_BEAT_DETECTOR = "madmom"
FIXED_CHORD_DETECTOR = "chord-cnn-lstm"
YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[A-Za-z0-9_-]+"
)

# 終端機色彩碼（yt-dlp 錯誤訊息常帶 ANSI，回傳給前端前剥除）
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text or "")


def _resolve_youtube_download_path(ydl: YoutubeDL, info: dict[str, Any]) -> Path | None:
    """從 extract_info 結果找出實際寫入且非空的音檔路徑。"""
    candidates: list[str | None] = [info.get("filepath")]
    for req in info.get("requested_downloads") or []:
        if isinstance(req, dict):
            candidates.append(req.get("filepath"))
    candidates.append(ydl.prepare_filename(info))
    seen: set[str] = set()
    for raw in candidates:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        path = Path(raw)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def download_youtube_audio(video_id: str, workdir: str) -> tuple[str, str]:
    """
    下載 YouTube 音訊；若單一 format 得到 0-byte，改試其他 format 與 player_client。
    可緩解 yt-dlp 回報「The downloaded file is empty」的情況。
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    outtmpl = str(Path(workdir) / "source.%(ext)s")

    format_candidates = [
        "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best/worst",
        "bestaudio/best/worst",
        "ba/b",
    ]
    client_profiles: list[dict[str, Any]] = [
        {"youtube": {"player_client": ["android", "web"]}},
        {"youtube": {"player_client": ["web", "default"]}},
        {"youtube": {"player_client": ["ios", "web"]}},
    ]

    last_error: Exception | None = None
    for extractor_args in client_profiles:
        for fmt in format_candidates:
            options: dict[str, Any] = {
                "quiet": True,
                "noplaylist": True,
                "outtmpl": outtmpl,
                "format": fmt,
                "retries": 5,
                "fragment_retries": 10,
                "file_access_retries": 3,
                "socket_timeout": 45,
                "extractor_args": extractor_args,
            }
            try:
                with YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if not isinstance(info, dict):
                        continue
                    path = _resolve_youtube_download_path(ydl, info)
                    if path is not None:
                        return str(path.resolve()), str(info.get("title") or "YouTube Audio")
                    last_error = RuntimeError("下載回傳成功但檔案為空或路徑無法解析")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue

    hint = (
        "請更新 yt-dlp（例如：pip install -U yt-dlp）、改用上傳音檔，或稍後再試。"
        " 若影片需登入、僅限會員或地區限制，下載也可能失敗。"
    )
    detail = _strip_ansi(str(last_error)) if last_error else "未知錯誤"
    raise RuntimeError(f"YouTube 音訊下載失敗（檔案為空或無法取得）。{hint} 技術細節：{detail}") from last_error


app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def format_duration(total_seconds: int | None) -> str:
    if not total_seconds:
        return ""

    minutes, seconds = divmod(int(total_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def is_youtube_url(query: str) -> bool:
    return bool(YOUTUBE_URL_RE.match(query.strip()))


def build_search_result(entry: dict[str, Any]) -> dict[str, Any]:
    video_id = entry.get("id") or ""
    thumbnail = entry.get("thumbnail") or (
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
    )
    return {
        "id": video_id,
        "title": entry.get("title") or "Untitled",
        "thumbnail": thumbnail,
        "channel": entry.get("uploader") or entry.get("channel") or "Unknown channel",
        "duration": format_duration(entry.get("duration")),
        "upload_date": entry.get("upload_date") or "",
        "url": entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else ""),
    }


def search_youtube(query: str) -> list[dict[str, Any]]:
    options = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "noplaylist": True,
    }
    with YoutubeDL(options) as ydl:
        if is_youtube_url(query):
            info = ydl.extract_info(query, download=False)
            return [build_search_result(info)]

        search_info = ydl.extract_info(f"ytsearch8:{query}", download=False)
        entries = search_info.get("entries", []) if search_info else []
        return [build_search_result(entry) for entry in entries if entry]


def prune_audio_cache(max_files: int = 40) -> None:
    cached_files = [path for path in AUDIO_CACHE_DIR.iterdir() if path.is_file()]
    if len(cached_files) <= max_files:
        return

    cached_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for stale_file in cached_files[max_files:]:
        stale_file.unlink(missing_ok=True)


def cache_audio_file(source_path: str) -> tuple[Path, str | None]:
    source = Path(source_path)
    suffix = source.suffix.lower() or ".bin"
    cached_name = f"{uuid4().hex}{suffix}"
    cached_path = AUDIO_CACHE_DIR / cached_name
    shutil.copy2(source, cached_path)
    mime_type, _ = mimetypes.guess_type(cached_path.name)
    prune_audio_cache()
    return cached_path, mime_type


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/health")
def health() -> Any:
    return jsonify(get_engine_status())


@app.get("/api/search")
def api_search() -> Any:
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"success": False, "error": "請輸入 YouTube 關鍵字或連結。"}), 400

    try:
        results = search_youtube(query)
        return jsonify({"success": True, "results": results})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": f"YouTube 搜尋失敗：{exc}"}), 500


@app.get("/media/<path:filename>")
def serve_cached_media(filename: str) -> Any:
    target = AUDIO_CACHE_DIR / filename
    if not target.exists() or not target.is_file():
        abort(404)
    return send_from_directory(AUDIO_CACHE_DIR, filename, as_attachment=False, conditional=True)


@app.post("/api/analyze")
def api_analyze() -> Any:
    video_id = request.form.get("video_id", "").strip()
    title = request.form.get("title", "").strip()
    uploaded_file = request.files.get("audio_file")
    beat_detector = request.form.get("beat_detector", FIXED_BEAT_DETECTOR).strip() or FIXED_BEAT_DETECTOR
    chord_detector = request.form.get("chord_detector", FIXED_CHORD_DETECTOR).strip() or FIXED_CHORD_DETECTOR

    if not video_id and not uploaded_file:
        return jsonify({"success": False, "error": "請先選擇 YouTube 影片或上傳音檔。"}), 400
    if beat_detector != FIXED_BEAT_DETECTOR:
        return jsonify({"success": False, "error": f"目前只支援 {FIXED_BEAT_DETECTOR}。"}), 400
    if chord_detector != FIXED_CHORD_DETECTOR:
        return jsonify({"success": False, "error": f"目前只支援 {FIXED_CHORD_DETECTOR}。"}), 400

    try:
        with tempfile.TemporaryDirectory(prefix="igtgs_") as workdir:
            source_type = "upload"
            audio_path = ""
            resolved_title = title or "Uploaded Audio"

            if uploaded_file and uploaded_file.filename:
                safe_name = Path(uploaded_file.filename).name or "upload_audio"
                audio_path = str(Path(workdir) / safe_name)
                uploaded_file.save(audio_path)
            else:
                source_type = "youtube"
                audio_path, resolved_title = download_youtube_audio(video_id, workdir)

            cached_audio_path, playback_mime = cache_audio_file(audio_path)

            beat_data, chord_data = analyze_audio_file(
                audio_path,
                beat_detector=beat_detector,
                chord_detector=chord_detector,
                chord_dict="submission",
            )

            chord_data, chord_refine_report = refine_chords_with_beats(
                audio_path,
                beat_data,
                chord_data,
            )

            analysis_payload = build_frontend_analysis(
                resolved_title,
                source_type,
                beat_detector,
                chord_detector,
                beat_data,
                chord_data,
            )
            analysis_payload["playbackUrl"] = f"/media/{cached_audio_path.name}"
            analysis_payload["playbackMime"] = playback_mime
            analysis_payload["sourceFilename"] = cached_audio_path.name
            analysis_payload["raw"]["chordRefine"] = chord_refine_report
            align_chord_refine_report(analysis_payload, chord_refine_report)

            return jsonify(
                {
                    "success": True,
                    "analysis": analysis_payload,
                }
            )
    except Exception as exc:  # noqa: BLE001
        return jsonify(
            {
                "success": False,
                "error": (
                    "分析失敗。IGTGS 內建分析流程執行失敗，"
                    f"詳細錯誤：{_strip_ansi(str(exc))}"
                ),
            }
        ), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5055)
