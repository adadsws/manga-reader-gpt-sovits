"""批量模型试读事务：验证结果持久化，同时保护当前朗读选择。"""

import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from server.model_catalog import ModelCatalog


ROOT = Path(__file__).resolve().parents[1]


class Reply:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"message":"success"}'


class AutomaticValidationTests(unittest.TestCase):
    def catalog(self):
        folder = ROOT / "~outputs-intermediate/tests/model-validation" / uuid.uuid4().hex
        model_root = folder / "models/gpt-sovits/作品/v4"
        for role in ("旧角色", "新角色"):
            directory = model_root / role
            directory.mkdir(parents=True)
            (directory / f"{role}-e10.ckpt").write_bytes(b"gpt")
            (directory / f"{role}_e10_s10_l32.pth").write_bytes(b"sovits")
            (directory / "【默认】参考.wav").write_bytes(b"wav")
        settings = {"tts_url": "http://tts", "voice": "旧角色", "voice_name": "旧角色", "model_version": "v4"}
        catalog = ModelCatalog(folder / "models/gpt-sovits", settings, folder / "selection.json")
        models = catalog.scan()["models"]
        candidate = next(item for item in models if item["name"] == "新角色")
        return catalog, settings, candidate, folder

    def test_success_persists_result_without_changing_selection(self):
        catalog, settings, candidate, folder = self.catalog()
        active = catalog.active_id
        with patch("server.model_catalog.urllib.request.urlopen", return_value=Reply()) as opened:
            result = catalog.validate_one(candidate["id"], lambda item: self.assertEqual(item["reference_text"], "参考"))
        self.assertEqual(opened.call_count, 4)
        self.assertEqual(result["state"], "available")
        self.assertEqual(catalog.active_id, active)
        self.assertEqual(catalog.loaded_id, active)
        self.assertEqual(settings["voice_name"], "旧角色")
        self.assertFalse((folder / "selection.json").exists())
        self.assertEqual(catalog.scan()["counts"]["available"], 1)
        restarted = ModelCatalog(folder / "models/gpt-sovits", settings.copy(), folder / "selection.json")
        self.assertEqual(restarted.scan()["counts"]["available"], 1)
        with patch.object(catalog, "_load") as loaded:
            self.assertEqual(catalog.validate_one(candidate["id"], lambda item: None)["state"], "available")
            loaded.assert_not_called()

    def test_candidate_failure_is_recorded_after_restoration(self):
        catalog, settings, candidate, folder = self.catalog()
        active = catalog.active_id
        with patch("server.model_catalog.urllib.request.urlopen", return_value=Reply()):
            with self.assertRaisesRegex(RuntimeError, "试读失败"):
                catalog.validate_one(candidate["id"], lambda item: (_ for _ in ()).throw(ValueError("试读失败")))
        self.assertEqual(catalog.loaded_id, active)
        self.assertEqual(settings["voice_name"], "旧角色")
        validation = json.loads((folder / "model-validation.json").read_text(encoding="utf-8"))
        self.assertEqual(validation[candidate["id"]]["status"], "failed")

    def test_restoration_failure_stops_batch(self):
        catalog, settings, candidate, folder = self.catalog()
        with patch.object(catalog, "_load", side_effect=[None, RuntimeError("CUDA 恢复失败")]):
            with self.assertRaisesRegex(RuntimeError, "当前模型恢复失败"):
                catalog.validate_one(candidate["id"], lambda item: None)
        self.assertIsNone(catalog.loaded_id)
        self.assertEqual(settings["voice_name"], "旧角色")
        validation = json.loads((folder / "model-validation.json").read_text(encoding="utf-8"))
        self.assertEqual(validation[candidate["id"]]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
