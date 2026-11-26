"""Release exports omit local data and reject known credential values."""

from pathlib import Path

import pytest

from scripts import prepare_release


def test_export_excludes_private_data_and_generated_files(tmp_path, monkeypatch):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/app.py").write_text("print('example')", encoding="utf-8")
    (tmp_path / "README.md").write_text("Example", encoding="utf-8")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=synthetic-secret-for-test", encoding="utf-8")
    (tmp_path / "research_navigator.db").write_bytes(b"private fixture")
    (tmp_path / "backend/private.db").write_bytes(b"private fixture")
    (tmp_path / "backend/__pycache__").mkdir()
    (tmp_path / "backend/__pycache__/cached.py").write_text("cached", encoding="utf-8")
    (tmp_path / "evaluation/published").mkdir(parents=True)
    (tmp_path / "evaluation/published/summary.json").write_text('{"publications": 1}', encoding="utf-8")
    (tmp_path / "evaluation/reports").mkdir()
    (tmp_path / "evaluation/reports/private.json").write_text("private fixture", encoding="utf-8")
    (tmp_path / "docs/audits/2026-11-25").mkdir(parents=True)
    (tmp_path / "docs/audits/2026-11-25/GAP_ANALYSIS.md").write_text("Archived findings", encoding="utf-8")
    (tmp_path / "evaluation/HUMAN_REVIEW.md").write_text("Unfilled review rubric", encoding="utf-8")
    monkeypatch.setattr(prepare_release, "ROOT", tmp_path)
    result = prepare_release.export_release()
    destination = Path(result["directory"])
    files = {str(path.relative_to(destination)).replace("\\", "/") for path in destination.rglob("*") if path.is_file()}
    assert files == {
        "README.md",
        "backend/app.py",
        "evaluation/published/summary.json",
        "docs/audits/2026-11-25/GAP_ANALYSIS.md",
        "evaluation/HUMAN_REVIEW.md",
    }
    assert result["credential_value_scan"] == "passed"


def test_embedded_credential_blocks_export(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=synthetic-sensitive-secret", encoding="utf-8")
    (tmp_path / "README.md").write_text("synthetic-sensitive-secret", encoding="utf-8")
    monkeypatch.setattr(prepare_release, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="Credential scan failed in README.md"):
        prepare_release.export_release()
    assert not (tmp_path / ".runtime").exists()
