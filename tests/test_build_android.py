# Android 构建路径回归：正式 APK 唯一发布，测试 APK 保持可重建输出。
import subprocess
import sys
import unittest

from tools import build_android


class AndroidBuildPathTests(unittest.TestCase):
    def test_reader_uses_the_single_published_apk(self):
        self.assertEqual(build_android.output_path("reader"), build_android.ROOT / "reader.apk")

    def test_non_release_apks_stay_in_generated_outputs(self):
        self.assertEqual(
            build_android.output_path("reader-fixture"),
            build_android.ROOT / "~outputs-final/android/reader-fixture.apk",
        )
        self.assertEqual(
            build_android.output_path("reader-tests"),
            build_android.ROOT / "~outputs-final/android/reader-tests.apk",
        )

    def test_help_documents_explicit_fixture_build(self):
        result = subprocess.run(
            [sys.executable, str(build_android.ROOT / "tools/build_android.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertIn("--fixture", result.stdout)

    def test_pc_and_android_reader_default_ports_match(self):
        self.assertEqual(build_android.verify_reader_port(), 8765)

    def test_port_mismatch_blocks_a_published_apk(self):
        android_defaults = "static final int READER_PORT = 8765;"
        with self.assertRaisesRegex(ValueError, "Reader port mismatch"):
            build_android.validate_reader_port({"reader_port": 8766}, android_defaults)
        with self.assertRaisesRegex(ValueError, "integer from 1 to 65535"):
            build_android.validate_reader_port({"reader_port": 0}, android_defaults)
