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
from grid_builder import build_frontend_analysis


BASE_DIR = Path(__file__).resolve().parent
AUDIO_CACHE_DIR = BASE_DIR / "runtime" / "audio_cache"
FIXED_BEAT_DETECTOR = "madmom"
FIXED_CHORD_DETECTOR = "chord-cnn-lstm"
YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[A-Za-z0-9_-]+"
)


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


def download_youtube_audio(video_id: str, workdir: str) -> tuple[str, str]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    options = {
        "quiet": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "outtmpl": str(Path(workdir) / "source.%(ext)s"),
    }

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        source_path = ydl.prepare_filename(info)
        return source_path, info.get("title") or "YouTube Audio"


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
                    f"詳細錯誤：{exc}"
                ),
            }
        ), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5055)
