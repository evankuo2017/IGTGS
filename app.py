from __future__ import annotations

import mimetypes
import os
import re
from collections import Counter
import shutil
import tempfile
from uuid import uuid4
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_file
from yt_dlp import YoutubeDL

from analysis_engine import analyze_audio_file, get_engine_status
from beat_chord_refinement import align_chord_refine_report, refine_chords_with_beats
from grid_builder import build_frontend_analysis, choose_meter_and_downbeats


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
    下載 YouTube 音訊。

    上傳 MP3 可播、YouTube 常失敗多半是瀏覽器對 **webm/opus** 支援差。
    因此：**有 ffmpeg 時優先轉成 m4a/mp3**（與上傳檔體驗一致）；否則再試原生 m4a，最後才 webm。
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    outtmpl = str(Path(workdir) / "source.%(ext)s")

    client_profiles: list[dict[str, Any]] = [
        {"youtube": {"player_client": ["android", "web"]}},
        {"youtube": {"player_client": ["web", "default"]}},
        {"youtube": {"player_client": ["ios", "web"]}},
    ]

    base_opts: dict[str, Any] = {
        "quiet": True,
        "noplaylist": True,
        "outtmpl": outtmpl,
        "retries": 5,
        "fragment_retries": 10,
        "file_access_retries": 3,
        "socket_timeout": 45,
    }

    last_error: Exception | None = None

    def try_ydl(options: dict[str, Any]) -> tuple[str, str] | None:
        nonlocal last_error
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                if not isinstance(info, dict):
                    return None
                path = _resolve_youtube_download_path(ydl, info)
                if path is not None:
                    return str(path.resolve()), str(info.get("title") or "YouTube Audio")
                last_error = RuntimeError("下載回傳成功但檔案為空或路徑無法解析")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        return None

    # --- 階段 1：ffmpeg 轉成 m4a / mp3（優先，對齊「上傳音檔可播」）---
    if shutil.which("ffmpeg"):
        for codec in ("m4a", "mp3"):
            for extractor_args in client_profiles:
                opts = {
                    **base_opts,
                    "format": "bestaudio/best",
                    "extractor_args": extractor_args,
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": codec,
                            "preferredquality": "192",
                        },
                    ],
                }
                got = try_ydl(opts)
                if got is not None:
                    return got

    # --- 階段 2：不轉檔，盡量只抓 m4a / mp3，避免先落到 webm ---
    format_candidates = [
        "ba[ext=m4a]/ba[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best/worst",
        "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best/worst",
        "bestaudio/best/worst",
        "ba/b",
    ]
    for extractor_args in client_profiles:
        for fmt in format_candidates:
            opts = {**base_opts, "format": fmt, "extractor_args": extractor_args}
            got = try_ydl(opts)
            if got is not None:
                return got

    hint = (
        "請安裝 ffmpeg 並確認在 PATH 中，讓 YouTube 音訊轉成 m4a/mp3（與上傳檔相同易播放）；"
        "或更新 yt-dlp、改上傳音檔。若影片需登入或地區限制也可能失敗。"
    )
    detail = _strip_ansi(str(last_error)) if last_error else "未知錯誤"
    raise RuntimeError(f"YouTube 音訊下載失敗（檔案為空或無法取得）。{hint} 技術細節：{detail}") from last_error


# 快取／串流音訊用 MIME（與 serve_cached_media 一致；避免 .webm 被 guess 成 video/webm 害 <audio> 拒播）
_AUDIO_MIME_BY_SUFFIX: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
    ".opus": "audio/opus",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}


def _playback_mime_for_file(path: Path) -> str | None:
    """給 JSON playbackMime 與瀏覽器用；YouTube 常見 webm 不可回傳 video/webm。"""
    ext = path.suffix.lower()
    if ext in _AUDIO_MIME_BY_SUFFIX:
        return _AUDIO_MIME_BY_SUFFIX[ext]
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed == "video/webm":
        return "audio/webm"
    if guessed and guessed.startswith("audio/"):
        return guessed
    return guessed


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
    # copy2 會保留「來源檔」的 mtime；YouTube 暫存檔時間戳可能很舊，導致 prune 依 mtime 排序時
    # 把「剛寫入的快取」當成最舊檔刪除 → /media/... 404。上傳檔通常較新故較少踩到。
    cached_path.touch()
    mime_type = _playback_mime_for_file(cached_path)
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


def _refine_user_hint(report: dict[str, Any]) -> str | None:
    """Refine 按鈕為灰時給使用者的簡短原因（對應前端 refineUserHint）。"""
    beats = list(report.get("beats") or [])
    if not report.get("success") and report.get("error") == "no_beats":
        return "沒有節拍資料，無法 refine。"
    n_refined = sum(1 for b in beats if b.get("refined"))
    if n_refined > 0:
        return None
    if not beats:
        return "無 refine 對照資料。請確認分析有成功產生節拍。"
    c = Counter(b.get("skipReason") for b in beats)
    n = len(beats)
    if c.get("model_unavailable", 0) == n:
        return "未載入 ChordRefiner：請將 best_chord_model.pth 置於 igtgs_backend/models/ChordRefiner/"
    if c.get("quality_not_target", 0) == n:
        return "此曲辨識結果沒有 maj/maj7/min/min7 類型，故未跑 refiner。"
    if c.get("low_confidence", 0) >= max(1, n // 2):
        return "Refiner 輸出信心多數低於 0.5，未套用。可檢查模型或音檔清晰度。"
    return "沒有任何拍通過 refine；請開 Raw Data → chordRefine 查看各筆 skipReason。"


@app.get("/media/<path:filename>")
def serve_cached_media(filename: str) -> Any:
    target = AUDIO_CACHE_DIR / filename
    if not target.exists() or not target.is_file():
        abort(404)
    mimetype = _playback_mime_for_file(target)
    if not mimetype or not str(mimetype).startswith("audio/"):
        mimetype = "application/octet-stream"
    resp = send_file(target, mimetype=mimetype, conditional=True, max_age=3600)
    resp.headers["Accept-Ranges"] = "bytes"
    if mimetype and str(mimetype).startswith("audio/"):
        resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


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

            # 先套用 3/4、4/4 自動選拍與 refine 使用同一套 beat_data，避免 refine 報告 beatIndex 與譜面格子错位
            meter_choice = choose_meter_and_downbeats(beat_data, chord_data)
            if meter_choice:
                beat_data = dict(beat_data)
                beat_data["downbeats"] = meter_choice["downbeats"]
                beat_data["time_signature"] = meter_choice["time_signature"]

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
                skip_meter_selection=True,
            )
            analysis_payload["playbackUrl"] = f"/media/{cached_audio_path.name}"
            analysis_payload["playbackMime"] = playback_mime
            analysis_payload["sourceFilename"] = cached_audio_path.name
            analysis_payload["raw"]["chordRefine"] = chord_refine_report
            align_chord_refine_report(analysis_payload, chord_refine_report)
            hint = _refine_user_hint(chord_refine_report)
            if hint:
                analysis_payload["refineUserHint"] = hint

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
