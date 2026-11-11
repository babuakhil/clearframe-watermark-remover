"""Small reproducible GPU and preservation check using the upstream sample shot."""
from pathlib import Path
from fractions import Fraction
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import av
import cv2
import numpy as np
from PIL import Image, ImageDraw

from watermark_remover.domain import Job, Rect, Region
from watermark_remover.media import probe, rgb_frame, run_tool, executable, plane_view
from watermark_remover.pipeline import process


def make_examples():
    folder = ROOT/'examples'
    folder.mkdir(exist_ok=True)
    samples = sorted((ROOT/'vendor'/'ProPainter'/'inputs'/'object_removal'/'bmx-trees').glob('*.jpg'))[:24]
    if len(samples) < 24:
        raise RuntimeError('Upstream example frames are missing.')
    for name, marked in [('clean-reference.mkv', False), ('marked-silent.mkv', True)]:
        with av.open(str(folder/name), 'w') as output:
            stream = output.add_stream('ffv1', rate=12)
            stream.width, stream.height, stream.pix_fmt = 640, 360, 'yuv420p'
            stream.codec_context.time_base = Fraction(1, 12000)
            stream.time_base = Fraction(1, 12000)
            for index, sample in enumerate(samples):
                image = Image.open(sample).convert('RGB').resize((640, 360), Image.Resampling.LANCZOS)
                if marked:
                    draw = ImageDraw.Draw(image)
                    draw.rectangle((504, 30, 611, 61), fill=(233, 237, 244))
                    draw.text((517, 39), 'WATERMARK', fill=(25, 35, 45))
                frame = av.VideoFrame.from_ndarray(np.asarray(image), format='rgb24')
                frame.pts, frame.time_base = index*1000, Fraction(1, 12000)
                for packet in stream.encode(frame):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
    target = folder/'Watermarked sample.mp4'
    run_tool([executable('ffmpeg'), '-v', 'error', '-y', '-i', str(folder/'marked-silent.mkv'),
              '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
              '-map', '0:v', '-map', '1:a', '-c:v', 'libx264', '-crf', '18', '-preset', 'slow',
              '-c:a', 'aac', '-b:a', '128k', '-shortest', str(target)])
    job = Job(str(target), str(folder/'Repaired sample.mkv'), [Region(Rect(504, 30, 108, 32))], window=32)
    (folder/'sample-selections.json').write_text(json.dumps(job.to_dict(), indent=2))
    return job


def run():
    job = make_examples()
    # Re-running uses a distinct result; cached repair chunks remain reusable.
    if Path(job.output).exists():
        job.output = str(ROOT/'examples'/f'Repaired sample-{int(time.time())}.mkv')
    import torch
    torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    output = process(job, lambda **kw: print(json.dumps(kw), flush=True))
    info = probe(job.source)
    area = job.regions[0].rect.expanded(job.margin, info.width, info.height)
    outside_equal = True
    scores_before, scores_after = [], []
    from skimage.metrics import structural_similarity
    with av.open(job.source) as marked, av.open(output) as repaired, av.open(str(ROOT/'examples'/'clean-reference.mkv')) as clean:
        for index, (source, result, truth) in enumerate(zip(marked.decode(video=0), repaired.decode(video=0), clean.decode(video=0))):
            for p, (sp, rp) in enumerate(zip(source.planes, result.planes)):
                sx = 1 if p == 0 else 2
                sy = 1 if p == 0 else 2
                a, b = plane_view(sp)[:, :sp.width], plane_view(rp)[:, :rp.width]
                mask = np.ones_like(a, dtype=bool)
                mask[area.y//sy:(area.y+area.h)//sy, area.x//sx:(area.x+area.w)//sx] = False
                outside_equal &= bool(np.array_equal(a[mask], b[mask]))
            original, fixed, reference = [rgb_frame(f, info) for f in (source, result, truth)]
            rect = job.regions[0].rect
            region = np.s_[rect.y:rect.y+rect.h, rect.x:rect.x+rect.w]
            scores_before.append(structural_similarity(reference[region], original[region], channel_axis=2, data_range=255))
            scores_after.append(structural_similarity(reference[region], fixed[region], channel_axis=2, data_range=255))
            if index == 12:
                comparison = Image.new('RGB', (1280, 400), (16, 23, 32))
                comparison.paste(Image.fromarray(original), (0, 40))
                comparison.paste(Image.fromarray(fixed), (640, 40))
                draw = ImageDraw.Draw(comparison)
                draw.text((20, 14), 'SOURCE WITH WATERMARK', fill='#e8edf5')
                draw.text((660, 14), 'TEMPORAL AI REPAIR - LOSSLESS OUTPUT', fill='#63e1c6')
                comparison.save(ROOT/'examples'/'comparison.png')
    report = {'gpu': torch.cuda.get_device_name(0), 'frames': 24, 'resolution': '640x360',
              'wall_seconds': round(time.monotonic()-started, 2),
              'peak_cuda_allocated_gib': round(torch.cuda.max_memory_allocated()/1024**3, 3),
              'outside_repair_native_samples_equal': outside_equal,
              'watermark_region_ssim_before': float(np.mean(scores_before)),
              'watermark_region_ssim_after': float(np.mean(scores_after)), 'output': output}
    (ROOT/'examples'/'benchmark.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    if not outside_equal:
        raise AssertionError('Native pixels outside the repair changed in the lossless output.')


if __name__ == '__main__':
    run()
