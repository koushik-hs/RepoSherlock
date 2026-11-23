from pathlib import Path
from shutil import copytree, ignore_patterns

import pytest


@pytest.fixture
def sample_repository(tmp_path: Path) -> Path:
    return copytree(
        Path(__file__).parent / "fixtures" / "sample_repo", tmp_path / "repo",
        ignore=ignore_patterns(".reposherlock", "__pycache__"),
    )
