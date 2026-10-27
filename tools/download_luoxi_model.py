"""校验并安装用户从来源论坛取得的洛茜模型包，保留上游文件原样。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/luoxi-model.lock.json"
TARGET = ROOT / "models/gpt-sovits/终末地-中文"
CACHE = ROOT / "~temp/downloads/luoxi"
STAGING = ROOT / "~temp/model-imports"


def digest_stream(stream) -> str:
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(block)
    return result.hexdigest()


def load_entries(lock_path: Path = LOCK) -> list[dict]:
    entries = json.loads(lock_path.read_text(encoding="utf-8"))["files"]
    if not entries or len({item["path"] for item in entries}) != len(entries):
        raise ValueError("模型锁文件为空或含重复路径")
    for item in entries:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts or item["bytes"] <= 0:
            raise ValueError("模型锁文件包含不安全的路径或长度")
    return entries


def verify_existing(target: Path, entries: list[dict]) -> None:
    for item in entries:
        path = target / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            raise RuntimeError(f"模型文件缺失或长度不符：{item['path']}")
        with path.open("rb") as stream:
            if digest_stream(stream) != item["sha256"]:
                raise RuntimeError(f"模型文件哈希不符：{item['path']}")


def download(url: str, cache: Path = CACHE) -> Path:
    # 只缓存用户给出的模型包直链；论坛页面不解析成隐含的下载源。
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("只接受 HTTPS 模型包直链；论坛帖子页面不能作为模型包")
    cache.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20] + ".zip"
    archive = cache / name
    if archive.exists():
        return archive
    partial = cache / (name + ".download")
    if partial.exists():
        raise FileExistsError(f"上次下载的未完成文件仍在：{partial}")
    request = urllib.request.Request(url, headers={"User-Agent": "manga-reader"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("xb") as output:
        shutil.copyfileobj(response, output)
    partial.rename(archive)
    return archive


def find_payloads(archives: list[Path], entries: list[dict]) -> dict[str, tuple[Path, str]]:
    # 按内容而非 ZIP 路径定位文件，避免来源包层级变化和越界路径。
    wanted = {(item["bytes"], item["sha256"]) for item in entries}
    matches: dict[str, tuple[Path, str]] = {}
    sizes = {size for size, _ in wanted}
    for archive in archives:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir() or member.file_size not in sizes:
                    continue
                with bundle.open(member) as stream:
                    fingerprint = (member.file_size, digest_stream(stream))
                if fingerprint in wanted:
                    matches[fingerprint[1]] = (archive, member.filename)
    missing = [item["path"] for item in entries if item["sha256"] not in matches]
    if missing:
        raise RuntimeError("下载包缺少或无法校验这些运行文件：" + ", ".join(missing))
    return matches


def install_from_archives(
    archives: list[Path], target: Path, entries: list[dict], staging: Path = STAGING
) -> None:
    # 全量核对后先写入独立暂存目录，再一次性移入不存在的目标目录。
    if target.exists():
        raise FileExistsError(f"目标目录已存在，不覆盖本机模型：{target}")
    matches = find_payloads(archives, entries)
    staging.mkdir(parents=True, exist_ok=True)
    prepared = staging / ("luoxi-" + uuid.uuid4().hex)
    prepared.mkdir()
    for item in entries:
        archive, member = matches[item["sha256"]]
        output = prepared / item["path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle, bundle.open(member) as source, output.open(
            "xb"
        ) as destination:
            shutil.copyfileobj(source, destination)
    verify_existing(prepared, entries)
    target.parent.mkdir(parents=True, exist_ok=True)
    prepared.rename(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, action="append", default=[])
    parser.add_argument("--url", action="append", default=[], help="模型 ZIP 的 HTTPS 直链，可重复")
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    entries = load_entries()
    if args.verify_existing:
        verify_existing(TARGET, entries)
        print(f"本机洛茜运行文件校验通过：{len(entries)} 个")
        return
    archives = args.archive + [download(url) for url in args.url]
    if not archives:
        parser.error("提供 --archive 下载包或 --url 模型 ZIP 直链")
    install_from_archives(archives, TARGET, entries)
    print(f"洛茜运行文件安装并校验通过：{len(entries)} 个")


if __name__ == "__main__":
    main()
