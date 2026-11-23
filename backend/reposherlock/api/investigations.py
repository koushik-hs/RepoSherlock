"""Bind explicit approval to the verified snapshot and exact initial disclosure."""

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from reposherlock.api.errors import ApiError, repository_busy, snapshot_changed
from reposherlock.api.jobs import InvestigationOutput
from reposherlock.api.repositories import RegisteredRepository
from reposherlock.api.schemas import (
    ApprovalRequest, InvestigationConfig, InvestigationMetrics, InvestigationResponse, PreviewRequest,
    PreviewResponse, Problem, Snapshot, Strategy, TraceResponse,
)
from reposherlock.api.workspace import Workspace
from reposherlock.investigation.agent import investigate_agentic
from reposherlock.investigation.agent_provider import GeminiAgentProvider
from reposherlock.investigation.provider import GeminiProvider, ProviderError, configured_model
from reposherlock.investigation.report import InvalidInvestigation, InvestigationDocument
from reposherlock.investigation.service import SnapshotChanged, context_digest, investigate, verify_source_snapshot
from reposherlock.investigation.trace import InvestigationTrace
from reposherlock.retrieval.models import EvidenceDiagnostics

MAX_PREVIEWS = 64
FAILURE_MESSAGES = {
    "provider_auth": "Gemini rejected the server credential or access. Check the key and model permissions.",
    "provider_failure": "Gemini could not complete the request. Check configuration and try again.",
    "provider_quota": "Gemini rate or quota limits were reached.",
    "provider_unavailable": "Gemini was temporarily unavailable.",
    "provider_timeout": "The Gemini request timed out.",
    "invalid_report": "Gemini returned a report that failed local validation.",
    "invalid_planning_response": "Gemini returned an invalid planning response.",
    "snapshot_changed": "The repository or evidence changed. Create a new preview.",
}


@dataclass(frozen=True)
class Preview:
    repository: RegisteredRepository
    snapshot: Snapshot
    issue: str
    issue_summary: str
    strategy: Strategy
    configuration: InvestigationConfig
    model: str
    context_sha256: str
    expires: float


def preview_gone() -> ApiError:
    return ApiError(410, "preview_unavailable", "The preview expired, was consumed, or is unknown. Create a new preview.")


class Investigations:
    def __init__(self, workspace: Workspace, ttl: int,
                 provider_factory: Callable[[], GeminiProvider]) -> None:
        self.workspace = workspace
        self.ttl = ttl
        self.provider_factory = provider_factory
        self._previews: dict[str, Preview] = {}
        self._lock = Lock()

    def _simulate_or_run(self, record: Preview, *, dry_run: bool, provider: GeminiProvider | None = None):
        root = self.workspace.repositories.checked_path(record.repository)
        common = dict(dry_run=dry_run, allow_remote_code=not dry_run, embeddings=self.workspace.embeddings,
                      expected_snapshot=record.snapshot.digests,
                      expected_context=None if dry_run else record.context_sha256,
                      resolve_repository=lambda: self._request_repository(record))
        if record.strategy == "single":
            return investigate(root, record.issue, limits=record.configuration.context_limits(),
                               provider=provider, **common)
        return investigate_agentic(
            root, record.issue, context_limits=record.configuration.context_limits(),
            limits=record.configuration.agent_limits(),
            provider=GeminiAgentProvider(provider) if provider is not None else None, **common,
        )

    def preview(self, request: PreviewRequest) -> PreviewResponse:
        repo = self.workspace.repositories.get(request.repository_id)
        privacy = self.workspace.privacy
        model = configured_model()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", model) or privacy.text(model) != model:
            raise ApiError(503, "model_configuration_invalid", "Configure a valid server-side Gemini model ID.")
        privacy.reject_credentials(request.issue)
        issue = privacy.text(request.issue)
        config = request.configuration.effective(request.strategy)
        with self.workspace.lane():
            snapshot = self.workspace.require_snapshot(repo)
            record = Preview(repo, snapshot, issue, "", request.strategy, config, model, "", 0.0)
            try:
                result = self._simulate_or_run(record, dry_run=True)
            except ValueError as error:
                if str(error).startswith("The bug report exceeds the context budget."):
                    raise ApiError(422, "context_too_small", "The bug report exceeds the configured context size.") from None
                raise snapshot_changed() from None
            except (OSError, RuntimeError):
                raise ApiError(409, "preview_failed", "Preview could not read the current verified index.") from None

            context = result.context
            sources = [evidence.outbound() for evidence in context.sources]
            privacy.reject_credentials(context.issue, *(value for source in sources
                                                         for value in source.values() if isinstance(value, str)))
            expires_at = datetime.now(UTC) + timedelta(seconds=self.ttl)
            record = replace(record, context_sha256=context_digest(context), expires=monotonic() + self.ttl,
                             issue_summary=context.issue[:2000])
            with self._lock:
                self._previews = {key: preview for key, preview in self._previews.items()
                                  if preview.expires > monotonic()}
                if len(self._previews) >= MAX_PREVIEWS:
                    raise ApiError(429, "preview_capacity", "The local preview store is full. Retry after previews expire.")
                key = uuid4().hex
                self._previews[key] = record
        can_gather = request.strategy == "agentic"
        return PreviewResponse(
            preview_id=key, expires_at=expires_at, repository_id=repo.id, snapshot=snapshot,
            strategy=request.strategy, configuration=config, model=model, issue=context.issue,
            sources=sources, source_chars=sum(len(evidence.snippet) for evidence in context.sources),
            source_bytes=sum(len(evidence.snippet.encode("utf-8")) for evidence in context.sources),
            context_chars=len(context.payload), additional_disclosure_possible=can_gather,
            disclosure_notice=("Approval also permits additional redacted excerpts from this snapshot "
                               "during bounded agent tool lookups and later Gemini calls." if can_gather
                               else "Approval permits Gemini to receive this redacted issue and the excerpts shown."),
            diagnostics=privacy.data(asdict(result.diagnostics)),
        )

    def _assert_snapshot(self, record: Preview) -> None:
        try:
            root = self.workspace.repositories.checked_path(record.repository)
            verify_source_snapshot(root, record.repository.id, record.snapshot.digests)
        except (ApiError, OSError, ValueError) as error:
            if isinstance(error, ValueError) and str(error).startswith("Semantic index is busy"):
                raise repository_busy() from None
            raise snapshot_changed() from None

    def _request_repository(self, record: Preview) -> Path:
        try:
            return self.workspace.repositories.checked_path(record.repository)
        except ApiError:
            raise SnapshotChanged() from None

    def start(self, request: ApprovalRequest) -> str:
        if request.allow_remote_code is not True:
            raise ApiError(403, "remote_code_permission_required", "Explicitly approve remote-code disclosure for this investigation.")
        with self.workspace.lane(), self._lock:
            record = self._previews.get(request.preview_id)
            if record is None or record.expires <= monotonic():
                raise preview_gone()
            if request.strategy != record.strategy or request.configuration.effective(request.strategy) != record.configuration:
                raise ApiError(409, "preview_configuration_mismatch", "Approve the strategy and configuration shown in the preview.")
            self._assert_snapshot(record)
            try:
                base = self.provider_factory()
            except (ValueError, ProviderError):
                raise ApiError(503, "model_configuration_missing", "Configure the server-side Gemini credential and model.") from None
            if base.model != record.model or configured_model() != record.model:
                raise ApiError(409, "preview_model_changed", "The server model changed. Create a new preview.")
            if record.expires <= monotonic():
                raise preview_gone()

            def work(key: str) -> InvestigationOutput:
                with self.workspace.lane(wait=True):
                    return self._execute(key, record, base)

            key = self.workspace.jobs.submit(
                record.repository.id, "investigation", work,
                initial=lambda key: self._output(key, record),
            )
            del self._previews[request.preview_id]
            return key

    def _execute(self, key: str, record: Preview, base: GeminiProvider) -> InvestigationOutput:
        if record.expires <= monotonic():
            raise preview_gone()
        self._assert_snapshot(record)
        if configured_model() != record.model:
            raise ApiError(409, "preview_model_changed", "The server model changed. Create a new preview.")

        provider = replace(base, additional_secrets=self.workspace.privacy.secrets())
        try:
            result = self._simulate_or_run(record, dry_run=False, provider=provider)
            self._assert_snapshot(record)
        except ValueError:
            raise snapshot_changed() from None
        except (ProviderError, InvalidInvestigation) as error:
            trace = getattr(error, "trace", None)
            code = "invalid_report" if isinstance(error, InvalidInvestigation) else error.code or "provider_failure"
            return self._output(key, record, trace=trace, error=self._problem(code))
        trace = result.trace
        failure = None
        if trace and trace.stop_reason in (
            "planner_provider_failure", "report_provider_failure", "invalid_report", "invalid_planning_response",
        ):
            codes = [step.error_code for step in trace.model_requests if step.error_code]
            failure = self._problem(trace.stop_reason if trace.stop_reason.startswith("invalid_")
                                    else codes[-1] if codes else "provider_failure")
        return self._output(key, record, document=None if failure else result.document,
                            diagnostics=result.diagnostics, trace=trace, error=failure)

    @staticmethod
    def _problem(code: str) -> Problem:
        if code not in FAILURE_MESSAGES:
            code = "provider_failure"
        return Problem(code=code, message=FAILURE_MESSAGES[code])

    def _output(self, key: str, record: Preview, *, document: InvestigationDocument | None = None,
                diagnostics: EvidenceDiagnostics | None = None, trace: InvestigationTrace | None = None,
                error: Problem | None = None) -> InvestigationOutput:
        privacy = self.workspace.privacy
        metrics = None
        if trace is not None:
            valid = invalid = None
            if document is not None:
                valid = sum(len(item.citations) for item in (*document.observations, *document.hypotheses))
                invalid = 0
            elif trace.model_requests:
                final = trace.model_requests[-1]
                valid, invalid = final.valid_citations, final.invalid_citations
            metrics = InvestigationMetrics(
                duration_ms=trace.duration_ms, initial_retrieval_ms=trace.initial_retrieval_ms,
                retrieval_operations=trace.retrieval_operations, model_request_count=len(trace.model_requests),
                tool_call_count=len(trace.tools), usage_summary=trace.usage_summary,
                valid_citations=valid, invalid_citations=invalid,
            )
        response = InvestigationResponse(
            investigation_id=key, repository_id=record.repository.id, snapshot=record.snapshot,
            state="queued", issue_summary=privacy.text(document.problem_summary if document else record.issue_summary),
            strategy=record.strategy, model=privacy.text(record.model), error=error,
            report=privacy.data(document.model_dump()) if document else None,
            diagnostics=privacy.data(asdict(diagnostics)) if diagnostics else None,
            stop_reason=trace.stop_reason if trace else None, metrics=metrics,
        )
        return InvestigationOutput(
            response, TraceResponse(investigation_id=key, state="queued",
                                    trace=privacy.data(asdict(trace)) if trace else None),
        )
