import pytest

from watermark_remover import pipeline


@pytest.fixture(autouse=True)
def keep_test_reports_in_scratch(tmp_path, monkeypatch):
    """Test exports must not populate the user's application log history."""
    monkeypatch.setattr(pipeline, 'REPORTS_DIR', tmp_path / 'app' / 'workspace' / 'logs')
