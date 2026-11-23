"""Validate structured investigation claims against the outbound source registry."""

import re
from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator

from reposherlock.investigation.context import Evidence
from reposherlock.investigation.redaction import redact

Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
EXECUTION_CLAIM = re.compile(
    r"\b(?:i|we|reposherlock|the investigator)\s+(?:(?:have|has|successfully|actually)\s+)*"
    r"(?:ran|executed|reproduced|tested|verified|confirmed|applied)\b"
    r"|\b(?:bug|issue|failure|fix|patch|tests?|regression|root cause)\s+"
    r"(?:(?:has|have)\s+been|was|were)\s+(?:successfully\s+)?"
    r"(?:reproduced|fixed|applied|tested|executed|verified|confirmed)\b"
    r"|\btests?\s+(?:(?:all|now)\s+)*passed\b",
    re.IGNORECASE,
)


class InvalidInvestigation(RuntimeError):
    """The provider output failed shape or grounding checks."""

    valid_citations: int | None = None
    invalid_citations: int | None = None


class ResponseModel(BaseModel, extra="forbid", strict=True):
    pass


class Citation(ResponseModel):
    source_id: str
    file_path: str
    symbol_name: str | None
    symbol_type: str | None
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str | None = None


class Observation(ResponseModel):
    statement: Description
    citations: list[Citation] = Field(min_length=1, max_length=6)


class Hypothesis(ResponseModel):
    suspected_cause: Description
    reasoning: Description
    citations: list[Citation] = Field(min_length=1, max_length=6)
    suggested_fixes: list[Description] = Field(min_length=1, max_length=4)
    verification_tests: list[Description] = Field(min_length=1, max_length=4)


class InvestigationDocument(ResponseModel):
    status: Literal["hypotheses", "insufficient_evidence"]
    problem_summary: Description
    observations: list[Observation] = Field(max_length=6)
    hypotheses: list[Hypothesis] = Field(max_length=4)
    uncertainties: list[Description] = Field(max_length=6)
    missing_evidence: list[Description] = Field(max_length=6)

    @model_validator(mode="after")
    def _consistent_status(self) -> "InvestigationDocument":
        if self.status == "hypotheses" and not self.hypotheses:
            raise ValueError("Hypotheses status requires at least one hypothesis.")
        if self.status == "insufficient_evidence" and (self.hypotheses or not self.missing_evidence):
            raise ValueError("Insufficient evidence requires missing evidence and an empty hypothesis list.")
        return self


def provider_schema() -> dict:
    # Gemini's constrained decoder can reject deeply nested, count-bounded
    # arrays. Preserve shape remotely and enforce size bounds locally.
    def simplify(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: simplify(item) for key, item in value.items()
                if key not in {"minItems", "maxItems", "minLength", "maxLength", "minimum", "title"}
            }
        if isinstance(value, list):
            return [simplify(item) for item in value]
        return value

    schema = simplify(InvestigationDocument.model_json_schema())
    assert isinstance(schema, dict)
    return schema


def _canonicalize(citation: Citation, registry: dict[str, Evidence]) -> Citation:
    source = registry.get(citation.source_id)
    if source is None:
        raise InvalidInvestigation("The provider cited an unknown source ID.")
    if (
        citation.file_path != source.file_path
        or citation.symbol_name != source.symbol_name
        or citation.symbol_type != source.symbol_type
        or citation.start_line != source.start_line
        or citation.end_line != source.end_line
    ):
        raise InvalidInvestigation("A citation's path, symbol, or line range disagrees with the evidence registry.")
    if citation.quote is not None and (
        not citation.quote.strip() or citation.quote not in source.snippet
    ):
        raise InvalidInvestigation("A quoted excerpt does not match the selected redacted source.")
    return Citation(
        source_id=source.source_id, file_path=source.file_path,
        symbol_name=source.symbol_name, symbol_type=source.symbol_type,
        start_line=source.start_line, end_line=source.end_line, quote=citation.quote,
    )


def _text(value: str) -> str:
    if EXECUTION_CLAIM.search(value):
        raise InvalidInvestigation("The provider claimed execution or verification that was not performed.")
    return redact(value)


def validate_report(raw: str, sources: tuple[Evidence, ...]) -> InvestigationDocument:
    if len(raw) > 64_000:
        raise InvalidInvestigation("The provider response exceeded the report size limit.")
    try:
        document = InvestigationDocument.model_validate_json(raw)
    except ValidationError:
        raise InvalidInvestigation("The provider returned an invalid structured investigation report.") from None

    registry = {source.source_id: source for source in sources}
    valid = invalid = 0
    first_error = None
    citations = []
    for item in (*document.observations, *document.hypotheses):
        group = []
        citations.append(group)
        for citation in item.citations:
            try:
                group.append(_canonicalize(citation, registry))
            except InvalidInvestigation as error:
                first_error = first_error or error
                invalid += 1
            else:
                valid += 1
    if first_error is not None:
        first_error.valid_citations = valid
        first_error.invalid_citations = invalid
        raise first_error

    try:
        return _validated_text(document, iter(citations))
    except InvalidInvestigation as error:
        error.valid_citations = valid
        error.invalid_citations = 0
        raise


def _validated_text(document: InvestigationDocument, groups: Iterator[list[Citation]]) -> InvestigationDocument:
    observations = [
        item.model_copy(update={
            "statement": _text(item.statement),
            "citations": next(groups),
        })
        for item in document.observations
    ]
    hypotheses = [
        item.model_copy(update={
            "suspected_cause": _text(item.suspected_cause),
            "reasoning": _text(item.reasoning),
            "citations": next(groups),
            "suggested_fixes": [_text(value) for value in item.suggested_fixes],
            "verification_tests": [_text(value) for value in item.verification_tests],
        })
        for item in document.hypotheses
    ]
    return document.model_copy(update={
        "problem_summary": _text(document.problem_summary),
        "observations": observations,
        "hypotheses": hypotheses,
        "uncertainties": [_text(value) for value in document.uncertainties],
        "missing_evidence": [_text(value) for value in document.missing_evidence],
    })
