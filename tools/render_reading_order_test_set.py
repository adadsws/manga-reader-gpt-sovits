"""把已记录的两批阅读顺序样本渲染为可逐页对照的 Markdown 表格。"""

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "secrets/manga/ocr-visual-ground-truth.json"
EXTRA = ROOT / "secrets/manga/reading-order-extra.json"
DEFAULT_OUTPUT = ROOT / "~outputs-final/reading-order-test-set.md"


def safe_cell(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("|", "&#124;")
            .replace("\r", " ").replace("\n", " "))


def number(index):
    return chr(0x2460 + index) if index < 20 else str(index+1)


def render(output):
    original = json.loads(BASE.read_text(encoding="utf-8"))
    additional = json.loads(EXTRA.read_text(encoding="utf-8"))
    cases = []
    for page in original["pages"]:
        cases.append({"collection": original["source_collection"],
                      "image": page["image"], "sha256": page["sha256"],
                      "units": page["text_units"], "status": "用户已修正"})
    for page in additional["pages"]:
        units = [" ／ ".join(page["fixed_ocr_rows"][row_id]["original"]
                            for row_id in group) for group in page["reference_groups"]]
        cases.append({"collection": page["source_collection"],
                      "image": page["image"], "sha256": page["sha256"],
                      "units": units, "status": "代理目视顺序；文字为 OCR 草稿，待复核"})
    collections = sorted({case["collection"] for case in cases})
    order = {collection: index+1 for index, collection in enumerate(collections)}
    cases.sort(key=lambda case: (order[case["collection"]], case["image"]))
    lines = ["# 漫画整段阅读顺序测试集", "",
             f"共 {len(cases)} 页，{sum(len(case['units']) for case in cases)} 段。",
             "原有 12 页的文字与顺序经用户修正；新增 10 页仅目视标注整段顺序，"
             "文字列是固定 OCR 草稿，不能作为识字正确答案。", "",
             "| 册 | 页与原图 | 阅读顺序 | 状态 |", "|---|---|---|---|"]
    for case in cases:
        path = ROOT / "secrets/manga" / case["collection"] / case["image"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != case["sha256"]:
            raise ValueError(f"原图哈希不匹配：{path}")
        image = f"![{order[case['collection']]}-{case['image']}](<{path.as_posix()}>)"
        open_link = f"[打开原图](<{path.as_posix()}>)"
        ordered = "<br>".join(f"{number(i)} {safe_cell(unit)}"
                               for i, unit in enumerate(case["units"]))
        lines.append(f"| {order[case['collection']]} | {safe_cell(case['image'])}<br>{image}<br>{open_link} | {ordered} | {case['status']} |")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(output)
    print(f"pages={len(cases)} segments={sum(len(case['units']) for case in cases)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    render(parser.parse_args().output)
