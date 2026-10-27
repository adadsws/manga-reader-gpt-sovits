"""按固定 ModelScope 清单下载并安装鸣潮中文 GPT-SoVITS 角色模型。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import traceback
import urllib.parse
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/mingchao-zh-model.lock.json"
CACHE = ROOT / "~temp/downloads/mingchao-zh"
STAGING = ROOT / "~temp/model-imports/mingchao-zh"
TARGET = ROOT / "models/gpt-sovits/鸣潮-中文/v4"
PROGRESS = CACHE / "progress.jsonl"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def entries() -> tuple[dict, list[dict]]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    files = lock["files"]
    if len(files) != 57 or len({item["path"] for item in files}) != len(files):
        raise ValueError("鸣潮中文模型锁文件不完整或路径重复")
    for item in files:
        path = PurePosixPath(item["path"])
        if (
            len(path.parts) != 3
            or path.parts[:2] != ("鸣潮", "中文")
            or not path.name.endswith("_ZH.zip")
            or item["bytes"] <= 0
            or len(item["sha256"]) != 64
        ):
            raise ValueError(f"模型锁文件条目无效：{item['path']}")
    return lock, files


def role_name(item: dict) -> str:
    return PurePosixPath(item["path"]).name.removesuffix(".zip")


def verify_file(path: Path, size: int, digest: str) -> None:
    if not path.is_file() or path.stat().st_size != size or sha256(path) != digest:
        raise RuntimeError(f"文件大小或 SHA-256 不符：{path}")


def download(lock: dict, item: dict) -> Path:
    name = PurePosixPath(item["path"]).name
    archive = CACHE / name
    if archive.exists():
        verify_file(archive, item["bytes"], item["sha256"])
        return archive
    CACHE.mkdir(parents=True, exist_ok=True)
    partial = CACHE / (name + ".download")
    url = (
        f"https://www.modelscope.cn/models/{lock['repository']}/resolve/"
        f"{lock['revision']}/{urllib.parse.quote(item['path'])}"
    )
    offset = partial.stat().st_size if partial.exists() else 0
    if offset == item["bytes"]:
        verify_file(partial, item["bytes"], item["sha256"])
        partial.rename(archive)
        return archive
    if offset > item["bytes"]:
        raise RuntimeError(f"未完成缓存长度异常，请检查：{partial}")
    headers = {"User-Agent": "manga-reader"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status not in ((200, 206) if offset else (200,)):
            raise RuntimeError(f"服务器未按预期响应下载：{name}，HTTP {response.status}")
        if offset and response.status == 206:
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {offset}-"):
                raise RuntimeError(f"服务器返回错误续传范围：{name}，{content_range}")
        # 服务端不支持 Range 时另存完整副本，保留原未完成文件供检查。
        output_path = (
            CACHE / f"{name}.{uuid.uuid4().hex}.restart.download"
            if offset and response.status == 200
            else partial
        )
        with output_path.open("ab" if offset and response.status == 206 else "wb") as output:
            shutil.copyfileobj(response, output, length=64 * 1024)
    verify_file(output_path, item["bytes"], item["sha256"])
    output_path.rename(archive)
    return archive


def record(role: str, status: str, **details: object) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    with PROGRESS.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"role": role, "status": status, **details}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def last_statuses() -> dict[str, str]:
    statuses = {}
    if PROGRESS.exists():
        for line in PROGRESS.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            statuses[event["role"]] = event["status"]
    return statuses


def check_source(lock: dict, files: list[dict]) -> None:
    # 长批处理开始前核对共享下载源，避免同一故障在全部角色上重复等待。
    query = urllib.parse.urlencode(
        {"Revision": lock["revision"], "Recursive": "true", "Root": "鸣潮/中文"}
    )
    url = f"https://modelscope.cn/api/v1/models/{lock['repository']}/repo/files?{query}"
    with urllib.request.urlopen(url, timeout=60) as response:
        listing = json.load(response)
    current = {item["Path"]: (item["Size"], item["Sha256"]) for item in listing["Data"]["Files"]}
    expected = {item["path"]: (item["bytes"], item["sha256"]) for item in files}
    if any(current.get(path) != values for path, values in expected.items()):
        raise RuntimeError("固定 ModelScope 文件清单与锁文件不一致")


def inspect_zip(bundle: zipfile.ZipFile, role: str) -> list[tuple[zipfile.ZipInfo, Path]]:
    prefix = ("v4", role)
    members = []
    seen = set()
    for member in bundle.infolist():
        # 上游 ZIP 未设置 UTF-8 标志，文件名实际是 GBK 字节。
        name = member.filename.encode("cp437").decode("gbk").replace("\\", "/")
        path = PurePosixPath(name)
        if member.is_dir() and path.parts == ("v4",):
            continue
        if path.is_absolute() or ".." in path.parts or path.parts[:2] != prefix:
            raise RuntimeError(f"ZIP 包含角色目录外的路径：{member.filename}")
        relative = Path(*path.parts[2:])
        if not relative.parts:
            continue
        if str(relative).casefold() in seen:
            raise RuntimeError(f"ZIP 包含重复路径：{member.filename}")
        seen.add(str(relative).casefold())
        mode = (member.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise RuntimeError(f"ZIP 包含符号链接：{member.filename}")
        members.append((member, relative))
    suffixes = {relative.suffix.lower() for member, relative in members if not member.is_dir()}
    missing = {".ckpt", ".pth", ".wav"} - suffixes
    if missing:
        raise RuntimeError(f"{role} 缺少运行文件：{sorted(missing)}")
    return members


def verify_installed(destination: Path, item: dict) -> None:
    manifest = destination / ".source.json"
    if not manifest.is_file():
        raise RuntimeError(f"已存在但缺少来源清单：{destination}")
    source = json.loads(manifest.read_text(encoding="utf-8"))
    if source.get("archive_sha256") != item["sha256"]:
        raise RuntimeError(f"已存在目录来自不同模型包：{destination}")
    for record in source["files"]:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"来源清单含不安全路径：{destination}")
        verify_file(destination / relative, record["bytes"], record["sha256"])


def install(archive: Path, item: dict) -> None:
    role = role_name(item)
    destination = TARGET / role
    if destination.exists():
        verify_installed(destination, item)
        return
    STAGING.mkdir(parents=True, exist_ok=True)
    prepared = STAGING / f"{role}-{uuid.uuid4().hex}"
    prepared.mkdir()
    records = []
    with zipfile.ZipFile(archive) as bundle:
        for member, relative in inspect_zip(bundle, role):
            output = prepared / relative
            if member.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, output.open("xb") as target:
                shutil.copyfileobj(source, target)
            records.append(
                {"path": relative.as_posix(), "bytes": output.stat().st_size, "sha256": sha256(output)}
            )
    (prepared / ".source.json").write_text(
        json.dumps({"archive_path": item["path"], "archive_sha256": item["sha256"], "files": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    verify_installed(prepared, item)
    TARGET.mkdir(parents=True, exist_ok=True)
    prepared.rename(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-existing", action="store_true", help="只校验已安装的全部角色")
    parser.add_argument("--only", help="只处理一个角色名，例如 吟霖_ZH")
    parser.add_argument("--retry-errors", action="store_true", help="重试上次标记失败的角色")
    args = parser.parse_args()
    lock, files = entries()
    if args.only:
        files = [item for item in files if role_name(item) == args.only]
        if not files:
            parser.error(f"锁文件中没有角色：{args.only}")
    if args.verify_existing:
        for item in files:
            verify_installed(TARGET / role_name(item), item)
        print(f"已校验 {len(files)} 个已安装角色", flush=True)
        return
    check_source(lock, files)
    statuses = last_statuses()
    completed = skipped = failed = 0
    for number, item in enumerate(files, 1):
        role = role_name(item)
        destination = TARGET / role
        if destination.exists():
            try:
                verify_installed(destination, item)
            except Exception as error:
                record(role, "error", stage="verify-existing", error=str(error), traceback=traceback.format_exc())
                failed += 1
                print(f"[{number}/{len(files)}] 已安装内容校验失败 {role}：{error}", flush=True)
            else:
                completed += 1
                print(f"[{number}/{len(files)}] 已校验并跳过 {role}", flush=True)
            continue
        if statuses.get(role) == "error" and not args.retry_errors:
            skipped += 1
            print(f"[{number}/{len(files)}] 跳过上次失败项 {role}；用 --retry-errors 重试", flush=True)
            continue
        stage = "download"
        try:
            record(role, "started", stage=stage)
            archive = download(lock, item)
            stage = "install"
            install(archive, item)
            record(role, "complete", archive_sha256=item["sha256"])
            completed += 1
            print(f"[{number}/{len(files)}] 已安装并校验 {role}", flush=True)
        except Exception as error:
            record(role, "error", stage=stage, error=str(error), traceback=traceback.format_exc())
            failed += 1
            print(f"[{number}/{len(files)}] 失败并跳过 {role}：{error}", flush=True)
            if stage == "download" and isinstance(error, urllib.error.URLError):
                check_source(lock, files)
    print(f"完成 {completed}/{len(files)}，跳过错误 {skipped}，本轮失败 {failed}；进度：{PROGRESS}", flush=True)
    if completed != len(files):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
