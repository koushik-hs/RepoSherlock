"""Load benchmark cases independently of provider inputs."""

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator

from reposherlock.ingestion.scanner import is_link, validate_repository
from reposherlock.investigation.context import validate_issue
from reposherlock.investigation.redaction import redact

DEFAULT_DATASET = Path(__file__).with_name("cases.json")
DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not path.parts or path.is_absolute() or PureWindowsPath(value).drive
            or path.as_posix() != value or any(
                not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", part)
                or part.endswith(".") or PureWindowsPath(part).is_reserved()
                for part in path.parts
            )):
        raise ValueError("Expected a canonical, relative fixture path.")
    return value


class Label(BaseModel, extra="forbid", strict=True, frozen=True):
    path: str
    symbol: Description
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self):
        safe_path(self.path)
        if self.start_line > self.end_line:
            raise ValueError("Invalid ground truth line range.")
        return self

    def covers(self, excerpt: dict, *, require_symbol: bool = False) -> bool:
        if excerpt["file_path"] != self.path:
            return False
        if require_symbol and excerpt["symbol_name"] != self.symbol:
            return False
        return excerpt["start_line"] <= self.start_line and excerpt["end_line"] >= self.end_line


class Case(BaseModel, extra="forbid", strict=True):
    id: Identifier
    fixture: Identifier
    category: Literal["single_file", "cross_file", "boundary", "filtering", "cache_invalidation",
                      "misleading_symbol", "wrong_initial_locus", "insufficient_evidence", "unimplemented"]
    issue: str
    rationale: Description
    defect: Label | None = None
    support: list[Label] = Field(default_factory=list, max_length=8)
    diagnosis: Description | None = None
    fix: Description | None = None
    regression: Description | None = None
    cross_file: bool = False

    @model_validator(mode="after")
    def validate_case(self):
        safe_path(self.fixture)
        self.issue = validate_issue(self.issue)
        unsupported = self.category in {"insufficient_evidence", "unimplemented"}
        labels = [self.defect, self.diagnosis, self.fix, self.regression]
        if unsupported:
            if any(labels) or self.support or self.cross_file:
                raise ValueError("Unsupported cases cannot contain a labelled defect.")
        else:
            if not all(labels):
                raise ValueError("A positive case needs a defect, diagnosis, fix and regression.")
            assert self.defect is not None
            if len(set(self.support)) != len(self.support) or self.defect in self.support:
                raise ValueError("Duplicate ground truth locations.")
            if self.cross_file != any(label.path != self.defect.path for label in self.support):
                raise ValueError("Cross-file flag disagrees with the supporting locations.")
        return self

    @property
    def locations(self) -> list[Label]:
        return [self.defect, *self.support] if self.defect else []


class Dataset(BaseModel, extra="forbid", strict=True):
    version: Literal[1]
    cases: list[Case] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate benchmark case ID.")
        return self


def load_dataset(path: Path) -> Dataset:
    with path.open("rb") as stream:
        data = stream.read(512_001)
    if len(data) > 512_000:
        raise ValueError("The benchmark dataset exceeds 512 KB.")
    try:
        return Dataset.model_validate_json(data)
    except ValidationError as error:
        # Do not repeat custom dataset values in error output.
        fields = ", ".join(".".join(map(str, item["loc"])) or "dataset" for item in error.errors())
        raise ValueError(f"Invalid benchmark dataset fields: {fields}.") from None


def resolve_fixture(root: Path, name: str) -> Path:
    safe_path(name)
    candidate = root / name
    if is_link(candidate.lstat()) or not candidate.resolve().is_relative_to(root):
        raise ValueError("Fixture escapes the benchmark fixture root.")
    return validate_repository(candidate)


def review_truth(case: Case) -> dict:
    record = case.model_dump(exclude={"issue"})
    for field in ("rationale", "diagnosis", "fix", "regression"):
        if record[field] is not None:
            record[field] = redact(record[field])
    return record
