from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import math


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def expanded(self, padding: int, width: int, height: int, align: int = 2) -> Rect:
        left = max(0, (self.x - padding) // align * align)
        top = max(0, (self.y - padding) // align * align)
        right = min(width, math.ceil((self.x + self.w + padding) / align) * align)
        bottom = min(height, math.ceil((self.y + self.h + padding) / align) * align)
        return Rect(left, top, max(0, right - left), max(0, bottom - top))

    @staticmethod
    def union(rects: list[Rect]) -> Rect:
        left, top = min(r.x for r in rects), min(r.y for r in rects)
        right, bottom = max(r.x + r.w for r in rects), max(r.y + r.h for r in rects)
        return Rect(left, top, right - left, bottom - top)


@dataclass
class Region:
    rect: Rect
    start: float = 0.0
    end: float | None = None
    keyframes: list[dict] = field(default_factory=list)

    def active(self, seconds: float) -> bool:
        return seconds >= self.start and (self.end is None or seconds <= self.end)

    def at(self, seconds: float) -> Rect:
        if not self.keyframes:
            return self.rect
        keys = sorted(self.keyframes, key=lambda k: k['time'])
        if seconds <= keys[0]['time']:
            return Rect(**keys[0]['rect'])
        for left, right in zip(keys, keys[1:]):
            if seconds <= right['time']:
                factor = (seconds - left['time']) / (right['time'] - left['time'])
                return Rect(**{k: round(left['rect'][k] * (1 - factor) + right['rect'][k] * factor)
                               for k in ('x', 'y', 'w', 'h')})
        return Rect(**keys[-1]['rect'])


@dataclass
class Job:
    source: str
    output: str
    regions: list[Region]
    export: str = 'lossless'
    margin: int = 4
    context: int = 128
    window: int = 32
    overlap: int = 6
    precision: str = 'fp16'
    crf: int = 18
    preset: str = 'slow'
    preview_start: float | None = None
    preview_duration: float = 3.0
    cache_dir: str = ''
    seamless: bool = True
    save_report: bool = False

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(data):
        data = dict(data)
        data['regions'] = [Region(Rect(**r['rect']), r.get('start', 0), r.get('end'),
                                  r.get('keyframes', [])) for r in data['regions']]
        return Job(**data)

    def validate(self, width: int, height: int):
        if Path(self.source).resolve() == Path(self.output).resolve():
            raise ValueError('Choose a new output file. The original is always kept intact.')
        if not self.regions:
            raise ValueError('Draw at least one rectangle over a watermark.')
        if self.export not in ('lossless', 'mp4'):
            raise ValueError('Unknown export format.')
        if not 0 <= self.margin <= 32 or not 16 <= self.context <= 512:
            raise ValueError('Invalid repair margin or context size.')
        if not 8 <= self.window <= 80 or not 2 <= self.overlap < self.window // 2:
            raise ValueError('Invalid temporal window settings.')
        if self.precision not in ('fp16', 'fp32') or not 0 <= self.crf <= 30:
            raise ValueError('Invalid precision or encoding quality.')
        for region in self.regions:
            if region.start < 0 or (region.end is not None and region.end < region.start):
                raise ValueError('Region end must be after its start.')
            for rect in [region.rect] + [Rect(**key['rect']) for key in region.keyframes]:
                if rect.w < 2 or rect.h < 2 or rect.x < 0 or rect.y < 0 or rect.x + rect.w > width or rect.y + rect.h > height:
                    raise ValueError('A rectangle lies outside the video or is too small.')
