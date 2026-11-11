import json
from pathlib import Path

from watermark_remover.domain import Job, Rect, Region
from watermark_remover import pipeline
from test_media_pipeline import ControlledEngine, fixture_video


def test_report_stays_internal_unless_export_requested(tmp_path, monkeypatch):
    input_folder = tmp_path / 'input'
    input_folder.mkdir()
    source, _ = fixture_video(input_folder, count=6)
    reports = tmp_path / 'app' / 'workspace' / 'logs'
    monkeypatch.setattr(pipeline, 'REPORTS_DIR', reports)
    output_folder = tmp_path / 'exports'
    job = Job(str(source), str(output_folder / 'clean.mkv'), [Region(Rect(40, 40, 30, 20))],
              cache_dir=str(tmp_path / 'cache'))
    pipeline.process(job, engine_factory=ControlledEngine)
    assert sorted(p.name for p in output_folder.iterdir()) == ['clean.mkv']
    first_report = next(reports.glob('*.report.json'))
    first_data = json.loads(first_report.read_text())
    assert first_data['status'] == 'complete'
    assert first_data['verified_frame_count'] == 6
    assert first_data['settings']['save_report'] is False

    # The export-only preference must not invalidate an expensive cached repair.
    cached_signature = pipeline.signature(job)
    job.output = str(output_folder / 'with-report.mkv')
    job.save_report = True
    assert pipeline.signature(job) == cached_signature

    class MustNotLoad:
        def __init__(self, *args):
            raise AssertionError('Report preference unnecessarily invalidated the repair cache.')

    pipeline.process(job, engine_factory=MustNotLoad)
    sidecar = Path(job.output + '.report.json')
    assert sidecar.exists()
    assert len(list(reports.glob('*.report.json'))) == 2
    exported_data = json.loads(sidecar.read_text())
    assert exported_data['settings']['save_report'] is True
    assert exported_data in [json.loads(p.read_text()) for p in reports.glob('*.report.json')]
    assert json.loads(first_report.read_text()) == first_data

    # A preview from the CLI must also keep its report out of the preview folder.
    job.output = str(tmp_path / 'previews' / 'preview.mkv')
    job.preview_start, job.preview_duration = 0.0, 0.1
    pipeline.process(job, engine_factory=ControlledEngine)
    assert sorted(p.name for p in Path(job.output).parent.iterdir()) == ['preview.mkv']
    assert len(list(reports.glob('*.report.json'))) == 3


def test_old_project_defaults_to_internal_reports(tmp_path):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'fingerprint')
    legacy_data = {'source': str(source), 'output': str(tmp_path / 'clean.mp4'),
                   'regions': [{'rect': {'x': 5, 'y': 5, 'w': 20, 'h': 20}}]}
    job = Job.from_dict(legacy_data)
    assert job.save_report is False
    assert Job.from_dict(job.to_dict()).save_report is False
    job.save_report = True
    assert Job.from_dict(job.to_dict()).save_report is True
