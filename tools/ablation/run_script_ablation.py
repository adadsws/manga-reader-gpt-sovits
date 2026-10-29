"""电脑端正式 HTTP 流程消融；用 WAV 时长模拟播放，不调用 Android。"""
import argparse
import hashlib
import io
import json
import statistics
import sys
import time
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '~temp/ocrdeps'))
import numpy as np

MODES = ('off', 'parallel', 'eager', 'off', 'prefetch', 'off', 'history', 'on')
FEATURES = ('parallel', 'eager', 'prefetch', 'history')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def request(base, token, path, method='GET', body=None, headers=None, timeout=360):
    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers={'X-Reader-Token': token, **(headers or {})})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read()
    return data, started, time.perf_counter()


def wav_metrics(body):
    with wave.open(io.BytesIO(body), 'rb') as wav:
        if wav.getsampwidth() != 2 or wav.getcomptype() != 'NONE':
            raise ValueError('仅支持完整的 16-bit PCM WAV')
        channels, rate, frames = wav.getnchannels(), wav.getframerate(), wav.getnframes()
        pcm = wav.readframes(frames)
    if not frames or len(pcm) != frames * channels * 2:
        raise ValueError('WAV 帧不完整')
    samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32).reshape(-1, channels)
    window = max(1, round(rate * .01))
    count = len(samples) // window
    if not count:
        raise ValueError('WAV 没有完整的 10ms 窗口')
    rms = np.sqrt(np.mean(samples[:count * window].reshape(count, window, channels) ** 2, axis=(1, 2)))
    active = np.flatnonzero(rms >= max(200.0, float(rms.max()) * .04))
    if not len(active):
        raise ValueError('WAV 没有有声窗口')
    lead_ms = round(int(active[0]) * window * 1000 / rate)
    trail_ms = round((frames - min(frames, (int(active[-1]) + 1) * window)) * 1000 / rate)
    return {'duration_s': frames / rate, 'leading_silence_ms': lead_ms,
            'trailing_silence_ms': trail_ms, 'sha256': digest(body)}


def mode_headers(mode):
    enabled = set(FEATURES) if mode == 'on' else ({mode} if mode != 'off' else set())
    return {'Content-Type': 'image/jpeg', 'X-Reader-ASR-Check': 'all',
            'X-Reader-Speech-Speed': '1.00',
            'X-Reader-Parallel-Vision': 'on' if 'parallel' in enabled else 'off',
            'X-Reader-Eager-First-Audio': 'on' if 'eager' in enabled else 'off',
            'X-Reader-Full-Page-Prefetch': 'on' if 'prefetch' in enabled else 'off',
            'X-Reader-History-Reuse': 'on' if 'history' in enabled else 'off',
            'X-Reader-History-Hidden': 'off'}


def run_one(base, token, source, mode, gap_ms):
    raw = source.read_bytes()
    headers = mode_headers(mode)
    response, page_begin, page_end = request(base, token, '/pages', 'POST', raw, headers)
    page = json.loads(response)
    page_id = page['page_id']
    sentences = page['sentences']
    signature = digest(json.dumps([row['text'] for row in sentences], ensure_ascii=False).encode('utf-8'))
    units = []
    pairs = []
    audio_headers = {'X-Reader-ASR-Check': 'all', 'X-Reader-Speech-Speed': '1.00'}
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = None
            for index in range(len(sentences)):
                if pending is None:
                    pending = executor.submit(request, base, token,
                        f'/pages/{page_id}/audio/{index}', headers=audio_headers)
                audio, fetch_begin, fetch_end = pending.result()
                pending = None
                metric = wav_metrics(audio)
                start_at = max(time.perf_counter(),
                               units[-1]['end_s'] + gap_ms / 1000 if units else 0)
                if index + 1 < len(sentences):
                    pending = executor.submit(request, base, token,
                        f'/pages/{page_id}/audio/{index + 1}', headers=audio_headers)
                if start_at > time.perf_counter():
                    time.sleep(start_at - time.perf_counter())
                actual_start = time.perf_counter()
                time.sleep(metric['duration_s'])
                actual_end = time.perf_counter()
                unit = {'index': index, 'fetch_ms': round((fetch_end - fetch_begin) * 1000),
                        'ready_s': fetch_end, 'start_s': actual_start, 'end_s': actual_end, **metric}
                if units:
                    previous = units[-1]
                    raw_gap = (actual_start - previous['end_s']) * 1000
                    voiced_gap = raw_gap + previous['trailing_silence_ms'] + metric['leading_silence_ms']
                    pairs.append({'previous_index': index - 1, 'next_index': index,
                                  'playback_gap_ms': round(raw_gap),
                                  'configured_minimum_ms': gap_ms,
                                  'previous_trailing_silence_ms': previous['trailing_silence_ms'],
                                  'next_leading_silence_ms': metric['leading_silence_ms'],
                                  'estimated_speech_gap_ms': round(voiced_gap),
                                  'extra_wait_ms': round(max(0, voiced_gap - gap_ms))})
                units.append(unit)
    finally:
        request(base, token, '/pages/' + page_id, 'DELETE', timeout=15)
    return {'mode': mode, 'source': source.name, 'source_sha256': digest(raw),
            'page_id': page_id, 'match_stage': page.get('match_stage'),
            'text_sha256': signature, 'unit_count': len(units),
            'page_http_ms': round((page_end - page_begin) * 1000),
            'first_audio_ready_ms': round((units[0]['ready_s'] - page_begin) * 1000) if units else None,
            'first_simulated_start_ms': round((units[0]['start_s'] - page_begin) * 1000) if units else None,
            'units': units, 'pairs': pairs}


def summarize(rows, sources):
    complete = [row for row in rows if row.get('status') == 'complete']
    by_page = {}
    for row in complete:
        by_page.setdefault((row['book'], row['source']), []).append(row)
    selected = [group for group in by_page.values()
                if {r['slot'] for r in group} == set(range(len(MODES)))]
    values = {mode: [] for mode in set(MODES)}
    paired = {mode: [] for mode in set(MODES) - {'off'}}
    incompatible = 0
    for group in selected:
        off = {}
        for row in group:
            if row['mode'] == 'off':
                for pair in row['pairs']:
                    off.setdefault((row['text_sha256'], pair['previous_index']), []).append(pair['extra_wait_ms'])
        for row in group:
            values[row['mode']].extend(pair['extra_wait_ms'] for pair in row['pairs'])
            if row['mode'] == 'off':
                continue
            for pair in row['pairs']:
                baseline = off.get((row['text_sha256'], pair['previous_index']))
                if baseline:
                    paired[row['mode']].append(pair['extra_wait_ms'] - statistics.median(baseline))
                else:
                    incompatible += 1
    def stats(items):
        return {'n': len(items), 'median_ms': statistics.median(items) if items else None,
                'mean_ms': round(statistics.mean(items), 1) if items else None}
    return {'method': 'PC HTTP plus timed WAV playback simulation; no Android or acoustic capture',
            'books': [str(source) for source in sources], 'expected_pages': sum(len(list(s.glob('*.jpg'))) for s in sources),
            'complete_page_groups': len(selected), 'complete_runs': len(complete),
            'modes': {name: stats(items) for name, items in values.items()},
            'paired_delta_to_off': {name: stats(items) for name, items in paired.items()},
            'unpaired_text_pairs': incompatible,
            'adaptive_capture': 'not_applicable: Android auto-flip frame capture is not exercised'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--book1', required=True, type=Path)
    parser.add_argument('--book2', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--minimum-gap-ms', type=int, default=100)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--limit-pages', type=int, default=0)
    args = parser.parse_args()
    if args.minimum_gap_ms < 0 or args.minimum_gap_ms > 3000:
        parser.error('minimum gap must be 0..3000 ms')
    sources = [args.book1.resolve(), args.book2.resolve()]
    if not all(source.is_dir() for source in sources):
        parser.error('both manga directories must exist')
    output = args.output.resolve()
    evidence = (ROOT / '~outputs-intermediate/evidence').resolve()
    if output == evidence or not output.is_relative_to(evidence):
        parser.error('output must be inside ~outputs-intermediate/evidence')
    output.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((ROOT / 'config/reader.json').read_text(encoding='utf-8'))
    base = f'http://127.0.0.1:{cfg["reader_port"]}'
    request(base, cfg['token'], '/health', timeout=15)
    records_path = output / ('smoke.jsonl' if args.smoke else 'runs.jsonl')
    rows = [json.loads(line) for line in records_path.read_text(encoding='utf-8').splitlines()] if records_path.exists() else []
    modes = ('off', 'prefetch', 'off') if args.smoke else MODES
    for book, source in enumerate(sources, 1):
        pages = sorted(source.glob('*.jpg'))
        if args.smoke or args.limit_pages:
            pages = pages[:(1 if args.smoke else args.limit_pages)]
        for page_number, page in enumerate(pages, 1):
            past = [row for row in rows if row['book'] == book and row['source'] == page.name and row.get('status') == 'complete']
            if {row['slot'] for row in past} == set(range(len(modes))):
                print(f'book {book} page {page_number}/{len(pages)} complete, skip', flush=True)
                continue
            for row in past:
                row['status'] = 'superseded'
            for slot, mode in enumerate(modes):
                print(f'book {book} page {page_number}/{len(pages)} {page.name} {slot+1}/{len(modes)} {mode}', flush=True)
                try:
                    row = run_one(base, cfg['token'], page, mode, args.minimum_gap_ms)
                    row.update(book=book, slot=slot, status='complete')
                except Exception as error:
                    row = {'book': book, 'source': page.name, 'slot': slot,
                           'mode': mode, 'status': 'failed', 'error': str(error)}
                    rows.append(row)
                    records_path.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows), encoding='utf-8')
                    raise
                rows.append(row)
                records_path.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows), encoding='utf-8')
                if not args.smoke:
                    write_json(output / 'summary.json', summarize(rows, sources))
    if args.smoke:
        print('smoke complete', flush=True)
    else:
        write_json(output / 'summary.json', summarize(rows, sources))
        print('all complete: ' + str(output / 'summary.json'), flush=True)


if __name__ == '__main__':
    main()
