#!/usr/bin/env python3
"""
音樂音源分離推理腳本（對齊 Hugging Face：abidlabs/music-separation）

該 Space 使用 Demucs v4 系列模型，預設為 htdemucs，並以 --two-stems=vocals
輸出人聲與伴奏（instrumental）。本腳本以相同 CLI 呼叫 `python -m demucs.separate`。

依賴：pip install demucs（需已安裝 PyTorch，見專案 requirements.txt）

使用範例：
  python music_separation_inference.py /path/to/song.wav
  python music_separation_inference.py song.mp3 -o ./separated --device cuda
  python music_separation_inference.py song.wav --full-stems   # 四軌：drums/bass/other/vocals
  python music_separation_inference.py song.wav --mp3          # 另存 .mp3（方便播放器／手機）
  python music_separation_inference.py song.wav --mp3 --mp3-only  # 僅保留 mp3，刪除 wav
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _run_demucs(
    audio_path: Path,
    out_dir: Path,
    model: str,
    two_stems: str | None,
    device: str | None,
    extra_args: list[str],
    *,
    log_cmd: bool = True,
) -> int:
    """呼叫 demucs.separate，回傳 process return code。log_cmd=False 時不印指令（供 refiner 內嵌呼叫）。"""
    cmd: list[str] = [
        sys.executable,
        "-m",
        "demucs.separate",
        "-n",
        model,
        str(audio_path.resolve()),
        "-o",
        str(out_dir.resolve()),
    ]
    if two_stems:
        cmd.append(f"--two-stems={two_stems}")
    if device:
        cmd.extend(["-d", device])
    cmd.extend(extra_args)

    if log_cmd:
        print("執行指令:", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


def demucs_no_vocals_wav_path(
    audio_path: str | Path,
    out_dir: str | Path,
    *,
    model: str = "htdemucs",
    device: str | None = None,
    extra_args: list[str] | None = None,
    log_cmd: bool = False,
) -> Path:
    """
    對整首音檔跑一次 Demucs（two-stems=vocals），回傳 no_vocals.wav 路徑。
    供 ChordRefiner 以伴奏軌做二次判斷時使用（時間軸與原檔對齊）。

    需已安裝 demucs；失敗時拋出 RuntimeError / FileNotFoundError。
    """
    ap = Path(audio_path)
    if not ap.is_file():
        raise FileNotFoundError(str(ap))
    try:
        import demucs  # noqa: F401
    except ImportError as e:
        raise RuntimeError("未安裝 demucs，請 pip install demucs") from e

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rc = _run_demucs(
        ap,
        out,
        model,
        "vocals",
        device,
        list(extra_args or []),
        log_cmd=log_cmd,
    )
    if rc != 0:
        raise RuntimeError(f"demucs.separate 失敗，結束碼 {rc}")

    track = ap.stem
    nv = out / model / track / "no_vocals.wav"
    if not nv.is_file():
        raise FileNotFoundError(f"未產生 no_vocals.wav：{nv}")
    return nv.resolve()


def _print_outputs(work_dir: Path, track_name: str, model: str, two_stems: str | None) -> None:
    """列印 demucs 預設輸出路徑（與輸入檔 basename 無副檔名相同之資料夾）。"""
    base = work_dir / model / track_name
    if not base.is_dir():
        print(f"警告：預期輸出目錄不存在：{base}", file=sys.stderr)
        return
    print("輸出目錄:", base.resolve())
    for p in sorted(base.glob("*.wav")):
        print(" ", p.name, "->", p.resolve())


def _ffmpeg_wav_to_mp3(wav_path: Path, bitrate: str) -> Path:
    """將單一 WAV 轉成 MP3（需系統 PATH 有 ffmpeg）。回傳產生的 .mp3 路徑。"""
    mp3_path = wav_path.with_suffix(".mp3")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(wav_path.resolve()),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(mp3_path.resolve()),
    ]
    subprocess.run(cmd, check=True)
    return mp3_path


def _encode_output_dir_to_mp3(
    base: Path,
    bitrate: str,
    remove_wav: bool,
) -> list[Path]:
    """將 base 目錄內所有 .wav 轉成 .mp3；remove_wav 為 True 時刪除原 wav。"""
    if not shutil.which("ffmpeg"):
        print(
            "錯誤：未找到 ffmpeg，無法輸出 MP3。請安裝 ffmpeg 並確認在 PATH 中。\n"
            "  Ubuntu/Debian: sudo apt install ffmpeg",
            file=sys.stderr,
        )
        raise SystemExit(1)

    mp3_paths: list[Path] = []
    for wav in sorted(base.glob("*.wav")):
        print("轉 MP3:", wav.name, flush=True)
        mp3_paths.append(_ffmpeg_wav_to_mp3(wav, bitrate))
        if remove_wav:
            wav.unlink()
    return mp3_paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Demucs 音樂音源分離（預設對齊 HF music-separation：htdemucs + vocals/伴奏）"
    )
    parser.add_argument(
        "audio",
        type=Path,
        help="輸入音檔路徑（wav/mp3/flac 等，需 ffmpeg 可讀）",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=Path("demucs_out"),
        help="輸出根目錄（預設：./demucs_out）",
    )
    parser.add_argument(
        "-n",
        "--model",
        default="htdemucs",
        help="Demucs 模型名稱（預設 htdemucs，與 HF Space 一致）",
    )
    parser.add_argument(
        "--two-stems",
        default="vocals",
        metavar="STEM",
        help="僅分離兩軌：vocals | drums | bass | ...；設為空字串則輸出完整多軌",
    )
    parser.add_argument(
        "--full-stems",
        action="store_true",
        help="輸出完整多軌（等同不帶 --two-stems；覆寫 --two-stems）",
    )
    parser.add_argument(
        "-d",
        "--device",
        default=None,
        metavar="DEVICE",
        help="例如 cpu 或 cuda（未指定則交給 demucs 預設）",
    )
    parser.add_argument(
        "--mp3",
        action="store_true",
        help="分離完成後另存 MP3（需 ffmpeg；預設仍保留 WAV）",
    )
    parser.add_argument(
        "--mp3-only",
        action="store_true",
        help="與 --mp3 併用：轉完後刪除 WAV，只留 MP3",
    )
    parser.add_argument(
        "--mp3-bitrate",
        default="192k",
        metavar="RATE",
        help="MP3 位元率（預設 192k）",
    )
    args, demucs_extra = parser.parse_known_args()
    audio_path = args.audio
    if not audio_path.is_file():
        print(f"錯誤：找不到音檔：{audio_path}", file=sys.stderr)
        return 1

    # 確認 demucs 可 import
    try:
        import demucs  # noqa: F401
    except ImportError:
        print(
            "錯誤：未安裝 demucs。請執行：pip install demucs\n"
            "（需已安裝相容的 torch / torchaudio）",
            file=sys.stderr,
        )
        return 1

    two_stems: str | None
    if args.full_stems:
        two_stems = None
    else:
        ts = (args.two_stems or "").strip()
        two_stems = ts if ts else None

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    rc = _run_demucs(
        audio_path,
        out_dir,
        args.model,
        two_stems,
        args.device,
        demucs_extra,
        log_cmd=True,
    )
    if rc != 0:
        return rc

    track_name = audio_path.stem
    base = out_dir / args.model / track_name
    _print_outputs(out_dir, track_name, args.model, two_stems)

    if args.mp3_only and not args.mp3:
        print("錯誤：--mp3-only 須與 --mp3 一併使用", file=sys.stderr)
        return 1

    if args.mp3:
        try:
            mp3_list = _encode_output_dir_to_mp3(
                base,
                bitrate=args.mp3_bitrate,
                remove_wav=args.mp3_only,
            )
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"轉 MP3 失敗：{e}", file=sys.stderr)
            return 1
        print("MP3 輸出：")
        for p in mp3_list:
            print(" ", p.name, "->", p.resolve())

    if two_stems == "vocals":
        v = base / "vocals.wav"
        nv = base / "no_vocals.wav"
        v_m = base / "vocals.mp3"
        nv_m = base / "no_vocals.mp3"
        if v.is_file() and nv.is_file():
            print("（與 HF Space 相同）人聲 WAV:", v.resolve())
            print("（與 HF Space 相同）伴奏 no_vocals WAV:", nv.resolve())
        if v_m.is_file() and nv_m.is_file():
            print("（與 HF Space 相同）人聲 MP3:", v_m.resolve())
            print("（與 HF Space 相同）伴奏 no_vocals MP3:", nv_m.resolve())

    return 0


if __name__ == "__main__":
    sys.exit(main())
