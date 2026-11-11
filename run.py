from pathlib import Path
import sys
import traceback

if __name__ == '__main__':
    try:
        from watermark_remover.app import main
        raise SystemExit(main())
    except Exception:
        log = Path(__file__).parent / 'startup-error.log'
        log.write_text(traceback.format_exc(), encoding='utf-8')
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, 'Clearframe could not start', f'See {log}\n\n{traceback.format_exc()[-1500:]}')
        except Exception:
            pass
        raise
