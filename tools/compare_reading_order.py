"""在固定 OCR 文字框与 Magi v2 分镜上比较整段话的阅读顺序。

不更改正式应用排序。人工对齐仅用于本测试集；OCR 缺失和额外行独立记录。
运行前可用 tools/compare_ocr_models.py production 生成固定 OCR 报告。
"""

import argparse
import hashlib
import heapq
import json
from bisect import bisect_left
from functools import cmp_to_key
from pathlib import Path

import cv2
import numpy as np

from server.core import compact_reading_order, map_text_to_panels, partition_reading_order
from server.layout import MangaLayout
from server.ocr import Region, sort


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "secrets/manga/ocr-visual-ground-truth.json"
DEFAULT_OCR = ROOT / "~outputs-intermediate/evidence/ocr-models-20261027/visual12-production.json"
DEFAULT_OUTPUT = ROOT / "~outputs-intermediate/evidence/reading-order-algorithms.json"
FIXED_OCR_ROWS_SHA256 = "398cdc4ce54deb2cdbad4ccd979ad03c04dbc022712c2c64ac5ec2228bdcb614"

# 固定 OCR 报告中各行对应的人工整段编号。重复值表示一段被拆行，元组表示两段被合行。
# None 表示不属于人工标注正文；本表必须随 OCR 报告的文字框核对后使用。
ROW_TO_SEGMENT = {
    "0003.jpg": [0, 1, 3, 2, 4, 5, 6, 7],
    "0004.jpg": [0, 1, 2, 3, 4, 4, 5, 6, 7, 8, 10],
    "0005.jpg": [1, 2],
    "0006.jpg": [0, 1, 2, 3, 4, 4, 5, 6, 7, 8],
    "0007.jpg": [0, 2, 3],
    "0008.jpg": [0, 1, 2, 3],
    "0009.jpg": list(range(14)),
    "0011.jpg": [0, 1, 2, 5, 3, 4, 5, 6, 7],
    "0012.jpg": [2, 0, 1, 3, None, 4, 5, None, 7, 6, 8, 9, 9, 10],
    "0013.jpg": [0, 1, 2, 3, 4],
    "0025.jpg": [0, 1, 2, 2, 3, 4, 5, (6, 7), 8, 9, 10],
    "0041.jpg": [0, 1, 2, 3, 5, 6, 7, 8],
}


def segment_order(row_ids, mapping):
    seen, result = set(), []
    for row_id in row_ids:
        target = mapping[row_id]
        indexes = target if isinstance(target, tuple) else (() if target is None else (target,))
        for index in indexes:
            if index not in seen:
                seen.add(index)
                result.append(index)
    return result


def minimum_moves(indexes):
    """一次移位取出一整段并插入任意位置，最少次数等于 N-LIS。"""
    tails = []
    for index in indexes:
        at = bisect_left(tails, index)
        if at == len(tails):
            tails.append(index)
        else:
            tails[at] = index
    return len(indexes) - len(tails)


def top_right(rows):
    if not rows:
        return []
    right = max(r["box"][2] for r in rows)
    top = min(r["box"][1] for r in rows)
    return [r["row_id"] for r in sorted(rows, key=lambda r: (
        (right-r["box"][2])**2 + (r["box"][1]-top)**2, r["row_id"]))]


def production_distance(rows):
    order = compact_reading_order(rows)
    if order is None:
        return [r.data["row_id"] for r in sort._simple_sort([Region(row) for row in rows], True)]
    return [rows[i]["row_id"] for i in order]


def baseline_partition_order(rows):
    """正式服务与基准共用分区规则，返回固定 OCR 行编号。"""
    def fallback(part):
        by_id = {row["row_id"]: row for row in part}
        return [by_id[row_id] for row_id in production_distance(part)]
    return [row["row_id"] for row in partition_reading_order(rows, fallback)]


def split_candidates(rows):
    boxes = [row["box"] for row in rows]
    typical = float(np.median([min(b[2]-b[0], b[3]-b[1]) for b in boxes]))
    for axis in (0, 1):
        edges = sorted({b[axis] for b in boxes} | {b[axis+2] for b in boxes})
        for lo, hi in zip(edges, edges[1:]):
            if hi-lo < typical * 0.8:
                continue
            pivot = (lo+hi)/2
            first = [r for r in rows if r["box"][axis+2] <= pivot]
            second = [r for r in rows if r["box"][axis] >= pivot]
            if first and second and len(first)+len(second) == len(rows):
                yield axis, (hi-lo)/max(typical, 1), first, second


def recursive_cut(rows, priority, two_item_fallback=False):
    """按空白递归分区；横向先上后下，纵向先右后左。

    横切优先取自 manga109/panel-order-estimator 的层级排序思路；
    来源：https://github.com/manga109/panel-order-estimator
    """
    if len(rows) < 2:
        return [r["row_id"] for r in rows]
    if two_item_fallback and len(rows) == 2:
        return production_distance(rows)
    candidates = list(split_candidates(rows))
    if not candidates:
        return production_distance(rows)
    if priority == "largest_gap":
        key = lambda c: (c[1], c[0])
    elif priority == "horizontal_first":
        key = lambda c: (c[0] == 1, c[1])
    elif priority == "vertical_first":
        key = lambda c: (c[0] == 0, c[1])
    elif priority == "balanced_gap":
        key = lambda c: (c[1] * min(len(c[2]), len(c[3])) / len(rows), c[1])
    else:
        raise ValueError(priority)
    axis, _, first, second = max(candidates, key=key)
    left = recursive_cut(first, priority, two_item_fallback)
    right = recursive_cut(second, priority, two_item_fallback)
    return left+right if axis == 1 else right+left


def same_level_rows(rows):
    """按高度重叠/接近判同层，再右到左；参考 ICCC 2021 的漫画方法。

    来源：https://computationalcreativity.net/iccc21/wp-content/uploads/2021/09/ICCC_2021_paper_52.pdf
    """
    def compare(a, b):
        x, y = a["box"], b["box"]
        if a["row_id"] == b["row_id"]:
            return 0
        height = min(x[3]-x[1], y[3]-y[1])
        same = ((x[1] <= y[1] and x[3] >= y[3]) or
                (y[1] <= x[1] and y[3] >= x[3]) or
                abs(x[1]-y[1]) < height/2 or abs(x[3]-y[3]) < height/2)
        if same:
            return -1 if (x[2], -x[1], -a["row_id"]) > (y[2], -y[1], -b["row_id"]) else 1
        return -1 if (x[1], -x[2], a["row_id"]) < (y[1], -y[2], b["row_id"]) else 1
    return [row["row_id"] for row in sorted(rows, key=cmp_to_key(compare))]


def spatial_graph(rows):
    """强空间关系建先后图，不可比项按右上角距离决胜。

    图排序思路参考 Magi 项目说明；关系规则为本地实验性实现：
    https://www.robots.ox.ac.uk/~vgg/research/magi/
    """
    count = len(rows)
    if count < 2:
        return [row["row_id"] for row in rows]
    boxes = [row["box"] for row in rows]
    edges = {i: set() for i in range(count)}
    indegree = [0]*count
    for i in range(count):
        a = boxes[i]
        for j in range(i+1, count):
            b = boxes[j]
            x_overlap = min(a[2], b[2])-max(a[0], b[0])
            y_overlap = min(a[3], b[3])-max(a[1], b[1])
            edge = None
            if x_overlap > 0 and a[3] <= b[1]: edge = (i, j)
            elif x_overlap > 0 and b[3] <= a[1]: edge = (j, i)
            elif y_overlap > 0 and a[0] >= b[2]: edge = (i, j)
            elif y_overlap > 0 and b[0] >= a[2]: edge = (j, i)
            if edge:
                edges[edge[0]].add(edge[1])
                indegree[edge[1]] += 1
    right = max(b[2] for b in boxes)
    top = min(b[1] for b in boxes)
    priority = lambda i: ((right-boxes[i][2])**2+(boxes[i][1]-top)**2, rows[i]["row_id"], i)
    ready = [priority(i) for i in range(count) if indegree[i] == 0]
    heapq.heapify(ready)
    result = []
    while ready:
        _, _, i = heapq.heappop(ready)
        result.append(rows[i]["row_id"])
        for j in edges[i]:
            indegree[j] -= 1
            if indegree[j] == 0:
                heapq.heappush(ready, priority(j))
    if len(result) < count:
        result.extend(rows[i]["row_id"] for i in sorted(
            (i for i in range(count) if indegree[i] > 0), key=priority))
    return result


def panel_order(rows, panels, strategy):
    if not panels:
        return strategy(rows)
    assigned = map_text_to_panels([r["box"] for r in rows], panels)
    groups = {}
    for row, panel in zip(rows, assigned):
        groups.setdefault(panel, []).append(row)
    return [index for panel in sorted(groups) for index in strategy(groups[panel])]


def read_inputs(ocr_path, panel_path):
    raw = FIXTURE.read_bytes()
    fixture = json.loads(raw)
    fixture_hash = hashlib.sha256(raw).hexdigest()
    if not fixture["annotation_policy"]["text_units_order_verified"]:
        raise ValueError("人工整段顺序尚未核对")
    ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
    if ocr["fixture_sha256"] != fixture_hash:
        raise ValueError("固定 OCR 报告与测试集哈希不一致；请重新运行 production OCR")
    fixed_rows = {name: [{key: row[key] for key in ("original", "box")}
                         for row in page["rows"]] for name, page in ocr["pages"].items()}
    rows_hash = hashlib.sha256(json.dumps(fixed_rows, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
    if rows_hash != FIXED_OCR_ROWS_SHA256:
        raise ValueError("OCR 行或坐标已变化；需重新人工核对行与整段的对应表")
    panel_cache = json.loads(panel_path.read_text(encoding="utf-8")) if panel_path else None
    if panel_cache is not None:
        if panel_cache["fixture_sha256"] != fixture_hash or panel_cache["model"] != "v2":
            raise ValueError("分镜缓存不是同一测试集的 Magi v2 结果")
    return fixture, fixture_hash, ocr, panel_cache


def compare(ocr_path, panel_path, output_path):
    fixture, fixture_hash, ocr, panel_cache = read_inputs(ocr_path, panel_path)
    layout = None if panel_cache else MangaLayout("cuda")
    methods = {
        "baseline_v2_xy": baseline_partition_order,
        "cut_horizontal_first": lambda rows: recursive_cut(rows, "horizontal_first"),
        "cut_vertical_first": lambda rows: recursive_cut(rows, "vertical_first"),
        "cut_balanced_gap": lambda rows: recursive_cut(rows, "balanced_gap"),
        "same_level_rows": same_level_rows,
        "spatial_graph": spatial_graph,
        "top_right_distance": top_right,
    }
    result = {"fixture_sha256": fixture_hash,
              "metric": "固定 OCR 整段；一次移位为取出一段后任意插入；最少次数 N-LIS。漏识和多识另记。",
              "methods": list(methods), "pages": {}, "totals": {}}
    source = FIXTURE.parent / fixture["source_collection"]
    for page in fixture["pages"]:
        name = page["image"]
        body = (source/name).read_bytes()
        if hashlib.sha256(body).hexdigest() != page["sha256"]:
            raise ValueError(f"原图哈希不匹配：{name}")
        rows = [dict(r, row_id=i) for i, r in enumerate(ocr["pages"][name]["rows"])]
        mapping = ROW_TO_SEGMENT[name]
        if len(rows) != len(mapping):
            raise ValueError(f"固定 OCR 行数变化，需重新人工对齐：{name}")
        image = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        panels = (panel_cache["pages"][name]["panels"] if panel_cache else layout.panels(image))
        present = set(segment_order(range(len(rows)), mapping))
        page_result = {"gold_segments": len(page["text_units"]), "evaluated_segments": len(present),
                       "missing_segment_ids": sorted(set(range(len(page["text_units"]))) - present),
                       "extra_ocr_row_ids": [i for i, target in enumerate(mapping) if target is None],
                       "panel_count": len(panels), "methods": {}}
        for name_method, strategy in methods.items():
            row_ids = panel_order(rows, panels, strategy)
            if sorted(row_ids) != list(range(len(rows))):
                raise AssertionError(f"排序丢行或重复：{name} {name_method}")
            indexes = segment_order(row_ids, mapping)
            page_result["methods"][name_method] = {"moves": minimum_moves(indexes),
                                                    "segment_order": indexes, "ocr_row_order": row_ids}
        result["pages"][name] = page_result
        print(name, {method: page_result["methods"][method]["moves"] for method in methods}, flush=True)
    for method in methods:
        result["totals"][method] = sum(p["methods"][method]["moves"] for p in result["pages"].values())
    result["totals"]["evaluated_segments"] = sum(p["evaluated_segments"] for p in result["pages"].values())
    result["totals"]["missing_ocr_segments"] = sum(len(p["missing_segment_ids"]) for p in result["pages"].values())
    result["totals"]["extra_ocr_rows"] = sum(len(p["extra_ocr_row_ids"]) for p in result["pages"].values())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("合计", result["totals"], flush=True)
    print(output_path, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-json", type=Path, default=DEFAULT_OCR)
    parser.add_argument("--panels-json", type=Path, help="可选：已核对测试集哈希的 Magi v2 分镜缓存；不填则实时推理")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    compare(args.ocr_json, args.panels_json, args.output_json)
