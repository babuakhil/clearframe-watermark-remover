from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import uuid

from PySide6.QtCore import QPointF, QProcess, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                              QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                              QSlider, QSpinBox, QSplitter, QToolButton, QVBoxLayout, QWidget)

from .domain import Job, Rect, Region
from .media import NO_WINDOW, probe
from .viewer import Canvas, Reader

ROOT = Path(__file__).resolve().parents[1]

STYLE = '''
QWidget { background: #111720; color: #e8edf5; font-family: "Segoe UI"; font-size: 13px; }
QMainWindow { background: #111720; }
QLabel#eyebrow { color: #7f91aa; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
QLabel#brand { color: #f6f8fc; font-size: 25px; font-weight: 700; letter-spacing: -1px; }
QLabel#subtle { color: #8f9eb2; }
QLabel#title { font-size: 17px; font-weight: 600; }
QLabel#badge { color: #70e3cc; background: #183630; border-radius: 10px; padding: 5px 10px; font-size: 10px; font-weight: 700; }
QFrame#divider { background: #293240; max-height: 1px; min-height: 1px; }
QFrame#card { background: #171f2b; border: 1px solid #2b3545; border-radius: 10px; }
QFrame#card QLabel { background: transparent; }
QPushButton, QToolButton { background: #202b3b; border: 1px solid #354154; border-radius: 6px; padding: 8px 13px; font-weight: 600; }
QPushButton:hover, QToolButton:hover { background: #2a3a4f; border-color: #536781; }
QPushButton:disabled, QToolButton:disabled { color: #536174; background: #1a2230; border-color: #263143; }
QPushButton#primary { background: #63e1c6; color: #09241d; border: none; padding: 13px 16px; font-size: 14px; }
QPushButton#primary:hover { background: #91f0da; }
QPushButton#primary:disabled { background: #29453f; color: #61857c; }
QPushButton:checked, QToolButton:checked { background: #193c36; color: #7bead1; border-color: #4eaa97; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #0e151f; border: 1px solid #334155; border-radius: 5px; padding: 7px; selection-background-color: #28594f; }
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:focus { border-color: #5b9186; }
QComboBox QAbstractItemView { background: #1b2635; selection-background-color: #28594f; }
QListWidget { background: #0e151f; border: 1px solid #2b3545; border-radius: 6px; outline: 0; padding: 3px; }
QListWidget::item { padding: 9px; border-radius: 4px; }
QListWidget::item:selected { background: #1b433c; color: #8cf0d9; }
QSlider::groove:horizontal { height: 4px; border-radius: 2px; background: #2e3b4e; }
QSlider::handle:horizontal { background: #66e4c8; width: 12px; margin: -5px 0; border-radius: 6px; }
QSlider::sub-page:horizontal { background: #4b9f8e; }
QProgressBar { background: #0b121b; border: 1px solid #283548; border-radius: 4px; height: 6px; text-align: center; }
QProgressBar::chunk { background: #63e1c6; border-radius: 3px; }
QCheckBox { spacing: 7px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #111720; width: 8px; }
QScrollBar::handle:vertical { background: #344155; min-height: 30px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu { background: #1b2533; padding: 5px; border: 1px solid #354154; }
QMenu::item { padding: 7px 20px; }
QMenu::item:selected { background: #28594f; }
QToolTip { background: #223346; color: #e8edf5; border: 1px solid #52657e; padding: 6px; }
'''


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, callback=None, name=None):
    widget = QPushButton(text)
    if callback:
        widget.clicked.connect(callback)
    if name:
        widget.setObjectName(name)
    return widget


def divider():
    frame = QFrame()
    frame.setObjectName('divider')
    return frame


def timestamp(seconds):
    seconds = max(0, seconds)
    return f'{int(seconds)//60:02}:{int(seconds)%60:02}.{int(seconds*1000)%1000:03}'


def app_icon():
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor('#183b36'))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 120, 120, 28, 28)
    painter.setPen(QColor('#7cf0d7'))
    painter.setFont(QFont('Segoe UI', 66, QFont.Weight.Light))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, 'C')
    painter.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    def __init__(self, source=None):
        super().__init__()
        self.setWindowTitle('Clearframe · Video Watermark Remover')
        self.setWindowIcon(app_icon())
        self.resize(1380, 920)
        self.setMinimumSize(1050, 740)
        self.setAcceptDrops(True)
        self.settings = QSettings('Clearframe', 'VideoRepair')
        self.info = None
        self.reader = None
        self.regions = []
        self.history = []
        self.seconds = 0.0
        self.process = None
        self.stdout_buffer = ''
        self.worker_log = ''
        self.current_job = None
        self.job_file = None
        self.preview = None
        self.last_output = None
        self.error_shown = False
        self._build_ui()
        self._shortcuts()
        if source:
            QTimer.singleShot(100, lambda: self.load_video(source))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(28, 20, 28, 20)
        outer.setSpacing(16)
        header = QHBoxLayout()
        identity = QVBoxLayout()
        identity.setSpacing(2)
        identity.addWidget(label('CLEARFRAME', 'brand'))
        identity.addWidget(label('VIDEO WATERMARK REMOVER', 'eyebrow'))
        header.addLayout(identity)
        header.addStretch()
        header.addWidget(label('●  PRIVATE · ON YOUR PC', 'badge'))
        self.open_button = button('Open video', self.open_video)
        header.addWidget(self.open_button)
        project_menu = QToolButton()
        project_menu.setText('Project')
        project_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        from PySide6.QtWidgets import QMenu
        menu = QMenu(project_menu)
        for text, callback in [('Save selections…', self.save_project), ('Open selections…', self.open_project),
                               ('Open output folder', self.open_output_folder), ('About & quality notes', self.about)]:
            menu.addAction(text, callback)
        project_menu.setMenu(menu)
        header.addWidget(project_menu)
        outer.addLayout(header)
        outer.addWidget(divider())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 18, 0)
        left_layout.setSpacing(12)
        title_row = QHBoxLayout()
        self.file_name = label('Your video, without the distraction.', 'title')
        title_row.addWidget(self.file_name, 1)
        self.resolution_badge = label('SOURCE RESOLUTION', 'eyebrow')
        title_row.addWidget(self.resolution_badge)
        left_layout.addLayout(title_row)
        self.file_details = label('Open a video, mark the watermark, and preview the repair.', 'subtle')
        left_layout.addWidget(self.file_details)
        tools = QHBoxLayout()
        self.draw_button = button('+ Draw area', self.draw_mode)
        self.draw_button.setCheckable(True)
        self.draw_button.setChecked(True)
        self.edit_button = button('Move / resize', self.edit_mode)
        self.edit_button.setCheckable(True)
        self.undo_button = button('Undo', self.undo)
        tools.addWidget(self.draw_button)
        tools.addWidget(self.edit_button)
        tools.addWidget(self.undo_button)
        tools.addStretch()
        self.compare_check = QCheckBox('Compare preview')
        self.compare_check.setEnabled(False)
        self.compare_check.toggled.connect(self.compare_mode)
        tools.addWidget(self.compare_check)
        self.zoom = QComboBox()
        self.zoom.addItems(['Fit', '150%', '200%', '100% pixels'])
        self.zoom.currentIndexChanged.connect(self.set_zoom)
        tools.addWidget(self.zoom)
        left_layout.addLayout(tools)
        self.canvas = Canvas()
        self.canvas.open_requested.connect(self.open_video)
        self.canvas.before_change.connect(self.remember)
        self.canvas.region_added.connect(self.add_region)
        self.canvas.changed.connect(self.region_changed)
        self.canvas.selection_changed.connect(self.select_region)
        left_layout.addWidget(self.canvas, 1)
        hint_row = QHBoxLayout()
        hint_row.addWidget(label('Drag to draw · Shift for a square · Middle-drag to pan', 'subtle'))
        hint_row.addStretch()
        self.preview_note = label('', 'subtle')
        hint_row.addWidget(self.preview_note)
        left_layout.addLayout(hint_row)
        transport = QHBoxLayout()
        self.play = button('Play', self.toggle_play)
        self.play.setFixedWidth(58)
        self.back = button('‹', lambda: self.step(-1))
        self.back.setFixedWidth(36)
        self.forward = button('›', lambda: self.step(1))
        self.forward.setFixedWidth(36)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 1000)
        self.timeline.sliderPressed.connect(self.pause)
        self.timeline.sliderMoved.connect(lambda value: self.seek(value/1000))
        self.timeline.sliderReleased.connect(lambda: self.seek(self.timeline.value()/1000))
        self.time_label = label('00:00.000 / 00:00.000', 'subtle')
        for widget in (self.play, self.back, self.forward, self.timeline, self.time_label):
            transport.addWidget(widget, 1 if widget == self.timeline else 0)
        left_layout.addLayout(transport)
        sample_row = QHBoxLayout()
        self.sample_button = button('Try the sample video', self.load_sample)
        sample_row.addWidget(self.sample_button)
        sample_row.addStretch()
        sample_row.addWidget(label('Temporal reconstruction · Source-scale detail', 'subtle'))
        left_layout.addLayout(sample_row)
        splitter.addWidget(left)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(400)
        panel = QWidget()
        sidebar = QVBoxLayout(panel)
        sidebar.setContentsMargins(14, 0, 3, 0)
        sidebar.setSpacing(12)
        sidebar.addWidget(label('01  SELECT THE AREA', 'eyebrow'))
        self.area_list = QListWidget()
        self.area_list.setMaximumHeight(82)
        self.area_list.setMinimumHeight(54)
        self.area_list.currentRowChanged.connect(self.select_region)
        sidebar.addWidget(self.area_list)
        area_actions = QHBoxLayout()
        area_actions.addWidget(button('Add area', self.draw_mode))
        area_actions.addWidget(button('Remove', self.remove_region))
        sidebar.addLayout(area_actions)
        self.region_details = label('No area selected', 'subtle')
        sidebar.addWidget(self.region_details)
        timing = QToolButton()
        timing.setText('Timing & motion')
        timing.setCheckable(True)
        timing.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        timing.setArrowType(Qt.ArrowType.RightArrow)
        sidebar.addWidget(timing)
        timing_panel = QWidget()
        timing_layout = QVBoxLayout(timing_panel)
        timing_layout.setContentsMargins(0, 0, 0, 0)
        range_form = QFormLayout()
        self.start_time = QDoubleSpinBox()
        self.end_time = QDoubleSpinBox()
        for spin in (self.start_time, self.end_time):
            spin.setDecimals(3)
            spin.setSuffix(' s')
            spin.setRange(0, 1000000)
            spin.editingFinished.connect(self.update_range)
        range_form.addRow('Start', self.start_time)
        range_form.addRow('End', self.end_time)
        timing_layout.addLayout(range_form)
        self.keyframe_button = button('Add position keyframe', self.add_keyframe)
        self.keyframe_button.setToolTip('Add a position at this time. Add another later, then move the rectangle. Positions interpolate between keyframes.')
        timing_layout.addWidget(self.keyframe_button)
        sidebar.addWidget(timing_panel)
        timing_panel.hide()
        timing.toggled.connect(lambda checked: (timing_panel.setVisible(checked), timing.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)))
        sidebar.addWidget(divider())
        sidebar.addWidget(label('02  REPAIR & PREVIEW', 'eyebrow'))
        basic_form = QFormLayout()
        self.margin = QSpinBox()
        self.margin.setRange(0, 32)
        self.margin.setValue(4)
        self.margin.setSuffix(' px')
        self.margin.valueChanged.connect(self.margin_changed)
        self.margin.setToolTip('Include the watermark edges, glow, and shadow. The dotted outline shows the actual repair boundary.')
        basic_form.addRow('Safety margin', self.margin)
        self.preview_duration = QComboBox()
        self.preview_duration.addItems(['2 seconds', '3 seconds', '5 seconds'])
        self.preview_duration.setCurrentIndex(1)
        basic_form.addRow('Preview length', self.preview_duration)
        sidebar.addLayout(basic_form)
        self.preview_button = button('Preview from playhead', lambda: self.start_job(True))
        sidebar.addWidget(self.preview_button)
        sidebar.addWidget(divider())
        export_panel = QWidget()
        export_layout = QVBoxLayout(export_panel)
        export_layout.setContentsMargins(14, 10, 3, 0)
        export_layout.setSpacing(11)
        export_layout.addWidget(divider())
        export_layout.addWidget(label('03  EXPORT', 'eyebrow'))
        self.export_format = QComboBox()
        self.export_format.addItems(['Lossless master  ·  MKV', 'High quality  ·  MP4'])
        self.export_format.setCurrentIndex(1)
        self.export_format.currentIndexChanged.connect(self.export_changed)
        export_layout.addWidget(self.export_format)
        self.export_note = label('Preserves the repaired frames without additional compression loss. Larger files.', 'subtle')
        self.export_note.setWordWrap(True)
        export_layout.addWidget(self.export_note)
        self.output = QLineEdit()
        self.output.setPlaceholderText('Choose an output file…')
        self.output.setReadOnly(True)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output, 1)
        output_row.addWidget(button('…', self.choose_output))
        export_layout.addLayout(output_row)
        self.save_report = QCheckBox('Save processing report')
        self.save_report.setChecked(False)
        self.save_report.setToolTip('Also save a JSON report next to the exported video. Reports are always kept in the app folder under workspace/logs.')
        export_layout.addWidget(self.save_report)
        self.export_button = button('Remove watermark & export', lambda: self.start_job(False), 'primary')
        export_layout.addWidget(self.export_button)
        advanced = QToolButton()
        advanced.setText('Advanced settings')
        advanced.setCheckable(True)
        advanced.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        advanced.setArrowType(Qt.ArrowType.RightArrow)
        sidebar.addWidget(advanced)
        advanced_panel = QWidget()
        advanced_form = QFormLayout(advanced_panel)
        advanced_form.setContentsMargins(0, 3, 0, 3)
        self.context = QSpinBox()
        self.context.setRange(16, 512)
        self.context.setSingleStep(16)
        self.context.setValue(128)
        self.context.setSuffix(' px')
        self.context.setToolTip('Surrounding background given to the AI. Larger values provide more context and use more GPU memory.')
        self.window = QSpinBox()
        self.window.setRange(16, 64)
        self.window.setSingleStep(8)
        self.window.setValue(32)
        self.precision = QComboBox()
        self.precision.addItems(['High (FP16)', 'Maximum (FP32)'])
        self.precision.setToolTip('FP32 uses more GPU memory for full numerical precision. Both modes use native source pixel scale.')
        self.crf = QSpinBox()
        self.crf.setRange(10, 22)
        self.crf.setValue(18)
        self.crf.setToolTip('MP4 only: lower numbers retain more detail and produce larger files. Lossless mode ignores this.')
        self.seamless = QCheckBox('Match surrounding light')
        self.seamless.setChecked(True)
        self.seamless.setToolTip('Match the repair boundary to the surrounding colors while preserving the generated detail. No Gaussian blur is applied.')
        self.seamless.toggled.connect(self.invalidate_preview)
        self.cache = QLineEdit(self.settings.value('cache', str(ROOT/'workspace'/'jobs')))
        cache_row = QWidget()
        cache_layout = QHBoxLayout(cache_row)
        cache_layout.setContentsMargins(0, 0, 0, 0)
        cache_layout.addWidget(self.cache)
        cache_layout.addWidget(button('…', self.choose_cache))
        for title, widget in [('Context', self.context), ('Temporal window', self.window), ('AI precision', self.precision), ('MP4 CRF', self.crf), ('Edge matching', self.seamless), ('Working folder', cache_row)]:
            advanced_form.addRow(title, widget)
        sidebar.addWidget(advanced_panel)
        advanced_panel.hide()
        advanced.toggled.connect(lambda checked: (advanced_panel.setVisible(checked), advanced.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)))
        sidebar.addStretch()
        quality_note = label('The hidden background is reconstructed. Complex detail may need a tighter area or another preview.', 'subtle')
        quality_note.setWordWrap(True)
        sidebar.addWidget(quality_note)
        scroll.setWidget(panel)
        right = QWidget()
        right.setMinimumWidth(340)
        right.setMaximumWidth(420)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(scroll, 1)
        right_layout.addWidget(export_panel)
        splitter.addWidget(right)
        splitter.setSizes([960, 350])

        status_card = QFrame()
        status_card.setObjectName('card')
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(16, 10, 16, 12)
        row = QHBoxLayout()
        status_text = QVBoxLayout()
        self.status_title = label('Ready when you are', 'title')
        self.status_detail = label('Your files stay on this computer.', 'subtle')
        self.status_detail.setWordWrap(True)
        status_text.addWidget(self.status_title)
        status_text.addWidget(self.status_detail)
        row.addLayout(status_text, 1)
        self.open_result = button('Open result', self.open_result_file)
        self.open_result.hide()
        row.addWidget(self.open_result)
        self.cancel_button = button('Cancel', self.cancel_job)
        self.cancel_button.hide()
        row.addWidget(self.cancel_button)
        status_layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)
        status_layout.addWidget(self.progress)
        outer.addWidget(status_card)
        self.export_changed()
        self.set_enabled()

    def _shortcuts(self):
        for key, callback in [('Ctrl+O', self.open_video), ('Ctrl+S', self.save_project), ('Ctrl+Z', self.undo),
                               ('Space', self.toggle_play), ('Left', lambda: self.step(-1)),
                               ('Right', lambda: self.step(1)), ('Delete', self.remove_region),
                               ('Escape', self.edit_mode)]:
            QShortcut(QKeySequence(key), self, activated=callback)

    def set_enabled(self):
        loaded = self.info is not None
        running = self.process is not None
        for widget in (self.preview_button, self.export_button):
            widget.setEnabled(loaded and bool(self.regions) and not running)
        for widget in (self.open_button, self.sample_button, self.draw_button, self.edit_button,
                       self.undo_button, self.area_list, self.start_time, self.end_time, self.keyframe_button,
                       self.margin, self.export_format, self.context, self.window, self.precision,
                       self.seamless, self.crf, self.cache, self.preview_duration, self.save_report):
            widget.setEnabled(not running)
        self.canvas.setEnabled(not running)
        self.cancel_button.setVisible(running)

    def open_video(self):
        if self.process:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Open video', self.settings.value('last_folder', str(ROOT)),
                                            'Video files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mts *.m2ts *.wmv *.flv *.mpg *.mpeg);;All files (*)')
        if path:
            self.load_video(path)

    def load_video(self, path):
        if self.process:
            return
        try:
            info = probe(path)
            info.validate()
        except Exception as error:
            self.show_error(str(error))
            return
        if self.reader:
            self.reader.stop()
        self.info = info
        self.settings.setValue('last_folder', str(Path(path).parent))
        self.regions = []
        self.history.clear()
        self.preview = None
        self.compare_check.setChecked(False)
        self.compare_check.setEnabled(False)
        self.canvas.info = info
        self.canvas.regions = self.regions
        self.canvas.image = None
        self.canvas.after = None
        self.canvas.selected = -1
        self.preview_note.setText('')
        self.file_name.setText(Path(path).name)
        self.file_name.setToolTip(str(path))
        self.file_details.setText(f'{timestamp(info.duration)}  ·  {float(info.fps):.3f} fps  ·  {info.pixel_format}  ·  Original audio retained')
        self.resolution_badge.setText(f'{info.width} × {info.height}')
        self.timeline.setRange(0, int(info.duration*1000))
        self.end_time.setValue(info.duration)
        self.reader = Reader(info)
        self.reader.frame_ready.connect(self.on_frame)
        self.reader.failed.connect(self.show_error)
        self.reader.stopped.connect(lambda: self.play.setText('Play'))
        self.reader.start()
        self.output.setText(self.default_output())
        self.status_title.setText('Draw a rectangle over the watermark')
        self.status_detail.setText('Include its edges and shadow. Hold Shift to draw a square.')
        self.progress.setValue(0)
        self.open_result.hide()
        self.refresh_regions()
        self.draw_mode()
        self.set_enabled()

    def default_output(self):
        if not self.info:
            return ''
        suffix = '.mkv' if self.export_format.currentIndex() == 0 else '.mp4'
        path = Path(self.info.path)
        candidate = path.with_name(path.stem+'_clean'+suffix)
        number = 2
        while candidate.exists():
            candidate = path.with_name(path.stem+f'_clean_{number}'+suffix)
            number += 1
        return str(candidate)

    def load_sample(self):
        path = ROOT/'examples'/'Watermarked sample.mp4'
        if not path.exists():
            self.show_error('The sample video is missing from the examples folder.')
            return
        self.load_video(str(path))
        if self.info and self.info.path == str(path.resolve()):
            data = json.loads((ROOT/'examples'/'sample-selections.json').read_text())
            self.regions = Job.from_dict(data).regions
            self.canvas.regions = self.regions
            self.refresh_regions(0)
            self.edit_mode()
            self.set_enabled()

    def on_frame(self, before, after, seconds):
        self.seconds = seconds
        self.canvas.set_frame(before, after, seconds)
        if not self.timeline.isSliderDown():
            self.timeline.setValue(round(seconds*1000))
        self.time_label.setText(f'{timestamp(seconds)} / {timestamp(self.info.duration)}')

    def seek(self, seconds):
        if self.reader:
            self.reader.seek(min(max(0, seconds), max(0, self.info.duration-1/float(self.info.fps))))

    def pause(self):
        if self.reader:
            self.reader.set_playing(False)
        self.play.setText('Play')

    def toggle_play(self):
        if not self.reader:
            return
        self.reader.set_playing(not self.reader.playing)
        self.play.setText('Pause' if self.reader.playing else 'Play')

    def step(self, direction):
        if self.info:
            self.pause()
            self.seek(self.seconds+direction/float(self.info.fps))

    def draw_mode(self):
        self.pause()
        self.compare_check.setChecked(False)
        self.canvas.draw_new = True
        self.draw_button.setChecked(True)
        self.edit_button.setChecked(False)

    def edit_mode(self):
        self.canvas.draw_new = False
        self.draw_button.setChecked(False)
        self.edit_button.setChecked(True)

    def set_zoom(self, index):
        self.canvas.pan = QPointF()
        if index < 3:
            self.canvas.zoom = (1, 1.5, 2)[index]
        elif self.info:
            width, height = self.info.display_size
            fit = min((self.canvas.width()-40)/width, (self.canvas.height()-40)/height)
            self.canvas.zoom = 1/fit
        self.canvas.update()

    def compare_mode(self, checked):
        self.canvas.compare = checked
        self.canvas.update()

    def remember(self):
        self.pause()
        self.invalidate_preview()
        self.history.append(deepcopy(self.regions))
        self.history = self.history[-30:]

    def undo(self):
        if self.history and not self.process:
            self.invalidate_preview()
            self.regions = self.history.pop()
            self.canvas.regions = self.regions
            self.refresh_regions(min(self.canvas.selected, len(self.regions)-1))
            self.set_enabled()

    def add_region(self, region):
        region.end = self.info.duration
        self.regions.append(region)
        self.refresh_regions(len(self.regions)-1)
        self.edit_mode()
        self.set_enabled()

    def refresh_regions(self, selected=None):
        index = self.canvas.selected if selected is None else selected
        self.area_list.blockSignals(True)
        self.area_list.clear()
        for number, region in enumerate(self.regions):
            self.area_list.addItem(f'Area {number+1:02}     {region.rect.w} × {region.rect.h} px' + (f'  ·  {len(region.keyframes)} keys' if region.keyframes else ''))
        self.area_list.setCurrentRow(index)
        self.area_list.blockSignals(False)
        self.select_region(index)
        self.canvas.update()

    def select_region(self, index):
        self.canvas.selected = index
        self.area_list.blockSignals(True)
        self.area_list.setCurrentRow(index)
        self.area_list.blockSignals(False)
        if 0 <= index < len(self.regions):
            region = self.regions[index]
            self.start_time.setValue(region.start)
            self.end_time.setValue(region.end if region.end is not None else self.info.duration)
            rect = region.at(self.seconds)
            self.region_details.setText(f'X {rect.x}  ·  Y {rect.y}  ·  {rect.w} × {rect.h} px')
        else:
            self.region_details.setText('No area selected')
        self.canvas.update()

    def region_changed(self):
        self.refresh_regions(self.canvas.selected)

    def remove_region(self):
        index = self.canvas.selected
        if 0 <= index < len(self.regions) and not self.process:
            self.remember()
            self.regions.pop(index)
            self.refresh_regions(min(index, len(self.regions)-1))
            self.set_enabled()

    def update_range(self):
        index = self.canvas.selected
        if 0 <= index < len(self.regions):
            start, end = self.start_time.value(), self.end_time.value()
            if end < start:
                self.end_time.setValue(start)
                end = start
            self.remember()
            self.regions[index].start = start
            self.regions[index].end = end
            self.canvas.update()

    def add_keyframe(self):
        if 0 <= self.canvas.selected < len(self.regions):
            self.remember()
            region = self.regions[self.canvas.selected]
            rect = region.at(self.seconds)
            region.keyframes = [k for k in region.keyframes if abs(k['time']-self.seconds) > 0.001]
            region.keyframes.append({'time': self.seconds, 'rect': asdict(rect)})
            self.refresh_regions()
            self.edit_mode()

    def margin_changed(self, value):
        self.canvas.margin = value
        self.canvas.update()
        self.invalidate_preview()

    def invalidate_preview(self):
        if self.reader and self.reader.preview_info:
            self.reader.preview_info = None
            self.canvas.after = None
            self.compare_check.setChecked(False)
            self.compare_check.setEnabled(False)
            self.preview_note.setText('Settings changed · generate a new preview')

    def export_changed(self):
        lossless = self.export_format.currentIndex() == 0
        self.export_note.setText('Preserves the repaired frames without additional compression loss. Larger files.' if lossless else 'Smaller, widely compatible files. High-quality encoding adds a small compression loss.')
        if self.info:
            self.output.setText(self.default_output())

    def choose_output(self):
        if not self.info:
            return
        suffix = '.mkv' if self.export_format.currentIndex() == 0 else '.mp4'
        path, _ = QFileDialog.getSaveFileName(self, 'Save repaired video', self.output.text(), 'Video (*'+suffix+')')
        if path:
            if not path.lower().endswith(suffix):
                path += suffix
            self.output.setText(path)

    def choose_cache(self):
        directory = QFileDialog.getExistingDirectory(self, 'Working folder for resumable repair chunks', self.cache.text())
        if directory:
            self.cache.setText(directory)
            self.settings.setValue('cache', directory)

    def make_job(self):
        return Job(self.info.path, self.output.text(), deepcopy(self.regions),
                   'lossless' if self.export_format.currentIndex() == 0 else 'mp4',
                   self.margin.value(), self.context.value(), self.window.value(), 6,
                   'fp16' if self.precision.currentIndex() == 0 else 'fp32', self.crf.value(),
                   cache_dir=self.cache.text(), seamless=self.seamless.isChecked(),
                   save_report=self.save_report.isChecked())

    def start_job(self, preview):
        if self.process or not self.info or not self.regions:
            return
        self.pause()
        job = self.make_job()
        if preview:
            folder = ROOT/'workspace'/'previews'
            folder.mkdir(parents=True, exist_ok=True)
            job.output = str(folder/f'preview-{uuid.uuid4().hex[:10]}.mkv')
            job.export = 'lossless'
            job.preview_start = self.seconds
            job.preview_duration = (2, 3, 5)[self.preview_duration.currentIndex()]
            job.save_report = False
        try:
            job.validate(self.info.width, self.info.height)
            if Path(job.output).exists():
                raise ValueError('The output already exists. Choose a new filename to keep existing files intact.')
            if not Path(job.output).suffix.lower() == ('.mkv' if job.export == 'lossless' else '.mp4'):
                raise ValueError('Choose an output file with the matching MKV or MP4 extension.')
        except Exception as error:
            self.show_error(str(error))
            return
        folder = ROOT/'workspace'/'requests'
        folder.mkdir(parents=True, exist_ok=True)
        self.job_file = folder/f'{uuid.uuid4().hex}.json'
        self.job_file.write_text(json.dumps(job.to_dict(), indent=2), encoding='utf-8')
        self.current_job = job
        self.error_shown = False
        self.worker_log = ''
        self.stdout_buffer = ''
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.readyReadStandardError.connect(self.read_errors)
        self.process.finished.connect(self.job_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.setProgram(str(ROOT/'.venv'/'Scripts'/'python.exe'))
        self.process.setArguments(['-u', '-m', 'watermark_remover.worker', str(self.job_file)])
        self.process.start()
        self.progress.setValue(0)
        self.status_title.setText('Starting full-quality preview' if preview else 'Starting native-resolution repair')
        self.status_detail.setText('Loading the temporal AI engine on your GPU…')
        self.open_result.hide()
        self.cancel_button.setEnabled(True)
        self.set_enabled()

    def read_progress(self):
        if not self.process:
            return
        self.stdout_buffer += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self.stdout_buffer:
            line, self.stdout_buffer = self.stdout_buffer.split('\n', 1)
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            self.status_title.setText(event.get('stage', 'Processing'))
            detail = event.get('detail', '')
            if 'eta' in event and event['eta'] > 1:
                detail += f" · About {int(event['eta'])//60}m {int(event['eta'])%60}s remaining for repair"
            self.status_detail.setText(detail)
            if 'progress' in event:
                self.progress.setValue(event['progress'])
            if event.get('error'):
                self.error_shown = True
                self.show_error(detail)
            if event.get('output'):
                self.last_output = event['output']

    def read_errors(self):
        if self.process:
            self.worker_log += bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace')

    def process_error(self, error):
        if self.process and error == QProcess.ProcessError.FailedToStart:
            self.show_error('The private Python runtime could not start. Run Setup.ps1 from this application folder.')
            self.process = None
            self.set_enabled()

    def job_finished(self, code, status):
        process = self.process
        self.read_progress()
        self.read_errors()
        self.process = None
        if process:
            process.deleteLater()
        if self.worker_log and self.job_file:
            self.job_file.with_suffix('.log').write_text(self.worker_log, encoding='utf-8')
        job = self.current_job
        if code == 0 and job and Path(job.output).exists():
            self.last_output = job.output
            self.open_result.show()
            self.progress.setValue(100)
            if job.preview_start is not None:
                self.reader.preview_info = probe(job.output)
                self.reader.preview_start = job.preview_start
                self.preview = job
                self.compare_check.setEnabled(True)
                self.compare_check.setChecked(True)
                self.preview_note.setText(f'Preview: {timestamp(job.preview_start)}–{timestamp(min(self.info.duration, job.preview_start+job.preview_duration))}')
                self.seek(job.preview_start)
                self.status_title.setText('Preview ready · drag the comparison divider')
                self.status_detail.setText('Inspect edges and motion. Preview uses the same source-scale AI settings as export.')
            else:
                self.status_title.setText('Your repaired video is ready')
                self.status_detail.setText(job.output)
                self.output.setText(self.default_output())
        elif code == 2:
            self.status_title.setText('Cancelled · completed chunks saved')
            self.status_detail.setText('Start with the same source and repair settings to resume.')
        elif not self.error_shown:
            self.status_title.setText('Repair stopped')
            self.show_error(self.worker_log[-2000:] or 'The repair process exited unexpectedly. See workspace/requests for its log.')
        self.set_enabled()

    def cancel_job(self):
        if self.process and self.job_file:
            self.job_file.with_suffix('.cancel').touch()
            self.status_title.setText('Finishing the current AI operation…')
            self.status_detail.setText('Cancellation keeps completed chunks available for resume.')
            self.cancel_button.setEnabled(False)

    def save_project(self):
        if not self.info:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Save selections', str(Path(self.info.path).with_suffix('.clearframe.json')), 'Clearframe project (*.json)')
        if path:
            Path(path).write_text(json.dumps(self.make_job().to_dict(), indent=2), encoding='utf-8')

    def open_project(self):
        if self.process:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Open selections', str(ROOT), 'Clearframe project (*.json)')
        if path:
            try:
                job = Job.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))
                self.load_video(job.source)
                if not self.info or self.info.path != str(Path(job.source).resolve()):
                    return
                job.validate(self.info.width, self.info.height)
                self.regions = job.regions
                self.canvas.regions = self.regions
                self.margin.setValue(job.margin)
                self.context.setValue(job.context)
                self.window.setValue(job.window)
                self.precision.setCurrentIndex(0 if job.precision == 'fp16' else 1)
                self.seamless.setChecked(job.seamless)
                self.save_report.setChecked(job.save_report)
                self.export_format.setCurrentIndex(0 if job.export == 'lossless' else 1)
                self.output.setText(job.output if not Path(job.output).exists() else self.default_output())
                self.refresh_regions(0)
                self.set_enabled()
            except Exception as error:
                self.show_error(str(error))

    def open_result_file(self):
        if self.last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output))

    def open_output_folder(self):
        path = Path(self.last_output).parent if self.last_output else Path(self.output.text()).parent if self.output.text() else ROOT
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def show_error(self, text):
        self.status_detail.setText(text[:300])
        QMessageBox.warning(self, 'Clearframe', text)

    def about(self):
        QMessageBox.information(self, 'About Clearframe',
            'Clearframe 1.1.1\n\nLocal temporal video inpainting powered by ProPainter.\n'
            'Draw a rectangle, preview, and export at the source dimensions.\n\n'
            'Lossless MKV avoids additional compression loss. MP4 is a lossy delivery format. '
            'Hidden details are reconstructed and cannot be guaranteed to match the original. '
            'This version supports progressive 8-bit SDR footage; HDR is not silently converted.\n\n'
            'ProPainter is provided under S-Lab License 1.0 for noncommercial use. '
            'See THIRD_PARTY_NOTICES.md for credits and licenses.')

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.process:
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.load_video(urls[0].toLocalFile())

    def closeEvent(self, event):
        if self.process:
            self.cancel_job()
            self.status_detail.setText('Wait for cancellation to finish, then close the application.')
            event.ignore()
            return
        if self.reader:
            self.reader.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    # Explicit registration also supports Qt's offscreen renderer for UI checks.
    for font in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'seguisym.ttf'):
        path = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setApplicationName('Clearframe')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = MainWindow(sys.argv[1] if len(sys.argv) > 1 else None)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
