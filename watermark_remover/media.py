from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import json
import hashlib
from itertools import zip_longest
import os
import shutil
import subprocess

import av
import numpy as np

from .domain import Rect

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
NATIVE_FORMATS = {'yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p', 'yuv444p',
                  'yuvj444p', 'rgb24', 'bgr24', 'gbrp', 'gray'}


def run_tool(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', creationflags=NO_WINDOW, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:] or f'{args[0]} failed.')
    return result.stdout


def executable(name):
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f'{name} is missing. Install FFmpeg and reopen the application.')


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    duration: float
    fps: Fraction
    time_base: Fraction
    start: float
    frames: int
    pixel_format: str
    rotation: int
    sar: Fraction
    colorspace: int
    color_range: int
    primaries: int
    transfer: int
    streams: list
    raw: dict

    @property
    def display_size(self):
        return (self.height, self.width) if self.rotation in (90, 270) else (self.width, self.height)

    def validate(self):
        stream = next(s for s in self.streams if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic'))
        if self.pixel_format not in NATIVE_FORMATS:
            raise ValueError(f'{self.pixel_format} is not yet validated for quality-preserving repair. This version supports 8-bit SDR. Convert a separate SDR copy if needed; the original is kept intact.')
        if stream.get('color_transfer') in ('smpte2084', 'arib-std-b67') or stream.get('color_primaries') == 'bt2020':
            raise ValueError('HDR requires a separately validated color pipeline. This version supports SDR video.')
        if stream.get('field_order', 'unknown') not in ('progressive', 'unknown'):
            raise ValueError('Interlaced video is not supported by the temporal repair engine.')
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError('This display rotation is not supported. Standard 90-degree rotations are supported.')


def probe(path) -> VideoInfo:
    data = json.loads(run_tool([executable('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
    videos = [s for s in data['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')]
    if not videos:
        raise ValueError('No decodable video stream was found in this file.')
    video = videos[0]
    rotation = int(round(float(video.get('tags', {}).get('rotate', 0)))) % 360
    for side in video.get('side_data_list', []):
        if 'rotation' in side:
            # FFprobe reports counterclockwise display-matrix angles.
            rotation = (-int(round(float(side['rotation'])))) % 360
    with av.open(str(path)) as container:
        stream = next(s for s in container.streams.video if s.index == video['index'])
        context = stream.codec_context
        fps_text = video.get('avg_frame_rate', '0/0')
        fps = Fraction(fps_text) if fps_text != '0/0' else Fraction(30)
        return VideoInfo(str(Path(path).resolve()), int(video['width']), int(video['height']),
                         float(video.get('duration') or data.get('format', {}).get('duration') or 0),
                         fps, Fraction(video.get('time_base', '1/1000')),
                         float(video.get('start_time', 0)), int(video.get('nb_frames', 0) or 0),
                         video.get('pix_fmt', context.format.name), rotation,
                         Fraction(video.get('sample_aspect_ratio', '1:1').replace(':', '/'))
                         if video.get('sample_aspect_ratio', '1:1') not in ('N/A', '0:1') else Fraction(1),
                         context.colorspace, context.color_range, context.color_primaries,
                         context.color_trc, data['streams'], data)


def display_image(rgb, rotation):
    return np.ascontiguousarray(np.rot90(rgb, -(rotation // 90))) if rotation else rgb


def matrix(info):
    # swscale accepts 1 (709), 5 (601), 9 (2020); unknown defaults match FFmpeg.
    return info.colorspace if info.colorspace in (1, 5, 9) else (1 if info.height >= 720 else 5)


def rgb_frame(frame, info):
    return frame.reformat(format='rgb24', src_colorspace=matrix(info), dst_colorspace=matrix(info)).to_ndarray()


def frame_at(info: VideoInfo, seconds: float):
    with av.open(info.path) as container:
        stream = next(s for s in container.streams.video if s.index == next(v['index'] for v in info.streams if v['codec_type'] == 'video' and not v.get('disposition', {}).get('attached_pic')))
        target = max(0, seconds) + info.start
        container.seek(int(target / stream.time_base), stream=stream, backward=True)
        last = None
        for frame in container.decode(stream):
            last = frame
            if frame.pts is not None and float(frame.pts * frame.time_base) >= target - 0.00001:
                return frame, max(0.0, float(frame.pts * frame.time_base) - info.start)
        if last:
            return last, max(0.0, float(last.pts * last.time_base) - info.start)
    raise ValueError('This frame could not be decoded.')


def plane_view(plane):
    return np.frombuffer(plane, dtype=np.uint8).reshape(plane.height, plane.line_size)


def rgb_crop(frame, area, info):
    """Convert a native crop instead of round-tripping the full source raster."""
    crop = av.VideoFrame(area.w, area.h, frame.format.name)
    for plane_index, (original, target) in enumerate(zip(frame.planes, crop.planes)):
        horizontal = 2 if plane_index in (1, 2) and frame.format.name in ('yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p') else 1
        vertical = 2 if plane_index in (1, 2) and frame.format.name in ('yuv420p', 'yuvj420p') else 1
        channels = 3 if frame.format.name in ('rgb24', 'bgr24') else 1
        x, y = area.x//horizontal, area.y//vertical
        plane_view(target)[:, :target.width*channels] = plane_view(original)[y:y+target.height, x*channels:(x+target.width)*channels]
    crop.color_range, crop.colorspace = info.color_range, info.colorspace
    return rgb_frame(crop, info)


def composite_native(frame, repaired_rgb, crop: Rect, areas: list[Rect], info):
    """Replace only aligned native samples; keep every other source byte intact."""
    repair = av.VideoFrame.from_ndarray(np.ascontiguousarray(repaired_rgb), format='rgb24')
    repair.color_range = info.color_range
    repair.colorspace = info.colorspace
    repair = repair.reformat(format=frame.format.name, src_colorspace=matrix(info), dst_colorspace=matrix(info))
    for plane_index, (original, replacement) in enumerate(zip(frame.planes, repair.planes)):
        horizontal = 2 if plane_index in (1, 2) and frame.format.name in ('yuv420p', 'yuvj420p', 'yuv422p', 'yuvj422p') else 1
        vertical = 2 if plane_index in (1, 2) and frame.format.name in ('yuv420p', 'yuvj420p') else 1
        channels = 3 if frame.format.name in ('rgb24', 'bgr24') else 1
        original_array, replacement_array = plane_view(original), plane_view(replacement)
        for area in areas:
            x0, y0 = area.x // horizontal, area.y // vertical
            x1 = (area.x + area.w + horizontal - 1) // horizontal
            y1 = (area.y + area.h + vertical - 1) // vertical
            rx, ry = (area.x - crop.x) // horizontal, (area.y - crop.y) // vertical
            original_array[y0:y1, x0*channels:x1*channels] = replacement_array[ry:ry+y1-y0, rx*channels:(rx+x1-x0)*channels]
    return frame


def mux_original(video, source, dest, info, preview_start=None, preview_duration=3, cancelled=lambda: False):
    commands = [executable('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-copyts']
    if info.rotation:
        commands += ['-display_rotation:v:0', str(-info.rotation)]
    commands += ['-i', str(video), '-i', str(source),
                '-map', '0:v:0', '-map', '1:a?', '-map', '1:s?', '-map_metadata', '1', '-map_chapters', '1', '-c', 'copy']
    if preview_start is not None:
        # Generated preview retains source timestamps, so apply output trim to both streams.
        commands += ['-ss', str(max(0.0, preview_start + info.start)), '-t', str(preview_duration), '-avoid_negative_ts', 'make_zero']
    if str(dest).lower().endswith('.mp4'):
        commands += ['-movflags', '+faststart']
    commands.append(str(dest))
    with __import__('tempfile').TemporaryFile() as errors:
        process = subprocess.Popen(commands, stdout=subprocess.DEVNULL, stderr=errors, creationflags=NO_WINDOW)
        while process.poll() is None:
            if cancelled():
                process.terminate()
                process.wait()
                from .engine import Cancelled
                raise Cancelled()
            __import__('time').sleep(0.1)
        if process.returncode:
            errors.seek(0)
            detail = errors.read().decode('utf-8', errors='replace')[-2500:]
            raise RuntimeError('The chosen container could not preserve the original tracks. Try Lossless MKV.\n' + detail)


def verify_timeline(encoded, final, cancelled=lambda: False):
    """Check every presentation timestamp after muxing, allowing a shared offset."""
    with av.open(str(encoded)) as reference, av.open(str(final)) as candidate:
        tolerance = max(float(reference.streams.video[0].time_base),
                        float(candidate.streams.video[0].time_base)) + 0.00001
        offset, count = None, 0
        for before, after in zip_longest(reference.decode(video=0), candidate.decode(video=0)):
            if cancelled():
                from .engine import Cancelled
                raise Cancelled()
            if before is None or after is None:
                raise RuntimeError('Export verification failed: the frame count changed during muxing.')
            difference = float(after.pts*after.time_base-before.pts*before.time_base)
            if offset is None:
                offset = difference
            if abs(difference-offset) > tolerance:
                raise RuntimeError('Export verification failed: frame timing changed during muxing.')
            count += 1
        return count, offset or 0.0


def audio_hashes(path, cancelled=lambda: False):
    with av.open(str(path)) as container:
        tracks = {stream.index: hashlib.sha256() for stream in container.streams.audio}
        for packet in container.demux(list(container.streams.audio)):
            if cancelled():
                from .engine import Cancelled
                raise Cancelled()
            if packet.size and packet.stream.index in tracks:
                tracks[packet.stream.index].update(bytes(packet))
        return [digest.hexdigest() for digest in tracks.values()]
