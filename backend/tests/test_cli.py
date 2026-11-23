from pathlib import Path

import pytest

from reposherlock.cli import main


def test_cli_index(sample_repository: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["index", "--path", str(sample_repository), "--max-files", "2"]) == 0
    captured = capsys.readouterr()
    assert "Indexed 2 files into 2 chunks." in captured.out
    assert "Scanned 4 files and skipped 2." in captured.out
    assert "manifest.json" in captured.out
    assert not captured.err


def test_cli_invalid_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["index", "--path", str(tmp_path / "missing")])
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert "Repository path does not exist" in captured.err
    assert "Traceback" not in captured.err
