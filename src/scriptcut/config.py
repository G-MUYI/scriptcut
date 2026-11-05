"""配置：.env / 环境变量 / 包内资源路径 / 字体查找。源码里不写任何绝对路径和密钥。"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

PKG = Path(__file__).resolve().parent
REPO = PKG.parents[1]
STYLES_DIR = PKG / "styles"
WEB_DIR = PKG / "render" / "web"
TEMPLATES_DIR = PKG / "templates"

_loaded: set[Path] = set()


def load_env(*dirs: Path) -> None:
    """依次读取 dirs/.env、当前目录/.env、仓库根目录/.env；已存在的环境变量不覆盖。"""
    for d in [*dirs, Path.cwd(), REPO]:
        p = (Path(d) / ".env").resolve()
        if p in _loaded or not p.is_file():
            continue
        _loaded.add(p)
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:
                    os.environ.setdefault(k.strip(), v)


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def proxies() -> dict | None:
    px = env("FISH_PROXY") or env("HTTPS_PROXY") or env("https_proxy")
    return {"http": px, "https": px} if px else None


# ---------------------------------------------------------------- 外部程序

def which(name: str) -> str | None:
    return shutil.which(name)


def jianying_drafts_dir(project_dir: Path | None = None) -> Path:
    """剪映草稿目录：JIANYING_DRAFT_DIR > 各系统默认位置 > 项目 build/jianying/。"""
    p = env("JIANYING_DRAFT_DIR")
    if p:
        return Path(p)
    system = platform.system()
    if system == "Windows" and env("LOCALAPPDATA"):
        root = Path(env("LOCALAPPDATA")) / "JianyingPro" / "User Data"
        cfg = root / "Config" / "globalSetting"
        if cfg.exists():  # 用户在剪映里改过草稿位置
            for line in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("currentCustomDraftPath="):
                    custom = Path(line.split("=", 1)[1].strip())
                    if custom.is_dir():
                        return custom
        return root / "Projects" / "com.lveditor.draft"
    if system == "Darwin":
        mac = Path.home() / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"
        if mac.parent.exists():
            return mac
    return (project_dir or Path.cwd()) / "build" / "jianying"


# ---------------------------------------------------------------- 字体

FONT_CANDIDATES = [
    # 思源黑体 / Noto Sans CJK（OFL，推荐）
    "NotoSansCJK-Bold.ttc", "NotoSansCJKsc-Bold.otf", "NotoSansSC-Bold.otf", "NotoSansSC-Bold.ttf",
    "NotoSansSC-VF.ttf", "SourceHanSansSC-Bold.otf", "SourceHanSansCN-Bold.otf", "SourceHanSans-Bold.ttc",
    "思源黑体H.ttf",
    # Windows
    "msyhbd.ttc", "msyh.ttc", "simhei.ttf",
    # macOS
    "PingFang.ttc", "STHeiti Medium.ttc", "Hiragino Sans GB.ttc",
    # Linux 兜底
    "wqy-microhei.ttc", "DroidSansFallbackFull.ttf",
]


def _font_dirs(extra: list[Path]) -> list[Path]:
    dirs = list(extra)
    system = platform.system()
    if system == "Windows":
        dirs += [Path(env("WINDIR", "C:" + "\\Windows")) / "Fonts",Path(env("LOCALAPPDATA")) / "Microsoft" / "Windows" / "Fonts"]
    elif system == "Darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"), Path("/Library/Fonts"),
                 Path.home() / "Library" / "Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
                 Path.home() / ".local" / "share" / "fonts"]
    return [d for d in dirs if d.is_dir()]


def find_font(prefer: str | list[str] | None = None, extra_dirs: list[Path] | None = None) -> Path:
    """返回中文字体文件路径：SCRIPTCUT_FONT > prefer（文件名或路径）> 常见字体 > fc-match。"""
    wanted = [env("SCRIPTCUT_FONT")] if env("SCRIPTCUT_FONT") else []
    if isinstance(prefer, str):
        wanted.append(prefer)
    elif prefer:
        wanted += list(prefer)
    for w in wanted:
        if w and Path(w).is_file():
            return Path(w)
    dirs = _font_dirs(extra_dirs or [])
    names = [w for w in wanted if w] + FONT_CANDIDATES
    for name in names:
        for d in dirs:
            p = d / name
            if p.is_file():
                return p
    for name in names:  # 递归（Linux 字体通常在子目录里）
        for d in dirs:
            hits = list(d.rglob(name))
            if hits:
                return hits[0]
    if which("fc-match"):
        out = subprocess.run(["fc-match", "-f", "%{file}", "sans:lang=zh:weight=bold"], capture_output=True, text=True)
        if out.returncode == 0 and Path(out.stdout.strip()).is_file():
            return Path(out.stdout.strip())
    raise FileNotFoundError("找不到中文字体：请安装思源黑体 / Noto Sans CJK，或在 .env 设置 SCRIPTCUT_FONT=字体文件路径")
