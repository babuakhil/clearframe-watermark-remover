from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from watermark_remover.domain import Job, Rect, Region
from watermark_remover.media import plane_view, probe, run_tool, executable
from watermark_remover.pipeline import process
from test_media_pipeline import ControlledEngine, fixture_video


@pytest.mark.parametrize('size', [(1920, 1080), (3840, 2160), (128, 192)])
def test_native_dimensions(tmp_path, size):
    source, _ = fixture_video(tmp_path, count=2, size=size)
    class NativeCrop(ControlledEngine):
        def repair(self, images, masks):
            # A large source must still supply 1:1 pixels to the crop engine.
            assert images[0].shape[1] == min(182, size[0])
            assert images[0].shape[0] == 172
            return super().repair(images, masks)
    job = Job(str(source), str(tmp_path/'native.mkv'), [Region(Rect(60, 50, 60, 60))], context=58,
              cache_dir=str(tmp_path/'cache'))
    process(job, engine_factory=NativeCrop)
    result = probe(job.output)
    assert (result.width, result.height) == size


@pytest.mark.parametrize('pixel_format', ['yuv422p', 'yuv444p', 'yuvj420p', 'rgb24', 'gray'])
def test_native_pixel_formats(tmp_path, pixel_format):
    source = tmp_path/'native.nut'
    with av.open(str(source), 'w', format='nut') as container:
        stream = container.add_stream('rawvideo', rate=24)
        stream.width, stream.height, stream.pix_fmt = 192, 128, pixel_format
        stream.codec_context.time_base = Fraction(1, 24000)
        for i in range(3):
            array = np.random.default_rng(2).integers(0, 256, (128, 192, 3), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(array, format='rgb24').reformat(format=pixel_format)
            frame.pts, frame.time_base = i*1000, Fraction(1, 24000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    job = Job(str(source), str(tmp_path/'native.mkv'), [Region(Rect(60, 50, 40, 20))],
              cache_dir=str(tmp_path/'cache'))
    process(job, engine_factory=ControlledEngine)
    with av.open(str(source)) as a, av.open(job.output) as b:
        original = next(a.decode(video=0))
        fixed = next(b.decode(video=0))
        if pixel_format == 'rgb24':
            assert np.array_equal(original.to_ndarray(format='rgb24')[:30], fixed.to_ndarray(format='rgb24')[:30])
        else:
            for p, q in zip(original.planes, fixed.planes):
                assert np.array_equal(plane_view(p)[:20, :p.width], plane_view(q)[:20, :q.width])


@pytest.mark.parametrize('export', ['mp4', 'lossless'])
def test_mp4_vfr_and_rotation(tmp_path, export):
    source, _ = fixture_video(tmp_path, vfr=True, count=20)
    rotated = tmp_path/'rotated.mp4'
    encoded = tmp_path/'encoded.mp4'
    run_tool([executable('ffmpeg'), '-v', 'error', '-y', '-i', str(source), '-c:v', 'libx264',
              '-fps_mode', 'passthrough', '-enc_time_base', '1:1000', str(encoded)])
    run_tool([executable('ffmpeg'), '-v', 'error', '-y', '-display_rotation:v:0', '90', '-i', str(encoded), '-c', 'copy', str(rotated)])
    info = probe(rotated)
    assert info.rotation in (90, 270)
    job = Job(str(rotated), str(tmp_path/('result.mp4' if export == 'mp4' else 'result.mkv')), [Region(Rect(60, 50, 40, 20))], export=export,
              window=16, overlap=3, cache_dir=str(tmp_path/'cache'))
    process(job, engine_factory=ControlledEngine)
    assert probe(job.output).rotation == info.rotation
    with av.open(str(rotated)) as a, av.open(job.output) as b:
        before = [float(f.pts*f.time_base) for f in a.decode(video=0)]
        after = [float(f.pts*f.time_base) for f in b.decode(video=0)]
    assert np.allclose(before, after, atol=0.001)


def test_reject_high_bit_depth(tmp_path):
    source, _ = fixture_video(tmp_path, count=2)
    high = tmp_path/'high.mkv'
    run_tool([executable('ffmpeg'), '-v', 'error', '-y', '-i', str(source), '-c:v', 'ffv1', '-pix_fmt', 'yuv420p10le', str(high)])
    with pytest.raises(ValueError, match='8-bit SDR'):
        probe(high).validate()
