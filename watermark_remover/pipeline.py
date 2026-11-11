from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import hashlib
import json
import shutil
import time
import uuid

import av
import cv2
import numpy as np

from .domain import Job, Rect
from .blend import match_boundary
from .engine import Cancelled, ProPainterEngine
from .media import audio_hashes, composite_native, executable, mux_original, probe, rgb_crop, rgb_frame, verify_timeline

REPORTS_DIR = Path(__file__).resolve().parents[1] / 'workspace' / 'logs'


def save_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)


def source_frames(info, job):
    with av.open(info.path) as container:
        index = next(v['index'] for v in info.streams if v['codec_type'] == 'video' and not v.get('disposition', {}).get('attached_pic'))
        stream = container.streams[index]
        stream.thread_type = 'AUTO'
        if job.preview_start is not None:
            container.seek(int((job.preview_start + info.start) / stream.time_base), stream=stream, backward=True)
        durations = {}
        previous_pts = None
        for packet in container.demux(stream):
            if packet.pts is not None:
                durations[packet.pts] = packet.duration
            for frame in packet.decode():
                if previous_pts is not None and frame.pts is not None and frame.pts <= previous_pts:
                    raise ValueError('The source has repeated or out-of-order frame timestamps. This malformed timeline cannot be preserved safely. Use a correctly timed copy of the source.')
                previous_pts = frame.pts
                duration = durations.pop(frame.pts, 0)
                yield_from_frame = _timed_frame(frame, info, job, duration)
                if yield_from_frame is False:
                    return
                if yield_from_frame is not None:
                    yield yield_from_frame


def _timed_frame(frame, info, job, duration):
    if frame.pts is None:
        raise ValueError('This file contains frames without timestamps. Repair requires a valid source timeline.')
    seconds = float(frame.pts * frame.time_base) - info.start
    if job.preview_start is not None:
        if seconds + 0.000001 < job.preview_start:
            return None
        if seconds >= job.preview_start + job.preview_duration - 0.000001:
            return False
    return frame, seconds, duration


def signature(job):
    source = Path(job.source).resolve()
    config = job.to_dict()
    for key in ('output', 'cache_dir', 'export', 'crf', 'preset', 'seamless', 'save_report'):
        config.pop(key, None)
    config.update({'source': str(source), 'size': source.stat().st_size,
                   'mtime': source.stat().st_mtime_ns, 'pipeline': 1})
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:24]


def cut_between(left, right, info):
    # Downsample for cut detection only. Repair pixels always remain source-scale.
    a = left.reformat(width=64, height=36, format='rgb24').to_ndarray().astype(np.float32) / 255
    b = right.reformat(width=64, height=36, format='rgb24').to_ndarray().astype(np.float32) / 255
    difference = float(np.mean(np.abs(a-b)))
    histogram_a = cv2.calcHist([(a*255).astype('uint8')], [0, 1], None, [16, 16], [0, 256, 0, 256])
    histogram_b = cv2.calcHist([(b*255).astype('uint8')], [0, 1], None, [16, 16], [0, 256, 0, 256])
    cv2.normalize(histogram_a, histogram_a)
    cv2.normalize(histogram_b, histogram_b)
    distance = cv2.compareHist(histogram_a, histogram_b, cv2.HISTCMP_BHATTACHARYYA)
    return difference > 0.32 or (difference > 0.14 and distance > 0.62)


def repair_segment(segment, begin, end, job, info, engine, notify, cancelled):
    arrays, items = {}, []
    for region_index, region in enumerate(job.regions):
        if cancelled():
            raise Cancelled()
        if not any(region.active(segment[i][2]) for i in range(begin, end)):
            continue
        areas = [region.at(seconds).expanded(job.margin, info.width, info.height)
                 if region.active(seconds) else None for _, _, seconds in segment]
        crop = Rect.union([r for r in areas if r]).expanded(job.context, info.width, info.height)
        if crop.w * crop.h > 1_400_000:
            raise ValueError(f'This selection needs a {crop.w} × {crop.h} native crop, which is too large for the 12 GB quality profile. Use a tighter rectangle or reduce Context in Advanced settings. Resolution has not been reduced.')
        images, masks = [], []
        for (_, frame, _), area in zip(segment, areas):
            images.append(rgb_crop(frame, crop, info))
            mask = np.zeros((crop.h, crop.w), dtype=np.uint8)
            if area:
                mask[area.y-crop.y:area.y-crop.y+area.h, area.x-crop.x:area.x-crop.x+area.w] = 1
            masks.append(mask)
        notify(stage='Repairing selection', detail=f'Region {region_index+1} of {len(job.regions)} · {crop.w} × {crop.h} source pixels')
        repaired = engine.repair(images, masks)
        for i in range(begin, end):
            area = areas[i]
            if area is None:
                continue
            rx, ry = area.x-crop.x, area.y-crop.y
            key = f'p{len(items)}'
            arrays[key] = repaired[i][ry:ry+area.h, rx:rx+area.w].copy()
            items.append({'index': segment[i][0], 'pts': segment[i][1].pts,
                          'time_base': str(segment[i][1].time_base), 'rect': area.__dict__, 'array': key})
    return arrays, items


def process(job: Job, notify=lambda **kw: None, cancelled=lambda: False, engine_factory=ProPainterEngine):
    started = time.monotonic()
    info = probe(job.source)
    info.validate()
    job.validate(info.width, info.height)
    executable('ffmpeg')
    output = Path(job.output).resolve()
    if output.exists():
        raise ValueError('The output already exists. Choose a new filename to keep existing files intact.')
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < 512 * 1024**2:
        raise ValueError('There is less than 512 MB free on the output drive. Choose another folder.')
    root = Path(job.cache_dir) if job.cache_dir else Path(__file__).resolve().parents[1] / 'workspace' / 'jobs'
    cache = root / signature(job)
    cache.mkdir(parents=True, exist_ok=True)
    save_json(cache / 'job.json', job.to_dict())
    notify(stage='Preparing video', detail='Checking source timing and cached repair chunks', progress=0)
    estimated_count = max(1, int((job.preview_duration if job.preview_start is not None else info.duration) * float(info.fps)))
    manifest = {'source': info.path, 'width': info.width, 'height': info.height, 'chunks': [], 'status': 'repairing'}
    engine = None
    source_iterator = source_frames(info, job)
    iterator = iter(enumerate(source_iterator))
    buffer, skip, eof, chunk_id = [], 0, False, 0
    frame_count = 0
    frame_durations = {}
    try:
        while True:
            if cancelled():
                raise Cancelled()
            while len(buffer) < job.window and not eof:
                try:
                    index, (frame, seconds, duration) = next(iterator)
                    buffer.append((index, frame, seconds))
                    frame_durations[index] = duration
                except StopIteration:
                    eof = True
            if len(buffer) <= skip:
                break
            cut = next((i for i in range(max(1, skip), len(buffer))
                        if cut_between(buffer[i-1][1], buffer[i][1], info)), None)
            length = cut if cut else len(buffer)
            segment = buffer[:length]
            end = length if cut or eof else length-job.overlap
            if end <= skip:
                end = length
            chunk_path = cache / f'chunk-{chunk_id:06d}.npz'
            first, last = segment[skip][0], segment[end-1][0]
            valid_cache = False
            if chunk_path.exists():
                with np.load(chunk_path, allow_pickle=False) as saved:
                    metadata = json.loads(saved['metadata'].tobytes().decode())
                    valid_cache = metadata['first'] == first and metadata['last'] == last
            if not valid_cache:
                if engine is None:
                    engine = engine_factory(job.precision, notify, cancelled)
                arrays, items = repair_segment(segment, skip, end, job, info, engine, notify, cancelled)
                metadata = {'first': first, 'last': last, 'items': items,
                            'timestamps': [[entry[0], entry[1].pts, str(entry[1].time_base),
                                            frame_durations[entry[0]]] for entry in segment[skip:end]]}
                arrays['metadata'] = np.frombuffer(json.dumps(metadata).encode(), dtype=np.uint8)
                temporary = chunk_path.with_suffix('.tmp')
                with temporary.open('wb') as handle:
                    np.savez_compressed(handle, **arrays)
                temporary.replace(chunk_path)
            manifest['chunks'].append(chunk_path.name)
            frame_count = last+1
            manifest['frames'] = frame_count
            save_json(cache / 'manifest.json', manifest)
            elapsed = time.monotonic()-started
            notify(stage='Repairing video', detail=f'{frame_count:,} frames ready · native resolution',
                   progress=min(84, int(84 * frame_count / estimated_count)),
                   frames=frame_count, elapsed=elapsed,
                   eta=max(0, elapsed * (estimated_count/frame_count-1)))
            chunk_id += 1
            if cut:
                buffer, skip = buffer[length:], 0
            elif eof:
                break
            else:
                retained = max(0, end-job.overlap)
                buffer, skip = buffer[retained:], end-retained
            if buffer:
                frame_durations = {index: duration for index, duration in frame_durations.items() if index >= buffer[0][0]}
        if not frame_count:
            raise ValueError('There are no frames in the selected preview range.')
        del engine
        import gc
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass
        source_iterator.close()
        buffer.clear()
        notify(stage='Exporting', detail='Compositing native samples and preserving frame timestamps', progress=85)
        intermediate = cache / ('encoded.mkv' if job.export == 'lossless' else 'encoded.mp4')
        encode(job, info, manifest, cache, intermediate, frame_count, notify, cancelled)
        if cancelled():
            raise Cancelled()
        notify(stage='Preserving audio', detail='Copying original audio and subtitle streams', progress=96)
        temporary_output = output.with_name(output.stem + '.' + uuid.uuid4().hex[:8] + '.partial' + output.suffix)
        mux_original(intermediate, job.source, temporary_output, info, job.preview_start, job.preview_duration, cancelled)
        verify = probe(temporary_output)
        if (verify.width, verify.height) != (info.width, info.height):
            raise RuntimeError('Export verification failed: dimensions changed.')
        notify(stage='Verifying export', detail='Checking every frame timestamp and preserved audio tracks', progress=98)
        count, timeline_offset = verify_timeline(intermediate, temporary_output, cancelled)
        if count != frame_count:
            raise RuntimeError(f'Export verification failed: expected {frame_count} frames, found {count}.')
        if job.preview_start is None and any(s['codec_type'] == 'audio' for s in info.streams):
            # ADTS/TS AAC remuxing may legally strip transport headers. Exact packet
            # payload equality applies to containerized codec payloads (MP4/MKV/MOV).
            source_container = info.raw.get('format', {}).get('format_name', '')
            if any(container in source_container for container in ('mov', 'mp4', 'matroska', 'webm')):
                if audio_hashes(job.source, cancelled) != audio_hashes(temporary_output, cancelled):
                    raise RuntimeError('Export verification failed: an original audio payload changed.')
        temporary_output.replace(output)
        manifest.update(status='complete', output=str(output), seconds=round(time.monotonic()-started, 2))
        save_json(cache / 'manifest.json', manifest)
        report_data = {**manifest, 'settings': job.to_dict(), 'pixel_format': info.pixel_format,
                       'source_time_base': str(info.time_base), 'source_fps': str(info.fps),
                       'verified_frame_count': count, 'mux_timeline_offset_seconds': timeline_offset,
                       'engine': 'ProPainter', 'engine_commit': '326782529a63d997cab80ab21439b92ed422b3d3'}
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        save_json(REPORTS_DIR / f'{output.stem[:60]}-{uuid.uuid4().hex}.report.json', report_data)
        if job.save_report and job.preview_start is None:
            save_json(output.with_suffix(output.suffix + '.report.json'), report_data)
        notify(stage='Complete', detail=str(output), progress=100, output=str(output), frames=frame_count)
        return str(output)
    except Cancelled:
        manifest['status'] = 'cancelled'
        save_json(cache / 'manifest.json', manifest)
        raise
    finally:
        source_iterator.close()


def encode(job, info, manifest, cache, path, frame_count, notify, cancelled):
    rate = info.fps
    pix_fmt = info.pixel_format
    if pix_fmt.startswith('yuvj'):
        pix_fmt = pix_fmt.replace('yuvj', 'yuv')
    if job.export == 'mp4':
        if info.width % 2 or info.height % 2:
            pix_fmt = 'yuv444p'
        else:
            pix_fmt = 'yuv420p'
    with av.open(str(path), 'w') as destination:
        stream = destination.add_stream('ffv1' if job.export == 'lossless' else 'libx264', rate=rate)
        stream.width, stream.height = info.width, info.height
        if pix_fmt in ('rgb24', 'bgr24') and job.export == 'lossless':
            pix_fmt = 'bgr0'
        stream.pix_fmt = pix_fmt
        stream.time_base = info.time_base
        stream.codec_context.time_base = info.time_base
        stream.codec_context.sample_aspect_ratio = info.sar
        stream.codec_context.colorspace = info.colorspace
        stream.codec_context.color_range = info.color_range
        stream.codec_context.color_primaries = info.primaries
        stream.codec_context.color_trc = info.transfer
        stream.options = {'level': '3', 'slicecrc': '1'} if job.export == 'lossless' else {'crf': str(job.crf), 'preset': job.preset}
        chunks = iter(manifest['chunks'])
        saved = None
        metadata = {'last': -1}
        patches = {}
        durations = {}

        def mux(packet):
            if packet.pts in durations:
                packet.duration = durations.pop(packet.pts)
            destination.mux(packet)

        try:
            for index, (frame, seconds, duration) in enumerate(source_frames(info, job)):
                if cancelled():
                    raise Cancelled()
                if index > metadata['last']:
                    if saved is not None:
                        saved.close()
                    saved = np.load(cache / next(chunks), allow_pickle=False)
                    metadata = json.loads(saved['metadata'].tobytes().decode())
                    patches = {}
                    for item in metadata['items']:
                        patches.setdefault(item['index'], []).append(item)
                for item in patches.get(index, []):
                    if item['pts'] != frame.pts or Fraction(item['time_base']) != frame.time_base:
                        raise RuntimeError('Source timestamps changed. Cached repair cannot be safely reused.')
                    area = Rect(**item['rect'])
                    patch = saved[item['array']]
                    if job.seamless:
                        patch = match_boundary(patch, rgb_crop(frame, area, info),
                                               (area.y > 0, area.y+area.h < info.height,
                                                area.x > 0, area.x+area.w < info.width))
                    composite_native(frame, patch, area, [area], info)
                # An encoder may change frame fields, but the source is not reused here.
                frame.time_base = info.time_base
                frame.pict_type = av.video.frame.PictureType.NONE
                durations[frame.pts] = duration or max(1, round(1 / (info.fps * info.time_base)))
                if frame.format.name != pix_fmt:
                    frame = frame.reformat(format=pix_fmt)
                for packet in stream.encode(frame):
                    mux(packet)
                if index % 20 == 0:
                    notify(stage='Exporting', detail=f'Encoding {index+1:,} / {frame_count:,} frames',
                           progress=85+int(10*(index+1)/frame_count))
            for packet in stream.encode():
                mux(packet)
        finally:
            if saved is not None:
                saved.close()
