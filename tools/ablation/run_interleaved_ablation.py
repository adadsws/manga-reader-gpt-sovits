# 同一源页交错运行正式 Android 朗读模式，逐次保存可恢复的播放边界证据。
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / '~temp/ocrdeps'), str(ROOT)]
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
from tools import reproduce_test as repro
from tools import full_volume_run as capture
from tools.analyze_playback_gaps import intervals

BASE = ROOT / '~outputs-intermediate/evidence/reproductions'
MODES = ('off', 'parallel', 'eager', 'off', 'prefetch', 'adaptive', 'off', 'history', 'on')
FEATURES = {
    'parallel': 'OCR 与分镜并行',
    'eager': '首段语音提前生成',
    'prefetch': '按顺序预取本页全部语音',
    'adaptive': '翻页后自适应抓帧',
    'history': '匹配历史结果时跳过处理',
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def checked_ps(action, source=None):
    args = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(ROOT / 'tools/test_session.ps1'), '-Action', action]
    if source is not None:
        args += ['-Source', str(source)]
    subprocess.run(args, cwd=ROOT, check=True)


def set_mode(mode, previous):
    desired = set(FEATURES) if mode == 'on' else ({mode} if mode != 'off' else set())
    if desired == previous:
        return desired
    capture.adb('shell', 'am', 'start', '-n', 'org.local.reader/.MainActivity')
    time.sleep(.6)
    repro.open_advanced_options()
    for feature in FEATURES:
        if (feature in desired) == (feature in previous):
            continue
        label = FEATURES[feature]
        actual_before = repro.checkbox(label, feature in desired)
        if actual_before != (feature in previous):
            raise RuntimeError(f'设置起点不符：{feature}={actual_before}')
        if repro.checkbox(label, feature in desired) != (feature in desired):
            raise RuntimeError(f'设置后核验失败：{feature}')
    return desired


def current_log(started):
    raw = capture.adb('logcat', '-d', '-v', 'epoch', '-s', 'ReaderDebug:I',
                      'ReaderTiming:I', '*:S').decode('utf-8', errors='replace')
    lines = []
    for line in raw.splitlines():
        try:
            if float(line.split()[0]) >= started:
                lines.append(line)
        except (ValueError, IndexError):
            continue
    return lines


def overlay_main_button():
    # 正式服务 dump 返回按钮实时屏幕边界，可辨别“重新开始”和“全部停止”。
    data = capture.adb('shell', 'dumpsys', 'activity', 'service',
                       'org.local.reader/.ReaderService').decode('utf-8', errors='replace')
    controls = re.search(r'controls=([^@;]+)@(\d+),(\d+),(\d+),(\d+);', data)
    if controls is None:
        raise RuntimeError('无法读取正式浮窗主按钮边界')
    label, x, y, right, bottom = controls.groups()
    x, y, right, bottom = map(int, (x, y, right, bottom))
    if right <= x or bottom <= y:
        raise RuntimeError('浮窗按钮没有有效尺寸')
    px, py = (x + right) // 2, (y + bottom) // 2
    return label, px, py


def tap_overlay(button):
    label, px, py = overlay_main_button()
    expected = '重新开始' if button == '读' else '全部停止'
    if label != expected:
        raise RuntimeError(f'浮窗主按钮状态不符：预期 {expected}，实际 {label}')
    capture.adb('shell', 'input', 'tap', px, py)
    return px, py


def settle_overlay():
    # 上一页完成后自动翻页可能已排队；每轮都从静止状态开始。
    for _ in range(5):
        label, px, py = overlay_main_button()
        if label == '全部停止':
            capture.adb('shell', 'input', 'tap', px, py)
        elif label != '重新开始':
            raise RuntimeError(f'浮窗主按钮未知：{label}')
        time.sleep(.8)
    if overlay_main_button()[0] != '重新开始':
        raise RuntimeError('自动翻页未能停止')


def run_page_mode(item, mode, slot, folder, matcher):
    folder.mkdir(parents=True, exist_ok=False)
    settle_overlay()
    capture.open_page(item)
    settle_overlay()
    before = {p.stem for p in capture.RAW.glob('*.json')}
    # 宿主与模拟器墙钟可相差约一秒；给 epoch 过滤保留三秒余量。
    started = time.time() - 3
    tap_overlay('读')
    capture_started = False
    for _ in range(8):
        time.sleep(1)
        observed = current_log(started)
        if any('event=error ' in line for line in observed):
            raise RuntimeError('投屏后正式服务报错；请检查 ReaderConsole 与电脑服务日志')
        if any('event=capture_start ' in line for line in observed):
            capture_started = True
            break
    if not capture_started:
        raise RuntimeError('点击读按钮后未启动 MediaProjection 截图')
    lines = []
    terminal = None
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        lines = current_log(started)
        events = [line for line in lines if 'ReaderDebug:' in line]
        if any('event=error ' in line for line in events):
            terminal = 'error'
            break
        if any('event=play_complete ' in line for line in events):
            terminal = 'play_complete'
            break
        if any('event=no_text_turn ' in line for line in events):
            terminal = 'no_text_turn'
            break
        time.sleep(2)
    if terminal is None:
        terminal = 'timeout'
    stages = re.findall(r'ReaderDebug: event=history_match stage=(\S+)', '\n'.join(lines))
    stage = stages[-1] if stages else None
    tap_overlay('停')
    time.sleep(.3)
    (folder / 'android.log').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    timed = re.findall(r'ReaderTiming: event=(?:start|end) page=(\S+)', '\n'.join(lines))
    ids = set(timed)
    if len(ids) > 1:
        raise RuntimeError('一次源页出现多个播放页面 ID')
    page_id = next(iter(ids), None)
    if page_id is None:
        new = sorted((p for p in capture.RAW.glob('*.json') if p.stem not in before),
                     key=lambda p: p.stat().st_mtime)
        matching = []
        for candidate in new:
            screen = capture.RAW / (candidate.stem + '.jpg')
            if screen.exists():
                check = matcher.match(screen)
                if check['accepted'] and check['source'] == item['source']:
                    matching.append(candidate.stem)
        if not matching:
            raise RuntimeError(f'无法定位无文字源页：{len(new)} 个新页面')
        page_id = matching[0]
    screen, structure = capture.RAW / (page_id + '.jpg'), capture.RAW / (page_id + '.json')
    if not screen.exists() or not structure.exists():
        raise RuntimeError('正式上传页面缺少截图或 OCR 结构')
    observed = matcher.match(screen)
    if not observed['accepted'] or observed['source'] != item['source']:
        raise RuntimeError(f'正式截图未匹配源页：{observed}')
    page_folder = folder / 'pages' / page_id
    units_folder = page_folder / 'units'
    units_folder.mkdir(parents=True)
    shutil.copy2(screen, page_folder / 'screen.jpg')
    shutil.copy2(structure, page_folder / 'ocr.json')
    units = read_json(structure)
    hashes = []
    for index in range(len(units)):
        source_wav = capture.RAW / page_id / f'{index + 1:03d}.wav'
        if not source_wav.exists():
            raise RuntimeError(f'缺少第 {index + 1} 个正式 WAV')
        destination = units_folder / source_wav.name
        shutil.copy2(source_wav, destination)
        hashes.append({'index': index + 1, 'sha256': capture.digest(destination)})
    gaps, diagnostic = intervals(lines, folder / 'pages')
    if len(gaps) != max(0, len(units) - 1) or any(row['status'] != 'ok' for row in gaps):
        raise RuntimeError(f'逐单元端点不完整：{len(gaps)}/{max(0, len(units)-1)}')
    if terminal != ('play_complete' if units else 'no_text_turn'):
        raise RuntimeError(f'页面未正常完成：{terminal}')
    write_json(folder / 'playback-gaps.json', {'method': 'MediaPlayer and PCM voiced-window edges',
                                               'intervals': gaps, 'diagnostics': diagnostic})
    write_json(folder / 'run.json', {'finished': True, 'records': [{
        'page_id': page_id, 'source': item['source'], 'units': len(units),
        'audio_units': hashes, 'match': observed, 'history_match_stage': stage}],
        'mode': mode, 'slot': slot, 'started_epoch': started, 'terminal': terminal})
    return page_id, len(units), len(gaps), stage


def run_book(source, matrix_path, modes):
    source = source.resolve()
    matrix = read_json(matrix_path) if matrix_path.exists() else []
    if not isinstance(matrix, list):
        raise RuntimeError('交错矩阵文件格式错误')
    active = read_json(repro.ACTIVE) if repro.ACTIVE.exists() else None
    if active and not active.get('restored'):
        raise RuntimeError(f'批次 {active["id"]} 尚未恢复，拒绝开始下一批')
    checked_ps('new', source)
    try:
        out, state = repro.session()
        repro.prepare()
        repro.setup('off')
        items = read_json(out / 'sources.json')
        matcher = capture.Matcher(items)
        features = set()
        for page_number, item in enumerate(items, 1):
            repro.ensure_device()
            existing = [row for row in matrix if row.get('source') == str(source)
                        and row.get('source_page') == item['source'] and row.get('status') == 'complete']
            if {row['slot'] for row in existing} == set(range(len(modes))):
                print(f'page {page_number}/{len(items)} complete, skip', flush=True)
                continue
            for row in existing:
                row['status'] = 'superseded'
            write_json(matrix_path, matrix)
            for slot, mode in enumerate(modes):
                features = set_mode(mode, features)
                run_id = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                folder = out / 'interleaved' / item['source'].removesuffix('.jpg') / f'{slot:02d}-{mode}-{run_id}'
                print(f'page {page_number}/{len(items)} {item["source"]} mode {slot+1}/{len(modes)} {mode}', flush=True)
                try:
                    page_id, units, gap_count, stage = run_page_mode(item, mode, slot, folder, matcher)
                    record = {'source': str(source), 'source_page': item['source'], 'slot': slot,
                              'mode': mode, 'batch': folder.relative_to(BASE).as_posix(),
                              'status': 'complete', 'page_id': page_id, 'units': units,
                              'gap_count': gap_count, 'history_match_stage': stage}
                except Exception as error:
                    record = {'source': str(source), 'source_page': item['source'], 'slot': slot,
                              'mode': mode, 'batch': folder.relative_to(BASE).as_posix(),
                              'status': 'failed', 'error': str(error)}
                    matrix.append(record)
                    write_json(matrix_path, matrix)
                    raise
                matrix.append(record)
                write_json(matrix_path, matrix)
        return matrix
    finally:
        checked_ps('restore')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--book1', required=True, type=Path)
    parser.add_argument('--book2', type=Path)
    parser.add_argument('--output', default='~outputs-intermediate/evidence/android-ablation/interleaved-20261028', type=Path)
    parser.add_argument('--modes', nargs='+', choices=('off', 'parallel', 'eager', 'prefetch', 'adaptive', 'history', 'on'),
                        default=MODES)
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    evidence = (ROOT / '~outputs-intermediate/evidence').resolve()
    if not output.resolve().is_relative_to(evidence) or output.resolve() == evidence:
        raise RuntimeError('输出目录必须位于项目中间证据目录')
    output.mkdir(parents=True, exist_ok=True)
    matrices = []
    sources = (args.book1, args.book2) if args.book2 is not None else (args.book1,)
    for number, source in enumerate(sources, 1):
        if not source.is_dir():
            raise RuntimeError(f'样本目录不存在：{source}')
        matrix = output / f'book{number}.json'
        run_book(source, matrix, args.modes)
        matrices.append(matrix)
    from tools.ablation.summarize_android_ablation import summarize
    result = summarize(matrices)
    write_json(output / 'summary.json', result)
    print('summary', output / 'summary.json', flush=True)


if __name__ == '__main__':
    main()
