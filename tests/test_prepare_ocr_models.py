import hashlib
import io
import json
import unittest
import uuid
from unittest.mock import patch

from tools import prepare_ocr_models


class PrepareOcrModelsTests(unittest.TestCase):
    def fresh_root(self):
        root = prepare_ocr_models.ROOT / "~temp/test-prepare-ocr-models" / uuid.uuid4().hex
        (root / "config").mkdir(parents=True)
        return root

    def test_download_verifies_and_reuses_locked_model(self):
        payload = b"locked model bytes"
        root = self.fresh_root()
        config = root / "config"
        (config / "ocr-models.lock.json").write_text(json.dumps([{
            "file": prepare_ocr_models.NAME,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }]), encoding="utf-8")
        with patch.object(prepare_ocr_models, "ROOT", root), patch.object(
            prepare_ocr_models, "urlopen", return_value=io.BytesIO(payload)
        ) as download:
            path = prepare_ocr_models.prepare()
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(prepare_ocr_models.prepare(), path)
            download.assert_called_once()

    def test_existing_wrong_model_is_not_overwritten(self):
        root = self.fresh_root()
        config = root / "config"
        (config / "ocr-models.lock.json").write_text(json.dumps([{
            "file": prepare_ocr_models.NAME,
            "bytes": 4,
            "sha256": hashlib.sha256(b"good").hexdigest(),
        }]), encoding="utf-8")
        path = root / "~temp/ocrdeps/rapidocr/models" / prepare_ocr_models.NAME
        path.parent.mkdir(parents=True)
        path.write_bytes(b"user")
        with patch.object(prepare_ocr_models, "ROOT", root), patch.object(
            prepare_ocr_models, "urlopen"
        ) as download:
            with self.assertRaisesRegex(ValueError, "differs from lock"):
                prepare_ocr_models.prepare()
            self.assertEqual(path.read_bytes(), b"user")
            download.assert_not_called()
