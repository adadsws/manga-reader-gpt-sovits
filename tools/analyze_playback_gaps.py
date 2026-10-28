# 用 Android 单调时钟还原正式 MediaPlayer 的逐单元边界与段间额外等待。
import argparse
import json
import re
import wave
from pathlib import Path

TIMING = re.compile(r'ReaderTiming:\s+event=(start|end) page=(\S*) index=(\d+) generation=(\d+) elapsed_ms=(\d+) minimum_gap_ms=(\d+)')


def waveform_edges(path):
    """按 10 ms PCM RMS 找到有声窗口；返回首尾静音，作为出声边界估计。"""
    import numpy as np
    with wave.open(str(path), 'rb') as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != 'NONE':
            raise ValueError('unsupported PCM WAV')
        channels, rate, frames = audio.getnchannels(), audio.getframerate(), audio.getnframes()
        samples = np.frombuffer(audio.readframes(frames), dtype='<i2').astype(np.float32)
    samples = samples.reshape(-1, channels)
    window = max(1, round(rate * .01))
    count = len(samples) // window
    if count == 0:
        raise ValueError('empty WAV')
    windows = samples[:count * window].reshape(count, window, channels)
    rms = np.sqrt(np.mean(windows * windows, axis=(1, 2)))
    threshold = max(200.0, float(rms.max()) * .04)
    active = np.flatnonzero(rms >= threshold)
    if len(active) == 0:
        raise ValueError('no voiced PCM window')
    leading = round(active[0] * window * 1000 / rate)
    trailing = round((frames - min(frames, (int(active[-1]) + 1) * window)) * 1000 / rate)
    return leading, trailing


def intervals(lines, audio_root=None):
    boundaries = {}
    interrupted = set()
    for line in lines:
        event = TIMING.search(line)
        if event:
            kind, page, index, generation, elapsed, gap = event.groups()
            key = (page, int(generation), int(index))
            boundaries.setdefault(key, {})[kind] = (int(elapsed), int(gap))
        elif 'ReaderDebug:' in line and ('event=pause ' in line or 'event=cancel ' in line or 'event=error ' in line):
            try:
                interrupted.add(float(line.split()[0]))
            except (ValueError, IndexError):
                pass
    rows = []
    for (page, generation, index), current in sorted(boundaries.items()):
        following = boundaries.get((page, generation, index + 1), {})
        if 'end' not in current or 'start' not in following:
            continue
        end, _ = current['end']
        start, minimum = following['start']
        measured = start - end
        lead = trail = None
        status = 'ok' if measured >= 0 else 'invalid_clock_order'
        if audio_root is not None and status == 'ok':
            try:
                _, trail = waveform_edges(audio_root / page / 'units' / f'{index + 1:03d}.wav')
                lead, _ = waveform_edges(audio_root / page / 'units' / f'{index + 2:03d}.wav')
            except (OSError, ValueError) as error:
                status = 'missing_waveform_boundary: ' + str(error)
        speech_gap = measured + lead + trail if lead is not None and trail is not None else None
        rows.append({'page_id': page, 'generation': generation, 'previous_index': index,
                     'next_index': index + 1, 'previous_end_elapsed_ms': end,
                     'next_start_elapsed_ms': start, 'measured_gap_ms': measured,
                     'configured_minimum_ms': minimum,
                     'playback_extra_wait_ms': max(0, measured - minimum),
                     'previous_trailing_silence_ms': trail, 'next_leading_silence_ms': lead,
                     'speech_gap_ms': speech_gap,
                     'extra_wait_ms': max(0, speech_gap - minimum) if speech_gap is not None else None,
                     'status': status})
    return rows, {'boundary_units': len(boundaries), 'interruption_events': len(interrupted)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('log', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rows, diagnostics = intervals(args.log.read_text(encoding='utf-8', errors='replace').splitlines(),
                                  args.log.parent / 'pages')
    result = {'method': 'MediaPlayer boundaries plus 10ms PCM RMS voiced-window edges; not microphone evidence',
              'intervals': rows, 'diagnostics': diagnostics}
    output = args.output or args.log.with_name('playback-gaps.json')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{len(rows)} intervals; {diagnostics["boundary_units"]} units; {output}')


if __name__ == '__main__':
    main()
