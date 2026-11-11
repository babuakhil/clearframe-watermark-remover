"""Exercise the GUI/worker preview and export path without desktop input."""
from pathlib import Path
import json
import os
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from watermark_remover.app import MainWindow, STYLE
from watermark_remover.media import probe


def main():
    output = ROOT/'workspace'/'ui-verification'
    output.mkdir(parents=True, exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output/'settings'))
    app = QApplication([])
    for font in ('segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf', 'seguisym.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = MainWindow()
    assert window.export_format.currentIndex() == 1, 'MP4 must be the normal export'
    assert window.crf.value() == 18
    assert window.seamless.isChecked()
    assert not window.save_report.isChecked()
    errors = []
    window.show_error = errors.append
    window.show()
    window.load_sample()
    window.cache.setText(str(output/'cache'))
    state = {'phase': 'loading'}
    started = time.monotonic()

    def tick():
        if errors:
            print('ERROR:', errors, flush=True)
            if window.process:
                window.cancel_job()
                return
            window.close()
            app.exit(1)
            return
        if time.monotonic()-started > 150:
            errors.append('UI verification timed out')
            return
        if state['phase'] == 'loading' and window.canvas.image:
            window.start_job(True)
            state['phase'] = 'preview'
        elif state['phase'] == 'preview' and window.process is None and window.canvas.after:
            assert window.compare_check.isChecked()
            window.canvas.split = 0.5
            assert not window.current_job.save_report
            assert not Path(window.current_job.output + '.report.json').exists()
            window.grab().save(str(output/'preview-window.png'))
            print('GUI preview + comparison passed', flush=True)
            window.export_format.setCurrentIndex(1)
            target = output/f'export-{uuid.uuid4().hex[:8]}.mp4'
            window.output.setText(str(target))
            state['export'] = target
            window.start_job(False)
            state['phase'] = 'export'
        elif state['phase'] == 'export' and window.process is None:
            target = state['export']
            assert target.exists(), 'GUI export is missing'
            info = probe(target)
            assert (info.width, info.height) == (640, 360)
            assert any(s['codec_type'] == 'audio' for s in info.streams)
            assert window.progress.value() == 100
            assert not target.with_suffix(target.suffix + '.report.json').exists()
            window.grab().save(str(output/'export-window.png'))
            (output/'result.json').write_text(json.dumps({'preview': 'passed', 'mp4_export': str(target),
                                                         'seconds': round(time.monotonic()-started, 2)}, indent=2))
            print('GUI MP4 export + original audio passed', flush=True)
            window.close()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
