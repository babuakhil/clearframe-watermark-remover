from __future__ import annotations

import math
import threading
import time

import av
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .domain import Rect, Region
from .media import display_image, frame_at, rgb_frame


def image_from_rgb(rgb):
    rgb = np.ascontiguousarray(rgb)
    return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()


class Reader(QThread):
    frame_ready = Signal(object, object, float)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, info):
        super().__init__()
        self.info = info
        self.condition = threading.Condition()
        self.request = 0.0
        self.playing = False
        self.quit_requested = False
        self.current = 0.0
        self.preview_info = None
        self.preview_start = 0.0

    def seek(self, seconds):
        with self.condition:
            self.request = seconds
            self.condition.notify()

    def set_playing(self, value):
        with self.condition:
            self.playing = value
            if value:
                self.request = self.current
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.quit_requested = True
            self.condition.notify()
        self.wait(5000)

    def run(self):
        try:
            while not self.quit_requested:
                with self.condition:
                    if self.request is None:
                        self.condition.wait(0.2)
                        continue
                    target, self.request = self.request, None
                with av.open(self.info.path) as container:
                    index = next(v['index'] for v in self.info.streams if v['codec_type'] == 'video' and not v.get('disposition', {}).get('attached_pic'))
                    stream = container.streams[index]
                    container.seek(int((target+self.info.start)/stream.time_base), stream=stream, backward=True)
                    clock_start, video_start = time.monotonic(), None
                    for frame in container.decode(stream):
                        seconds = float(frame.pts*frame.time_base)-self.info.start
                        if seconds < target-0.00001:
                            continue
                        if self.quit_requested or self.request is not None:
                            break
                        if self.playing and video_start is not None:
                            delay = (seconds-video_start)-(time.monotonic()-clock_start)
                            while delay > 0 and not self.quit_requested and self.request is None and self.playing:
                                time.sleep(min(delay, 0.02))
                                delay = (seconds-video_start)-(time.monotonic()-clock_start)
                        if video_start is None:
                            video_start = seconds
                            clock_start = time.monotonic()
                        rgb = display_image(rgb_frame(frame, self.info), self.info.rotation)
                        after = None
                        preview = self.preview_info
                        if preview and self.preview_start-0.001 <= seconds < self.preview_start+preview.duration-0.001:
                            try:
                                other, _ = frame_at(preview, max(0, seconds-self.preview_start))
                                after = image_from_rgb(display_image(rgb_frame(other, preview), self.info.rotation))
                            except Exception:
                                pass
                        self.current = seconds
                        self.frame_ready.emit(image_from_rgb(rgb), after, seconds)
                        if not self.playing:
                            break
                    else:
                        self.playing = False
                        self.stopped.emit()
        except Exception as error:
            self.failed.emit(str(error))


class Canvas(QWidget):
    changed = Signal()
    before_change = Signal()
    selection_changed = Signal(int)
    region_added = Signal(object)
    open_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(440, 280)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.info = None
        self.image = None
        self.after = None
        self.regions = []
        self.selected = -1
        self.seconds = 0.0
        self.margin = 4
        self.zoom = 1.0
        self.pan = QPointF()
        self.pan_start = None
        self.compare = False
        self.split = 0.5
        self.drag = None
        self.origin = None
        self.original_rect = None
        self.draft = None
        self.draw_new = True

    def image_rect(self):
        if not self.info:
            return QRectF()
        width, height = self.info.display_size
        if self.info.rotation in (90, 270):
            height *= float(self.info.sar)
        else:
            width *= float(self.info.sar)
        scale = min((self.width()-40)/width, (self.height()-40)/height) * self.zoom
        return QRectF((self.width()-width*scale)/2+self.pan.x(), (self.height()-height*scale)/2+self.pan.y(), width*scale, height*scale)

    def to_source(self, point):
        box = self.image_rect()
        u = min(1.0, max(0.0, (point.x()-box.x())/box.width()))
        v = min(1.0, max(0.0, (point.y()-box.y())/box.height()))
        rotation = self.info.rotation
        x, y = ((u, v) if rotation == 0 else (v, 1-u) if rotation == 90 else
                (1-u, 1-v) if rotation == 180 else (1-v, u))
        return QPointF(x*self.info.width, y*self.info.height)

    def to_display(self, point):
        x, y = point.x()/self.info.width, point.y()/self.info.height
        rotation = self.info.rotation
        u, v = ((x, y) if rotation == 0 else (1-y, x) if rotation == 90 else
                (1-x, 1-y) if rotation == 180 else (y, 1-x))
        box = self.image_rect()
        return QPointF(box.x()+u*box.width(), box.y()+v*box.height())

    def displayed_rect(self, rect):
        return QRectF(self.to_display(QPointF(rect.x, rect.y)),
                      self.to_display(QPointF(rect.x+rect.w, rect.y+rect.h))).normalized()

    def set_frame(self, before, after, seconds):
        self.image, self.after, self.seconds = before, after, seconds
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#0c1018'))
        if self.image is None:
            center = self.rect().center()
            painter.setPen(QPen(QColor('#334054'), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor('#111822'))
            painter.drawRoundedRect(QRectF(60, 40, self.width()-120, self.height()-80), 14, 14)
            painter.setPen(QColor('#55dfc5'))
            painter.setFont(QFont('Segoe UI', 32))
            painter.drawText(QRectF(center.x()-30, center.y()-90, 60, 50), Qt.AlignmentFlag.AlignCenter, '+')
            painter.setPen(QColor('#eef2f7'))
            painter.setFont(QFont('Segoe UI', 17, QFont.Weight.DemiBold))
            painter.drawText(QRectF(0, center.y()-25, self.width(), 40), Qt.AlignmentFlag.AlignCenter, 'A cleaner frame starts here')
            painter.setPen(QColor('#8997aa'))
            painter.setFont(QFont('Segoe UI', 10))
            painter.drawText(QRectF(0, center.y()+23, self.width(), 35), Qt.AlignmentFlag.AlignCenter, 'Drop a video here, or click to browse')
            return
        box = self.image_rect()
        painter.drawImage(box, self.image)
        if self.compare and self.after:
            painter.save()
            painter.setClipRect(QRectF(box.x()+box.width()*self.split, box.y(), box.width()*(1-self.split), box.height()))
            painter.drawImage(box, self.after)
            painter.restore()
            split_x = box.x()+box.width()*self.split
            painter.setPen(QPen(QColor('#55dfc5'), 2))
            painter.drawLine(QPointF(split_x, box.top()), QPointF(split_x, box.bottom()))
            painter.setBrush(QColor('#55dfc5'))
            painter.drawEllipse(QPointF(split_x, box.center().y()), 10, 10)
            painter.setPen(QColor('#0c1018'))
            painter.drawText(QRectF(split_x-9, box.center().y()-9, 18, 18), Qt.AlignmentFlag.AlignCenter, '↔')
            painter.setFont(QFont('Segoe UI', 9, QFont.Weight.DemiBold))
            for text, x in [('ORIGINAL', box.left()+14), ('REPAIRED', box.right()-110)]:
                painter.fillRect(QRectF(x-6, box.top()+10, 100, 24), QColor(10, 15, 22, 210))
                painter.setPen(QColor('#ffffff'))
                painter.drawText(QRectF(x, box.top()+10, 95, 24), Qt.AlignmentFlag.AlignVCenter, text)
        elif not self.compare:
            for index, region in enumerate(self.regions):
                if not region.active(self.seconds):
                    continue
                rect = region.at(self.seconds)
                display = self.displayed_rect(rect)
                guard = self.displayed_rect(rect.expanded(self.margin, self.info.width, self.info.height))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor('#f3be75'), 1, Qt.PenStyle.DotLine))
                painter.drawRect(guard)
                color = QColor('#55dfc5' if index == self.selected else '#a8bacf')
                painter.setPen(QPen(color, 2))
                painter.setBrush(QColor(85, 223, 197, 30))
                painter.drawRect(display)
                painter.fillRect(QRectF(display.x(), display.y()-23, 66, 22), color)
                painter.setPen(QColor('#081917'))
                painter.setFont(QFont('Segoe UI', 8, QFont.Weight.Bold))
                painter.drawText(QRectF(display.x()+7, display.y()-23, 60, 22), Qt.AlignmentFlag.AlignVCenter, f'AREA {index+1:02}')
                if index == self.selected:
                    for corner in (display.topLeft(), display.topRight(), display.bottomLeft(), display.bottomRight()):
                        painter.fillRect(QRectF(corner.x()-4, corner.y()-4, 8, 8), color)
        if self.draft:
            painter.setBrush(QColor(85, 223, 197, 35))
            painter.setPen(QPen(QColor('#55dfc5'), 2, Qt.PenStyle.DashLine))
            painter.drawRect(self.displayed_rect(self.draft))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self.info:
            self.pan_start = event.position()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.info:
            self.open_requested.emit()
            return
        self.setFocus()
        if not self.image_rect().contains(event.position()):
            return
        if self.compare and self.after:
            self.drag = 'split'
            self.mouseMoveEvent(event)
            return
        source = self.to_source(event.position())
        self.origin = source
        if not self.draw_new:
            for index in reversed(range(len(self.regions))):
                region = self.regions[index]
                if not region.active(self.seconds):
                    continue
                rect = region.at(self.seconds)
                display = self.displayed_rect(rect)
                if index == self.selected:
                    for corner, point in [('tl', QPointF(rect.x, rect.y)), ('tr', QPointF(rect.x+rect.w, rect.y)),
                                          ('bl', QPointF(rect.x, rect.y+rect.h)), ('br', QPointF(rect.x+rect.w, rect.y+rect.h))]:
                        location = self.to_display(point)
                        if (location-event.position()).manhattanLength() < 16:
                            self.before_change.emit()
                            self.drag, self.original_rect = corner, rect
                            return
                if display.contains(event.position()):
                    self.before_change.emit()
                    self.selected = index
                    self.selection_changed.emit(index)
                    self.drag, self.original_rect = 'move', rect
                    self.update()
                    return
        self.before_change.emit()
        self.drag = 'draw'

    def mouseMoveEvent(self, event):
        if self.pan_start is not None:
            self.pan += event.position()-self.pan_start
            self.pan_start = event.position()
            self.update()
            return
        if not self.info:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        self.setCursor(Qt.CursorShape.CrossCursor if self.draw_new else Qt.CursorShape.ArrowCursor)
        if not self.drag:
            return
        if self.drag == 'split':
            box = self.image_rect()
            self.split = min(0.98, max(0.02, (event.position().x()-box.x())/box.width()))
            self.update()
            return
        point = self.to_source(event.position())
        dx, dy = point.x()-self.origin.x(), point.y()-self.origin.y()
        if self.drag == 'draw':
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                size = min(abs(dx), abs(dy))
                dx, dy = math.copysign(size, dx), math.copysign(size, dy)
            x, y = round(min(self.origin.x(), self.origin.x()+dx)), round(min(self.origin.y(), self.origin.y()+dy))
            self.draft = Rect(x, y, round(abs(dx)), round(abs(dy)))
        else:
            rect = self.original_rect
            if self.drag == 'move':
                changed = Rect(max(0, min(self.info.width-rect.w, round(rect.x+dx))),
                               max(0, min(self.info.height-rect.h, round(rect.y+dy))), rect.w, rect.h)
            else:
                x0, y0, x1, y1 = rect.x, rect.y, rect.x+rect.w, rect.y+rect.h
                if 'l' in self.drag:
                    x0 = min(x1-2, max(0, round(rect.x+dx)))
                if 'r' in self.drag:
                    x1 = max(x0+2, min(self.info.width, round(rect.x+rect.w+dx)))
                if 't' in self.drag:
                    y0 = min(y1-2, max(0, round(rect.y+dy)))
                if 'b' in self.drag:
                    y1 = max(y0+2, min(self.info.height, round(rect.y+rect.h+dy)))
                changed = Rect(x0, y0, x1-x0, y1-y0)
            region = self.regions[self.selected]
            if region.keyframes:
                region.keyframes = [k for k in region.keyframes if abs(k['time']-self.seconds) > 0.001]
                region.keyframes.append({'time': self.seconds, 'rect': changed.__dict__})
            else:
                region.rect = changed
            self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.pan_start = None
            return
        if self.drag == 'draw' and self.draft and self.draft.w >= 2 and self.draft.h >= 2:
            self.region_added.emit(Region(self.draft))
        self.drag, self.draft = None, None
        self.update()
