"""通用工具：JSON 读写、哈希、ffmpeg / ffprobe 封装、音频读写。时间单位统一为秒。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import wave
from pathlib import Path
from typing import Any, List, Tuple

import numpy as np

SR = 48000


class ScriptCutError(RuntimeError):
    """可预期的流程错误（缺文件、校验不通过、外部服务失败）：CLI 打印 message 并以非 0 退出。"""


# ---------------------------------------------------------------- JSON / 哈希

def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any, indent: int | None = 1) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    return path


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def key_hash(obj: Any, n: int = 10) -> str:
    return hashlib.md5(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:n]


# ---------------------------------------------------------------- ffmpeg

def _run(args: list, input: bytes | None = None, text: bool = True) -> subprocess.CompletedProcess:
    if text and input is None:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return subprocess.run(args, capture_output=True, input=input)


def _check(proc: subprocess.CompletedProcess, what: str) -> None:
    if proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else proc.stderr.decode("utf-8", "replace")
        tail = "\n".join(err.strip().splitlines()[-5:])
        raise ScriptCutError(f"{what} 失败（exit {proc.returncode}）：\n{tail}")


def probe_duration(path: Path) -> float:
    proc = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    _check(proc, f"ffprobe {path}")
    return float(proc.stdout.strip().splitlines()[0])


def probe_video(path: Path) -> Tuple[float, int, int]:
    """(时长, 宽, 高)"""
    proc = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                 "-show_entries", "format=duration", "-of", "json", str(path)])
    _check(proc, f"ffprobe {path}")
    d = json.loads(proc.stdout)
    st = (d.get("streams") or [{}])[0]
    return float(d["format"]["duration"]), int(st.get("width", 0)), int(st.get("height", 0))


def detect_silences(path: Path, noise_db: float = -40.0, min_silence: float = 0.2) -> List[Tuple[float, float]]:
    """静音区间 [(开始, 结束)]；结尾静音没有 silence_end 时用媒体时长补齐。"""
    proc = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                 "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"])
    _check(proc, f"silencedetect {path}")
    spans, start = [], None
    for line in proc.stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m and start is not None:
            spans.append((start, float(m.group(1))))
            start = None
    if start is not None:
        spans.append((start, probe_duration(path)))
    return spans


def decode_audio(path: Path, sr: int = SR, channels: int = 1) -> np.ndarray:
    """任意音频 → float32；单声道返回 (N,)，立体声返回 (2, N)。"""
    proc = _run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"],
                text=False)
    _check(proc, f"解码 {path}")
    x = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    return x if channels == 1 else x.reshape(-1, channels).T


def write_audio(path: Path, x: np.ndarray, sr: int = SR, codec: str = "pcm_s16le") -> Path:
    """float 数组 → wav（单声道 (N,) 或立体声 (2, N)）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    x = np.clip(np.asarray(x, dtype=np.float32), -1, 1)
    ch = 1 if x.ndim == 1 else x.shape[0]
    data = x if ch == 1 else x.T
    proc = _run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", str(ch), "-i", "-",
                 "-c:a", codec, str(path)], input=np.ascontiguousarray(data).tobytes(), text=False)
    _check(proc, f"写音频 {path}")
    return path


def trim_silence(x: np.ndarray, sr: int = SR, thresh_db: float = -42, pad: float = 0.03) -> np.ndarray:
    """裁掉首尾静音（相对峰值的阈值）。"""
    if not len(x):
        return x
    env = np.abs(x)
    thr = 10 ** (thresh_db / 20) * max(float(env.max()), 1e-9)
    idx = np.nonzero(env > thr)[0]
    if not len(idx):
        return x
    return x[max(0, idx[0] - int(pad * sr)): min(len(x), idx[-1] + int(pad * sr))]


def wav_duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def loudness(path: Path) -> float | None:
    proc = _run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"])
    lines = [ln for ln in proc.stderr.splitlines() if ln.strip().startswith("I:")]
    try:
        return float(lines[-1].split()[1])
    except (IndexError, ValueError):
        return None


def loudnorm(src: Path, dst: Path, I: float = -14, TP: float = -1.0, LRA: float = 11, sr: int = SR) -> Path:
    """两遍 loudnorm：先测量再线性归一，响度准、不泵。"""
    meas = _run(["ffmpeg", "-hide_banner", "-i", str(src), "-af",
                 f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json", "-f", "null", "-"])
    _check(meas, "loudnorm 测量")
    s = meas.stderr
    j = json.loads(s[s.rindex("{"): s.rindex("}") + 1])
    af = (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
          f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    proc = _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", af, "-ar", str(sr), str(dst)])
    _check(proc, "loudnorm")
    return Path(dst)


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(items: list[dict]) -> str:
    """[{start, end, text}] → SRT 文本"""
    return "\n".join(f"{k}\n{srt_time(x['start'])} --> {srt_time(x['end'])}\n{x['text']}\n"
                     for k, x in enumerate(items, 1))


def rel(path: Path, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()
