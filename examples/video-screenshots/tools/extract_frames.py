"""从三条源视频重建示例截图；视频不随仓库分发，已提取的 PNG 可直接使用。"""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upper", type=Path, required=True, help="合规平台上篇视频")
    parser.add_argument("--lower", type=Path, required=True, help="合规平台下篇视频")
    parser.add_argument("--midterms", type=Path, required=True, help="11.3.mp4")
    args = parser.parse_args()
    materials = Path(__file__).resolve().parents[1] / "materials"
    manifest = json.loads((materials / "sources.json").read_text(encoding="utf-8"))
    sources = {key: getattr(args, key).resolve() for key in ("upper", "lower", "midterms")}
    for source in sources.values():
        if not source.is_file():
            parser.error(f"源视频不存在：{source}")
    for frame in manifest["frames"]:
        dest = materials / frame["file"]
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-ss", str(frame["time"]),
            "-i", str(sources[frame["source"]]), "-frames:v", "1", str(dest),
        ], check=True)
        print(f"{dest.name} ← {frame['source']} {frame['time']:.2f}s")


if __name__ == "__main__":
    main()
