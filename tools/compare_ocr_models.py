# 同图比较固定 RapidOCR 模型的整页识别；不改生产配置与上游快照。
import argparse
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

from opencc import OpenCC
from rapidocr import RapidOCR
from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

from server.layout import MangaLayout
from server.ocr import MangaOCR

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "~outputs-intermediate/evidence/ocr-models-20261027"
GROUND_TRUTH = ROOT / "secrets/manga/ocr-visual-ground-truth.json"


def candidate_params(name):
    params = {"EngineConfig.onnxruntime.use_cuda": True}
    if name.startswith("v6-"):
        det, rec = {
            "v6-tiny": (ModelType.TINY, ModelType.TINY),
            "v6-small": (ModelType.SMALL, ModelType.SMALL),
            "v6-small-medium-rec": (ModelType.SMALL, ModelType.MEDIUM),
            "v6-medium": (ModelType.MEDIUM, ModelType.MEDIUM),
        }[name]
        params.update({"Det.model_type": det, "Rec.model_type": rec})
    elif name.startswith("v5-"):
        size = ModelType.MOBILE if name == "v5-mobile" else ModelType.SERVER
        params.update({"Det.ocr_version": OCRVersion.PPOCRV5,
                       "Rec.ocr_version": OCRVersion.PPOCRV5,
                       "Det.model_type": size, "Rec.model_type": size})
    elif name == "v4-cht":
        params.update({"Det.ocr_version": OCRVersion.PPOCRV4,
                       "Rec.ocr_version": OCRVersion.PPOCRV4,
                       "Det.model_type": ModelType.MOBILE,
                       "Rec.model_type": ModelType.MOBILE,
                       "Rec.lang_type": LangRec.CHINESE_CHT})
    else:
        raise ValueError(f"Unknown OCR candidate: {name}")
    return params


def compact_text(text):
    return "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text))


def unordered_char_score(expected, actual):
    reference = Counter(expected)
    result = Counter(actual)
    matches = sum((reference & result).values())
    precision = matches / len(actual) if actual else 0.0
    recall = matches / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"matched": matches, "expected": len(expected), "actual": len(actual),
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def run_candidate(name):
    # 每个候选在单独进程里加载，避免 GPU 会话互相占用。
    if name == "production":
        reader = MangaOCR("cuda")
        providers = reader.ocr_providers
    else:
        # 先初始化 Magi/CUDA 运行库，再加载固定 RapidOCR 候选。
        layout = MangaLayout("cuda")
        engine = RapidOCR(params=candidate_params(name))
        providers = MangaOCR._providers(engine)
        reader = MangaOCR.__new__(MangaOCR)
        reader.engine = engine
        reader.layout = layout
        reader.cc = OpenCC("t2s")
        reader.ocr_backend = "cuda"
        # 比较整页候选本身，现有短行复识统一关闭，避免另一个模型混入。
        reader.refine_short_rows = lambda frame, rows, regions: rows
    if set(providers) != {"det", "cls", "rec"} or any(
        value[0] != "CUDAExecutionProvider" for value in providers.values()
    ):
        raise RuntimeError(f"{name} did not use CUDA for all OCR models: {providers}")
    fixture_bytes = GROUND_TRUTH.read_bytes()
    fixture = json.loads(fixture_bytes)
    if len(fixture["pages"]) < 10:
        raise ValueError("人工文字测试集数量不足")
    source = GROUND_TRUTH.parent / fixture["source_collection"]
    report = {"candidate": name, "providers": providers,
              "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
              "metric": "order-independent character multiset precision/recall/F1; punctuation ignored",
              "pages": {}}
    for item in fixture["pages"]:
        page = item["image"]
        body = (source / page).read_bytes()
        if hashlib.sha256(body).hexdigest() != item["sha256"]:
            raise ValueError(f"原图哈希不匹配：{page}")
        started = time.perf_counter()
        rows = reader.read(body)
        timing = reader.last_timings
        expected_original = compact_text("".join(item["text_units"]))
        actual_original = compact_text("".join(row["original"] for row in rows))
        expected_reading = compact_text(reader.cc.convert("".join(item["text_units"])))
        actual_reading = compact_text("".join(row["text"] for row in rows))
        report["pages"][page] = {
            "seconds": round(time.perf_counter() - started, 3),
            "ocr_seconds": timing["ocr_seconds"],
            "layout_seconds": timing["layout_seconds"],
            "post_seconds": timing["post_seconds"],
            "units": len(rows),
            "reading_score": unordered_char_score(expected_reading, actual_reading),
            "original_score": unordered_char_score(expected_original, actual_original),
            "rows": [{"original": row["original"], "text": row["text"],
                      "box": row["box"], "confidence": row["confidence"]} for row in rows],
        }
        print(page, "reading_F1", report["pages"][page]["reading_score"]["f1"],
              "original_F1", report["pages"][page]["original_score"]["f1"],
              "seconds", report["pages"][page]["seconds"], flush=True)
    totals = {kind: {key: sum(page[kind][key] for page in report["pages"].values())
                     for key in ("matched", "expected", "actual")}
              for kind in ("reading_score", "original_score")}
    for kind, values in totals.items():
        precision = values["matched"] / values["actual"] if values["actual"] else 0.0
        recall = values["matched"] / values["expected"] if values["expected"] else 0.0
        values.update({"precision": round(precision, 4), "recall": round(recall, 4),
                       "f1": round(2 * precision * recall / (precision + recall), 4)
                       if precision + recall else 0.0})
    report["totals"] = totals
    OUT.mkdir(parents=True, exist_ok=True)
    output = OUT / f"visual{len(fixture['pages'])}-{name}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("overall reading_F1", totals["reading_score"]["f1"],
          "original_F1", totals["original_score"]["f1"], flush=True)
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", choices=("v6-tiny", "v6-small", "v6-small-medium-rec",
                                              "v6-medium", "v5-mobile", "v5-server", "v4-cht",
                                              "production"))
    run_candidate(parser.parse_args().candidate)
