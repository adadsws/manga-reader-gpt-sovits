# 汇总正式 Android 批次的页内播放等待，仅以相同来源页及相邻文字单元配对。
import argparse
import hashlib
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / '~outputs-intermediate/evidence/reproductions'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def load_batch(item):
    folder = (BASE / item['batch']).resolve()
    if not folder.is_relative_to(BASE.resolve()) or not folder.is_dir():
        raise ValueError('invalid batch path')
    run = read(folder / 'run.json')
    gaps = read(folder / 'playback-gaps.json')['intervals']
    pages = {record['page_id']: record for record in run['records']}
    rows = []
    for gap in gaps:
        if gap['status'] != 'ok':
            continue
        record = pages.get(gap['page_id'])
        if not record or not record['source']:
            continue
        units = read(folder / 'pages' / gap['page_id'] / 'ocr.json')
        index = gap['previous_index']
        if index + 1 >= len(units):
            continue
        pair = json.dumps([units[index]['text'], units[index + 1]['text']], ensure_ascii=False)
        signature = hashlib.sha256(pair.encode('utf-8')).hexdigest()
        rows.append({'key': (item['source'], record['source'], index, signature),
                     'source_page': record['source'], 'previous_index': index,
                     'extra_wait_ms': gap['extra_wait_ms'], 'measured_gap_ms': gap['measured_gap_ms']})
    return rows


def summary(values):
    ordered = sorted(values)
    return {'samples': len(values), 'median_ms': statistics.median(ordered) if ordered else None,
            'mean_ms': round(statistics.mean(ordered), 2) if ordered else None,
            'p95_ms': ordered[round(.95 * (len(ordered)-1))] if ordered else None,
            'max_ms': ordered[-1] if ordered else None}


def summarize(paths):
    items = []
    for path in paths:
        matrix = read(path)
        items.extend(entry for entry in (matrix if isinstance(matrix, list) else [matrix])
                     if entry['status'] == 'complete')
    groups = {}
    for item in items:
        groups.setdefault(item['mode'], []).extend(load_batch(item))
    off = {}
    for row in groups.get('off', []):
        off.setdefault(row['key'], []).append(row['extra_wait_ms'])
    result = {'modes': {}, 'comparison_to_off': {}, 'completed_batches': len(items)}
    for mode, rows in groups.items():
        result['modes'][mode] = summary([row['extra_wait_ms'] for row in rows])
        if mode == 'off':
            continue
        paired = [(row['extra_wait_ms'], statistics.median(off[row['key']])) for row in rows if row['key'] in off]
        result['comparison_to_off'][mode] = {
            'paired_samples': len(paired),
            'delta_ms': summary([value - baseline for value, baseline in paired]),
            'baseline_ms': summary([baseline for _, baseline in paired]),
            'mode_ms': summary([value for value, _ in paired])}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('matrices', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = summarize(args.matrices)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(args.output, result['completed_batches'])


if __name__ == '__main__':
    main()
