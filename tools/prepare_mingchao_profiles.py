"""保留上游模型文件，在鸣潮角色根目录补充默认参考音频与本机头像。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models/gpt-sovits/鸣潮-中文/v4"
LOCK = ROOT / "config/mingchao-avatar.lock.json"
CACHE = ROOT / "~temp/downloads/mingchao-avatars"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(path: Path, record: dict) -> None:
    if not path.is_file() or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
        raise RuntimeError(f"大小或 SHA-256 不符，未覆盖：{path}")


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise RuntimeError(f"头像不是 PNG：{path}")
    return struct.unpack(">II", header[16:24])


def default_reference(role: Path) -> tuple[Path, dict]:
    source = json.loads((role / ".source.json").read_text(encoding="utf-8"))
    choices = [item for item in source["files"] if Path(item["path"]).suffix.lower() == ".wav"]
    if not choices:
        raise RuntimeError(f"模型缺少参考音频：{role.name}")
    choices.sort(key=lambda item: (not Path(item["path"]).name.startswith("【默认】"), item["path"]))
    item = choices[0]
    path = role / item["path"]
    verify(path, item)
    if path.parent == role:
        return path, item
    return role / path.name, item


def prepare_reference(role: Path, verify_only: bool) -> None:
    destination, record = default_reference(role)
    if destination.exists():
        verify(destination, record)
    elif verify_only:
        raise RuntimeError(f"角色根目录缺少默认参考音频：{destination}")
    else:
        source = role / record["path"]
        shutil.copy2(source, destination)
        verify(destination, record)


def prepare_avatar(role: Path, record: dict, verify_only: bool) -> None:
    destination = role / "角色头像.png"
    if destination.exists():
        verify(destination, record)
    elif verify_only:
        raise RuntimeError(f"角色目录缺少头像：{destination}")
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        cached = CACHE / f"{role.name.removesuffix('_ZH')}.png"
        if cached.exists():
            verify(cached, record)
        else:
            request = urllib.request.Request(record["url"], headers={"User-Agent": "manga-reader-avatar/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read(record["bytes"] + 1)
            if len(body) != record["bytes"] or hashlib.sha256(body).hexdigest() != record["sha256"]:
                raise RuntimeError(f"下载的头像与固定校验值不符：{role.name}")
            cached.write_bytes(body)
        if png_size(cached) != tuple(record["size"]):
            raise RuntimeError(f"头像尺寸不符：{role.name}")
        shutil.copy2(cached, destination)
        verify(destination, record)
    if png_size(destination) != tuple(record["size"]):
        raise RuntimeError(f"头像尺寸不符：{role.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="只准备一个角色，例如 菲比_ZH")
    parser.add_argument("--verify-existing", action="store_true", help="只校验角色根目录，不下载或复制")
    args = parser.parse_args()
    avatars = json.loads(LOCK.read_text(encoding="utf-8"))["avatars"]
    roles = sorted((p for p in MODELS.iterdir() if p.is_dir() and p.name.lower() not in ("archive", "~archive")), key=lambda p: p.name)
    if args.only:
        roles = [role for role in roles if role.name == args.only]
        if not roles:
            parser.error(f"角色目录不存在：{args.only}")
    with_avatar = 0
    without_avatar = []
    for role in roles:
        prepare_reference(role, args.verify_existing)
        record = avatars.get(role.name.removesuffix("_ZH"))
        if record is None:
            without_avatar.append(role.name)
        else:
            prepare_avatar(role, record, args.verify_existing)
            with_avatar += 1
        print(f"已{'校验' if args.verify_existing else '准备'} {role.name}", flush=True)
    print(f"角色 {len(roles)} 个；有来源头像 {with_avatar} 个；无可靠头像 {len(without_avatar)} 个", flush=True)
    if without_avatar:
        print("暂用应用默认图标：" + "、".join(without_avatar), flush=True)


if __name__ == "__main__":
    main()
