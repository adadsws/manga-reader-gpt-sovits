"""在用户维护的 22 页表格上比较不调用 Magi 的整段排序。"""

import hashlib
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compare_reading_order import (
    DEFAULT_OCR, FIXTURE, ROW_TO_SEGMENT, minimum_moves, read_inputs,
    recursive_cut, same_level_rows, segment_order, spatial_graph, top_right,
)
from server.ocr import Region, sort


ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "~outputs-final/reading-order-test-set.md"
EXTRA = ROOT / "secrets/manga/reading-order-extra.json"
OUTPUT = ROOT / "~outputs-intermediate/evidence/no-magi-reading-order-results.json"
REPORT = ROOT / "~outputs-final/reading-order-no-magi-comparison.md"
BASE_REPORT = ROOT / "~outputs-intermediate/evidence/reading-order-algorithms-verified.json"
EXTRA_REPORT = ROOT / "~outputs-intermediate/evidence/extra-reading-order-results.json"
NUMBER = {chr(0x2460 + i): i for i in range(20)}


def test_set_rows():
    raw = MD.read_bytes()
    pages = {}
    for line in raw.decode("utf-8-sig").splitlines():
        if not re.match(r"\| [12] \| \d{4}\.jpg<br>", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        volume = int(cells[0])
        name = re.match(r"(\d{4}\.jpg)", cells[1]).group(1)
        image_match = re.search(r"!\[[^]]*\]\(<([^>]+)>\)", cells[1])
        if not image_match:
            raise ValueError(f"原图链接缺失: {volume}/{name}")
        image = Path(image_match.group(1))
        labels = []
        for part in cells[2].split("<br>"):
            if not part or part[0] not in NUMBER:
                raise ValueError(f"整段编号无法解析: {volume}/{name}: {part}")
            labels.append(NUMBER[part[0]])
        unique = list(dict.fromkeys(labels))
        if unique != list(range(len(unique))):
            raise ValueError(f"整段编号不连续或顺序有变，需更新 OCR 对齐: {volume}/{name}")
        key = (volume, name)
        if key in pages:
            raise ValueError(f"重复页: {key}")
        pages[key] = {"segments": len(unique), "image_path": image,
                      "status": cells[3], "text_fragments": len(labels)}
    if len(pages) != 22 or sum(p["segments"] for p in pages.values()) != 173:
        raise ValueError("用户测试集不是预期的 22 页 173 段")
    return pages, hashlib.sha256(raw).hexdigest()


def upstream_order(rows, image, simple=False, panel=False):
    regions = [Region(row) for row in rows]
    if panel:
        ordered = sort.sort_regions(regions, True, img=cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    elif simple:
        ordered = sort._simple_sort(regions, True)
    else:
        ordered = sort.sort_regions(regions, True)
    return [region.data["row_id"] for region in ordered]


def compare():
    md_pages, md_sha = test_set_rows()
    original, original_sha, ocr, _ = read_inputs(DEFAULT_OCR, None)
    extra_bytes = EXTRA.read_bytes()
    extra = json.loads(extra_bytes)
    baseline = json.loads(BASE_REPORT.read_text(encoding="utf-8"))
    extra_baseline = json.loads(EXTRA_REPORT.read_text(encoding="utf-8"))
    if baseline["fixture_sha256"] != original_sha:
        raise ValueError("原有 12 页 Magi 基准与当前原图测试集不一致")
    if extra_baseline["fixture_sha256"] != hashlib.sha256(extra_bytes).hexdigest():
        raise ValueError("新增 10 页 Magi 基准与当前 OCR 测试集不一致")
    methods = {
        "opencv_panels": lambda rows, image: upstream_order(rows, image, panel=True),
        "upstream_smart": lambda rows, image: upstream_order(rows, image),
        "upstream_simple": lambda rows, image: upstream_order(rows, image, simple=True),
        "xy_largest_gap": lambda rows, image: recursive_cut(rows, "largest_gap"),
        "xy_horizontal_first": lambda rows, image: recursive_cut(rows, "horizontal_first"),
        "xy_vertical_first": lambda rows, image: recursive_cut(rows, "vertical_first"),
        "xy_balanced_gap": lambda rows, image: recursive_cut(rows, "balanced_gap"),
        "same_level_rows": lambda rows, image: same_level_rows(rows),
        "spatial_graph": lambda rows, image: spatial_graph(rows),
        "top_right_distance": lambda rows, image: top_right(rows),
    }
    cases = []
    source = FIXTURE.parent / original["source_collection"]
    for case in original["pages"]:
        name = case["image"]
        rows = [dict(row, row_id=i) for i, row in enumerate(ocr["pages"][name]["rows"])]
        cases.append((1, name, source/name, case["sha256"], rows,
                      ROW_TO_SEGMENT[name], len(case["text_units"]),
                      baseline["pages"][name]["methods"]["baseline_v2_xy"]["moves"]))
    for case in extra["pages"]:
        name = case["image"]
        digest_rows = [{key: row[key] for key in ("original", "box")}
                       for row in case["fixed_ocr_rows"]]
        encoded = json.dumps(digest_rows, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != case["fixed_ocr_rows_sha256"]:
            raise ValueError(f"固定 OCR 行已变化: {case['source_collection']}/{name}")
        rows = [dict(row, row_id=i) for i, row in enumerate(case["fixed_ocr_rows"])]
        mapping = [None]*len(rows)
        for segment, group in enumerate(case["reference_groups"]):
            for row_id in group:
                if mapping[row_id] is not None:
                    raise ValueError(f"重复 OCR 对齐: {name}/{row_id}")
                mapping[row_id] = segment
        key = f"{case['source_collection']}/{name}"
        cases.append((2 if "サッキュ2" in case["source_collection"] else 1,
                      name, EXTRA.parent/case["source_collection"]/name, case["sha256"], rows,
                      mapping, len(case["reference_groups"]),
                      extra_baseline["pages"][key]["methods"]["baseline_v2_xy"]["moves"]))
    result = {"user_md_sha256": md_sha, "extra_fixture_sha256": hashlib.sha256(extra_bytes).hexdigest(),
              "metric": "按固定 OCR 行对应的整段编号，最少取出并插入一整段次数=N-LIS；未识别段不计。",
              "methods": list(methods), "pages": {}, "totals": {}}
    if len(cases) != len(md_pages):
        raise ValueError("测试集页数与固定 OCR 不一致")
    for volume, name, path, sha, rows, mapping, gold_count, baseline_moves in cases:
        ref = md_pages.pop((volume, name))
        if path.resolve() != ref["image_path"].resolve():
            raise ValueError(f"用户表格的原图路径不匹配: {volume}/{name}")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != sha:
            raise ValueError(f"原图哈希不匹配: {volume}/{name}")
        expected = len({i for m in mapping if m is not None for i in (m if isinstance(m, tuple) else (m,))})
        if ref["segments"] != gold_count or expected > gold_count:
            raise ValueError(f"用户表格段数与固定 OCR 对齐不一致: {volume}/{name}")
        image = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
        scored = {}
        for method, strategy in methods.items():
            order = strategy(rows, image)
            if sorted(order) != list(range(len(rows))):
                raise AssertionError(f"排序丢行或重复: {volume}/{name}/{method}")
            segments = segment_order(order, mapping)
            if len(segments) != expected:
                raise AssertionError(f"整段丢失: {volume}/{name}/{method}")
            scored[method] = {"moves": minimum_moves(segments), "segment_order": segments,
                              "ocr_row_order": order}
        result["pages"][f"{volume}/{name}"] = {
            "image_path": str(path), "gold_segments": ref["segments"],
            "evaluated_segments": expected, "annotation_status": ref["status"],
            "baseline_magi_v2_xy_moves": baseline_moves, "methods": scored,
        }
        print(f"{volume}/{name}", "Magi", baseline_moves,
              {method: item["moves"] for method, item in scored.items()}, flush=True)
    if md_pages:
        raise ValueError(f"用户表格有未纳入测试的页面: {list(md_pages)}")
    result["totals"] = {method: sum(p["methods"][method]["moves"] for p in result["pages"].values())
                        for method in methods}
    result["totals"].update({"baseline_magi_v2_xy": sum(p["baseline_magi_v2_xy_moves"]
                                                        for p in result["pages"].values()),
                             "pages": len(result["pages"]),
                             "gold_segments": sum(p["gold_segments"] for p in result["pages"].values()),
                             "evaluated_segments": sum(p["evaluated_segments"] for p in result["pages"].values())})
    if hashlib.sha256(MD.read_bytes()).hexdigest() != md_sha:
        raise ValueError("运行期间用户测试集被修改，请重新执行")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    ranked = sorted(methods, key=lambda method: result["totals"][method])
    lines = ["# 无 Magi 阅读顺序对比", "",
             f"测试集：[22 页原图与人工顺序]({MD.as_posix()})；SHA-256 `{md_sha}`。",
             "固定 OCR：173 段标注，其中 169 段可对齐 OCR；4 段漏识不参加排序计分。",
             "一次移位指取出一整段话再插入任意位置；每页最少次数为 N − LIS。",
             "新增 10 页的顺序仍是代理目视草稿，等待用户复核。", "",
             "| 排序方法 | 22 页移位合计 | 零移位页数 |", "|---|---:|---:|",
             f"| Magi v2＋分区排序 | {result['totals']['baseline_magi_v2_xy']} | "
             f"{sum(p['baseline_magi_v2_xy_moves'] == 0 for p in result['pages'].values())} |"]
    for method in ("cut_horizontal_first", "cut_balanced_gap", "cut_vertical_first",
                   "same_level_rows", "spatial_graph", "top_right_distance"):
        original_pages = list(baseline["pages"].values())
        added_pages = list(extra_baseline["pages"].values())
        all_pages = original_pages + added_pages
        moves = [page["methods"][method]["moves"] for page in all_pages]
        lines.append(f"| Magi v2＋{method} | {sum(moves)} | {moves.count(0)} |")
    for method in ranked:
        clean = sum(p["methods"][method]["moves"] == 0 for p in result["pages"].values())
        lines.append(f"| {method} | {result['totals'][method]} | {clean} |")
    lines += ["", "## 逐页对比", "",
              "| 册/页 | Magi v2＋分区 | 无 Magi 横切优先 | 无 Magi 传统 OpenCV 分镜 |",
              "|---|---:|---:|---:|"]
    for key, page in result["pages"].items():
        image_link = f"[{key}](<{Path(page['image_path']).as_posix()}>)"
        lines.append(f"| {image_link} | {page['baseline_magi_v2_xy_moves']} | "
                     f"{page['methods']['xy_horizontal_first']['moves']} | "
                     f"{page['methods']['opencv_panels']['moves']} |")
    lines += ["", "完整逐页、逐算法的 OCR 行顺序与整段顺序见 JSON 结果。",
              "本次只测试排序，未改变正式应用，也未重新运行 OCR。", ""]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("合计", result["totals"], flush=True)
    print(OUTPUT, REPORT, flush=True)


if __name__ == "__main__":
    compare()
