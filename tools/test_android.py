# 专用 ReaderAosp35 的安卓回归；构建正式 APK 和独立测试 APK，不查看图片。
import hashlib
import argparse
import json
import re
import subprocess
from pathlib import Path
from tools.build_android import build, output_path

ROOT = Path(__file__).resolve().parents[1]
ADB = ROOT / '~temp/android-sdk/platform-tools/adb.exe'
READER_SERIAL = None

def find_reader_serial():
    """只查找官方 SDK 中运行的 ReaderAosp35，避开其他 AVD 占用的端口。"""
    devices = subprocess.check_output([str(ADB), 'devices'], timeout=30).decode('utf-8', errors='replace')
    matches = []
    for line in devices.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith('emulator-') or parts[1] != 'device':
            continue
        serial = parts[0]
        try:
            name = subprocess.check_output(
                [str(ADB), '-s', serial, 'emu', 'avd', 'name'], timeout=15
            ).decode('utf-8', errors='replace').replace('\r', '').splitlines()[0].strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError):
            continue
        if name == 'ReaderAosp35':
            matches.append(serial)
    if len(matches) != 1:
        raise RuntimeError(f'需要唯一的 ReaderAosp35，当前找到 {len(matches)} 个')
    return matches[0]

def adb(*args):
    return subprocess.check_output([str(ADB), '-s', READER_SERIAL, *args], timeout=120).decode('utf-8', errors='replace').replace('\r', '')

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--out',default='~outputs-intermediate/evidence/overlay-auto-20260920'); args=parser.parse_args()
    READER_SERIAL = find_reader_serial()
    console_name = adb('emu', 'avd', 'name').splitlines()
    avd_name = console_name[0].strip() if console_name else adb('shell', 'getprop', 'ro.boot.qemu.avd_name').strip()
    if avd_name != 'ReaderAosp35':
        raise RuntimeError('只能在专用 ReaderAosp35 上验证')
    work = build('android', 'reader')
    build('tests/android', 'reader-tests', classpath=[work / 'classes.jar'])
    for name in ('reader', 'reader-tests'):
        print(adb('install', '-r', str(output_path(name))))
    result = adb('shell', 'am', 'instrument', '-w', '-r', 'org.local.reader.tests/org.local.reader.AutoFlipTests')
    print(result)
    # instrumentation 的 -r 安装可能清空已授权服务；仅在上方已核验的专用 AVD 中恢复固定服务。
    adb('shell', 'settings', 'put', 'secure', 'enabled_accessibility_services', 'org.local.reader/.TurnService')
    adb('shell', 'settings', 'put', 'secure', 'accessibility_enabled', '1')
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / 'instrumentation.log').write_text(result.rstrip()+'\n', encoding='utf-8')
    passed = re.search(r'INSTRUMENTATION_RESULT: passed=(\d+)', result)
    summary = re.search(r'PASS: (\d+) Android playback state tests', result)
    if (not passed or not summary or passed.group(1) != summary.group(1)
            or int(passed.group(1)) < 1 or 'INSTRUMENTATION_CODE: -1' not in result):
        raise RuntimeError('安卓状态机测试失败，见 instrumentation.log')
    apk = output_path('reader')
    (out / 'apk.json').write_text(json.dumps({'sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), 'bytes': apk.stat().st_size}, indent=2)+'\n', encoding='utf-8')
