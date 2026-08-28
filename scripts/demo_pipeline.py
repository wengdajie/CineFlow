"""真实文件的端到端演示：模拟下载器产出文件 -> 整理入库 -> 缺集收敛。

不触网，但使用真实磁盘 IO 与真实 SQLite。
"""
import shutil
import sys
from pathlib import Path

# 允许从项目根目录外直接运行本脚本
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.config import settings  # noqa: E402
from app.core.meta import parse  # noqa: E402
from app.core.organizer import transfer_directory  # noqa: E402
from app.services import library as library_service  # noqa: E402
from app.utils.strings import format_size  # noqa: E402

DEMO = Path("data/_e2e_demo")
if DEMO.exists():
    shutil.rmtree(DEMO)
incoming = DEMO / "incoming"
lib = DEMO / "library"
incoming.mkdir(parents=True)
lib.mkdir(parents=True)

# 造出一批贴近真实的下载产物
FILES = [
    "凡人修仙传.S02E103.2160p.WEB-DL.H265.DDP-CF.mkv",
    "凡人修仙传.S02E104.2160p.WEB-DL.H265.DDP-CF.mkv",
    "凡人修仙传.S02E104.2160p.WEB-DL.H265.DDP-CF.srt",
    "The.Last.of.Us.S02E01.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv",
    "Oppenheimer.2023.2160p.UHD.BluRay.REMUX.DV.TrueHD.Atmos-FraMeSToR.mkv",
    "sample.mkv",  # 体积过小，应被跳过
]
# 正片写 3 MB，sample 写 2 KB；并把入库门槛降到 1 MB 以便快速演示
settings.MIN_FILE_SIZE_MB = 1
for name in FILES:
    path = incoming / name
    path.write_bytes(b"0" * (2 * 1024 if name == "sample.mkv" else 3 * 1024 * 1024))

print("=" * 72)
print("1) 下载产物")
print("=" * 72)
for path in sorted(incoming.iterdir()):
    print(f"   {path.name}  ({format_size(path.stat().st_size)})")

print("\n" + "=" * 72)
print("2) 解析结果")
print("=" * 72)
for name in FILES:
    if not name.endswith((".mkv", ".srt")):
        continue
    info = parse(name, is_file=True)
    print(f"   {name[:52]:52} -> 标题={info.title!r} S={info.season} E={info.episode_start} {info.resolution}")

print("\n" + "=" * 72)
print("3) 硬链接整理入库")
print("=" * 72)
original_lib = settings.LIBRARY_DIR
settings.LIBRARY_DIR = lib
results = transfer_directory(incoming)
for item in results:
    status = "OK " if item.success else "SKIP"
    target = Path(item.target).relative_to(lib) if item.target else item.message
    print(f"   {status} {Path(item.source).name[:46]:46} -> {target}")

print("\n" + "=" * 72)
print("4) 最终媒体库结构")
print("=" * 72)
for path in sorted(lib.rglob("*")):
    depth = len(path.relative_to(lib).parts) - 1
    prefix = "   " + "    " * depth
    if path.is_dir():
        print(f"{prefix}📁 {path.name}/")
    else:
        stat = path.stat()
        link = f"  [硬链接 nlink={stat.st_nlink}]" if stat.st_nlink > 1 else ""
        print(f"{prefix}🎬 {path.name}{link}")

print("\n" + "=" * 72)
print("5) 扫描入库索引与缺集判断")
print("=" * 72)
stats = library_service.scan_library(lib)
print(f"   扫描：{stats}")
episodes = library_service.existing_episodes("凡人修仙传", 2)
print(f"   凡人修仙传 S02 已有集：{sorted(episodes)}")
print(f"   若订阅到 106 集，则缺：{[e for e in range(103, 107) if e not in episodes]}")
print(f"   电影是否已入库（Oppenheimer）：{library_service.has_library_file('Oppenheimer', 'movie')}")

settings.LIBRARY_DIR = original_lib
shutil.rmtree(DEMO)
print("\n演示目录已清理。")
