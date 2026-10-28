# 下载并校验整页识别模型；上游固定到 RapidOCR 3.9.2 的模型发布路径。
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
NAME = "PP-OCRv6_rec_medium.onnx"
URL = (
    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/"
    f"onnx/PP-OCRv6/rec/{NAME}"
)


def expected_model():
    lock = json.loads((ROOT / "config/ocr-models.lock.json").read_text(encoding="utf-8"))
    matches = [row for row in lock if row["file"] == NAME]
    if len(matches) != 1:
        raise ValueError(f"OCR model lock must contain exactly one {NAME}")
    return matches[0]


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def prepare():
    expected = expected_model()
    destination = ROOT / "~temp/ocrdeps/rapidocr/models" / NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.stat().st_size != expected["bytes"] or digest(destination) != expected["sha256"]:
            raise ValueError(f"Existing OCR model differs from lock: {destination}")
        print(f"OCR model verified: {destination}")
        return destination
    staged = destination.with_name(destination.name + ".download-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    with urlopen(URL, timeout=60) as source, staged.open("wb") as output:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            output.write(chunk)
    if staged.stat().st_size != expected["bytes"] or digest(staged) != expected["sha256"]:
        raise ValueError(f"Downloaded OCR model differs from lock: {staged}")
    staged.rename(destination)
    print(f"OCR model downloaded and verified: {destination}")
    return destination


if __name__ == "__main__":
    prepare()
