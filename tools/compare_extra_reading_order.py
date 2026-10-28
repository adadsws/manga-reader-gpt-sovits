"""比较新增两本漫画的整段顺序样本；原 12 页测试保持独立。

样本只含人工目视标注的 OCR 行分组与顺序，不评估文字正确率。
"""

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from compare_reading_order import (
    baseline_partition_order, minimum_moves, panel_order, recursive_cut, same_level_rows,
    spatial_graph, top_right,
)
from server.layout import MangaLayout


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "secrets/manga/reading-order-extra.json"
DEFAULT_OUTPUT = ROOT / "~outputs-intermediate/evidence/extra-reading-order-results.json"


def row_digest(rows):
    content = [{key: row[key] for key in ("original", "box")} for row in rows]
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compare(output):
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    layout = MangaLayout("cuda")
    methods = {
        "baseline_v2_xy": baseline_partition_order,
        "cut_horizontal_first": lambda rows: recursive_cut(rows, "horizontal_first"),
        "cut_vertical_first": lambda rows: recursive_cut(rows, "vertical_first"),
        "cut_balanced_gap": lambda rows: recursive_cut(rows, "balanced_gap"),
        "same_level_rows": same_level_rows,
        "spatial_graph": spatial_graph,
        "top_right_distance": top_right,
    }
    result = {"fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
              "annotation_status": fixture["annotation_status"],
              "metric": "最少整段取出并插入次数，N-LIS；未识别段不在本测试范围。",
              "pages": {}, "totals": {}}
    for case in fixture["pages"]:
        name = case["image"]
        collection = case["source_collection"]
        key = f"{collection}/{name}"
        path = FIXTURE.parent/collection/name
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != case["sha256"]:
            raise ValueError(f"原图哈希不匹配：{key}")
        fixed_rows = case["fixed_ocr_rows"]
        if row_digest(fixed_rows) != case["fixed_ocr_rows_sha256"]:
            raise ValueError(f"固定 OCR 行已变化，需重新目视对齐：{key}")
        rows = [dict(row, row_id=i) for i, row in enumerate(fixed_rows)]
        mapping = [None]*len(rows)
        for segment_id, row_ids in enumerate(case["reference_groups"]):
            for row_id in row_ids:
                if mapping[row_id] is not None:
                    raise ValueError(f"OCR 行重复标注：{key} {row_id}")
                mapping[row_id] = segment_id
        if [i for i, value in enumerate(mapping) if value is None] != case["ignored_ocr_row_ids"]:
            raise ValueError(f"未标注 OCR 行与排除列表不一致：{key}")
        frame = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        panels = layout.panels(frame)
        scored = {}
        for method, strategy in methods.items():
            order = panel_order(rows, panels, strategy)
            if sorted(order) != list(range(len(rows))):
                raise AssertionError(f"排序丢行或重复：{key} {method}")
            seen, indexes = set(), []
            for row_id in order:
                segment_id = mapping[row_id]
                if segment_id is not None and segment_id not in seen:
                    indexes.append(segment_id)
                    seen.add(segment_id)
            if len(indexes) != len(case["reference_groups"]):
                raise AssertionError(f"排序丢失整段：{key} {method}")
            scored[method] = {"moves": minimum_moves(indexes),
                              "segment_order": indexes, "ocr_row_order": order}
        result["pages"][key] = {"collection": collection, "image": name,
                                "segments": len(case["reference_groups"]),
                                "panel_count": len(panels), "methods": scored}
        print(name, "volume", 2 if "サッキュ2" in collection else 1,
              {method: scored[method]["moves"] for method in methods}, flush=True)
    result["totals"] = {method: sum(page["methods"][method]["moves"]
                                     for page in result["pages"].values()) for method in methods}
    result["totals"]["pages"] = len(result["pages"])
    result["totals"]["segments"] = sum(page["segments"] for page in result["pages"].values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("合计", result["totals"], flush=True)
    print(output, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT)
    compare(parser.parse_args().output_json)
