from fractions import Fraction
import json
from pathlib import Path

import av
import numpy as np
import pytest

from watermark_remover.domain import Job, Rect, Region
from watermark_remover.engine import Cancelled
from watermark_remover.media import composite_native, plane_view, probe, run_tool, executable
from watermark_remover.pipeline import process


class ControlledEngine:
    """Only for isolating media correctness; GPU inference is tested separately."""
    def __init__(self, *args):
        pass

    def repair(self, images, masks):
        output = []
        for image, mask in zip(images, masks):
            image = image.copy()
            image[mask.astype(bool)] = (70, 110, 150)
            output.append(image)
        return output


def fixture_video(folder, name='source.mkv', vfr=False, count=23, size=(192, 128)):
    path = folder/name
    timestamps = []
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('ffv1', rate=Fraction(30000, 1001))
        stream.width, stream.height = size
        stream.pix_fmt = 'yuv420p'
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        pts = 0
        for i in range(count):
            # Small changing texture; no artificial cut across chunk boundaries.
            rng = np.random.default_rng(2)
            data = rng.integers(40, 180, (size[1], size[0], 3), dtype=np.uint8)
            data = np.minimum(data.astype(int) + i, 255).astype(np.uint8)
            frame = av.VideoFrame.from_ndarray(data, format='rgb24')
            frame.pts, frame.time_base = pts, Fraction(1, 1000)
            timestamps.append(pts)
            pts += (20, 60, 35)[i % 3] if vfr else 33
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path, timestamps


@pytest.mark.parametrize('vfr', [False, True])
def test_lossless_timestamps_and_outside_pixels(tmp_path, vfr):
    source, timestamps = fixture_video(tmp_path, vfr=vfr)
    output = tmp_path/'result.mkv'
    job = Job(str(source), str(output), [Region(Rect(51, 39, 31, 21))], window=16, overlap=3,
              cache_dir=str(tmp_path/'cache'))
    process(job, engine_factory=ControlledEngine)
    area = job.regions[0].rect.expanded(job.margin, 192, 128)
    actual_times = []
    with av.open(str(source)) as a, av.open(str(output)) as b:
        for source_frame, target_frame in zip(a.decode(video=0), b.decode(video=0)):
            actual_times.append(round(float(target_frame.pts*target_frame.time_base)*1000))
            for index, (original, repaired) in enumerate(zip(source_frame.planes, target_frame.planes)):
                factor = 1 if index == 0 else 2
                mask = np.ones((original.height, original.width), dtype=bool)
                mask[area.y//factor:(area.y+area.h)//factor, area.x//factor:(area.x+area.w)//factor] = False
                assert np.array_equal(plane_view(original)[:, :original.width][mask], plane_view(repaired)[:, :repaired.width][mask])
    assert actual_times == timestamps
    # Same settings + a different export destination reuse all AI chunks.
    job.output = str(tmp_path/'resumed.mkv')
    class MustNotLoad:
        def __init__(self, *args):
            raise AssertionError('A completed chunk was unnecessarily recomputed.')
    process(job, engine_factory=MustNotLoad)


def test_audio_copy_and_preview(tmp_path):
    source, _ = fixture_video(tmp_path, count=30)
    audio_source = tmp_path/'with-audio.mkv'
    run_tool([executable('ffmpeg'), '-v', 'error', '-y', '-i', str(source), '-f', 'lavfi', '-i',
              'sine=frequency=440:duration=1:sample_rate=48000', '-map', '0:v', '-map', '1:a',
              '-c:v', 'copy', '-c:a', 'pcm_s16le', '-shortest', str(audio_source)])
    job = Job(str(audio_source), str(tmp_path/'audio-result.mkv'), [Region(Rect(30, 30, 30, 20))],
              window=16, overlap=3, cache_dir=str(tmp_path/'cache'))
    process(job, engine_factory=ControlledEngine)
    def payload(path):
        with av.open(str(path)) as container:
            return b''.join(bytes(p) for p in container.demux(audio=0) if p.size)
    assert payload(audio_source) == payload(job.output)
    job.preview_start, job.preview_duration = 0.2, 0.4
    job.output = str(tmp_path/'preview.mkv')
    process(job, engine_factory=ControlledEngine)
    assert abs(probe(job.output).duration-0.4) < 0.08


def test_cancellation_and_resume(tmp_path):
    source, _ = fixture_video(tmp_path, count=36)
    job = Job(str(source), str(tmp_path/'result.mkv'), [Region(Rect(20, 30, 20, 20))],
              window=16, overlap=3, cache_dir=str(tmp_path/'cache'))
    state = {'cancel': False}
    def event(**data):
        if data.get('frames', 0) > 0:
            state['cancel'] = True
    with pytest.raises(Cancelled):
        process(job, event, lambda: state['cancel'], engine_factory=ControlledEngine)
    assert not Path(job.output).exists()
    assert list((tmp_path/'cache').glob('*/chunk-000000.npz'))
    process(job, engine_factory=ControlledEngine)
    assert Path(job.output).exists()


def test_rectangle_keyframes_and_bounds():
    region = Region(Rect(10, 10, 20, 20), keyframes=[{'time': 0, 'rect': as_dict(Rect(10, 10, 20, 20))},
                                                   {'time': 2, 'rect': as_dict(Rect(30, 20, 40, 30))}])
    assert region.at(1) == Rect(20, 15, 30, 25)
    assert Rect(1, 1, 20, 20).expanded(4, 192, 128) == Rect(0, 0, 26, 26)
    with pytest.raises(ValueError):
        Job('a', 'a', [region]).validate(192, 128)


def as_dict(rect):
    return rect.__dict__
