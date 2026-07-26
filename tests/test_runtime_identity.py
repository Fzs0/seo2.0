from __future__ import annotations

from app.core.runtime_identity import source_fingerprint


def test_source_fingerprint_changes_when_python_source_changes(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    source = app_dir / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    before = source_fingerprint(app_dir)

    source.write_text("VALUE = 2\n", encoding="utf-8")

    assert source_fingerprint(app_dir) != before
