import json
from collections.abc import Sequence
from dataclasses import replace
from shutil import copytree, ignore_patterns

import httpx
import pytest

from reposherlock.benchmarking.dataset import DEFAULT_DATASET, DEFAULT_FIXTURES, load_dataset, safe_path
from reposherlock.benchmarking.metrics import compare
from reposherlock.benchmarking.mock import ScriptedProvider
from reposherlock.benchmarking.reporting import apply_review, summary
from reposherlock.benchmarking.runner import Options, run
from reposherlock.cli import main
from reposherlock.investigation.context import ContextLimits
from reposherlock.investigation.provider import ProviderError, TokenUsage
from reposherlock.semantic.indexing import build_semantic_index
from test_gemini_provider import FAKE_KEY, _error, _plan_response, stub_http


class FixtureEmbeddings:
    identity = "benchmark-test-embeddings"
    dimension = 3

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] if "checkout" in text.casefold() else [0.0, 1.0, 0.0]
                for text in texts]


@pytest.fixture
def options(tmp_path):
    return Options(DEFAULT_DATASET, DEFAULT_FIXTURES, tmp_path / "experiment")


def execute(options, **kwargs):
    return run(options, embeddings=FixtureEmbeddings(), announce=lambda _: None, **kwargs)


@pytest.mark.parametrize("path", ["../outside", "C:/repo", "/foo", ".", "a//b", "a\\b", "aux.txt", "a/x."])
def test_dataset_paths_are_canonical(path):
    with pytest.raises(ValueError, match="relative fixture path"):
        safe_path(path)


@pytest.mark.parametrize("kind", ["duplicate", "missing", "unsupported", "range", "unknown"])
def test_dataset_rejects_invalid_labels(tmp_path, kind):
    dataset = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if kind == "duplicate":
        cases.append(cases[0])
    elif kind == "missing":
        cases[0].pop("diagnosis")
    elif kind == "unsupported":
        cases[-1]["defect"] = cases[0]["defect"]
    elif kind == "range":
        cases[0]["defect"]["end_line"] = 1
    else:
        dataset["unrecognized"] = True
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid benchmark dataset"):
        load_dataset(path)


def test_compiled_labels_and_paired_unsupported_metrics(options):
    cases = load_dataset(options.dataset).cases
    assert len(cases) == 9
    assert len({case.category for case in cases}) == 9
    assert sum(case.cross_file for case in cases) == 2
    state = execute(options)
    assert len(state["rows"]) == 18 and len(state["snapshots"]) == 4
    for case in cases:
        first, second = [row for row in state["rows"] if row["case_id"] == case.id]
        assert first["snapshot"] == second["snapshot"]
        assert first["retrieved_sources"] == second["retrieved_sources"]
        assert first["initial_evidence"] == second["initial_evidence"]
        for row in (first, second):
            metrics = row["metrics"]
            assert row["provider"] == "mock" and metrics["usage_kind"] == "simulated"
            assert metrics["diagnosis"] is None and metrics["unsupported_claims"] is None
            assert metrics["physical_model_attempts"] is None and metrics["invalid_citations"] == 0
            if case.defect is None:
                assert metrics["unsupported_abstained"] is True
                assert metrics["file_localized"] is None
    assert state["requests_used"] == 0
    raw = (options.output / "results.json").read_text(encoding="utf-8")
    assert '"snippet"' not in raw and '"quote"' not in raw
    assert (options.output / "review-template.json").is_file()
    assert (options.output / "review.md").is_file()
    assert summary(state) == (options.output / "summary.md").read_text(encoding="utf-8")


def test_ground_truth_must_match_an_indexed_symbol_and_full_range(options):
    dataset = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    dataset["cases"][0]["defect"]["end_line"] = 900
    path = options.output.parent / "incorrect.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    with pytest.raises(ValueError, match="ground truth is missing"):
        execute(replace(options, dataset=path, max_cases=1))
    assert not json.loads((options.output / "results.json").read_text())["rows"]


def test_paired_cross_file_gain_and_real_source_range_checks(options):
    options = replace(options, case_ids=("shipping-threshold",), context=ContextLimits(max_sources=1))
    state = execute(options)
    single, agent = state["rows"]
    assert single["initial_evidence"] == agent["initial_evidence"]
    assert single["metrics"]["recall_at_k"] == agent["metrics"]["recall_at_k"] == 0.5
    assert single["metrics"]["file_localized"] is False
    assert agent["metrics"]["file_localized"] is True
    assert agent["metrics"]["faulty_function_identified"] is True
    assert agent["metrics"]["cross_file_gain"] is True
    assert agent["metrics"]["new_evidence_items"] == 1
    assert agent["metrics"]["new_relevant_locations"][0]["path"] == "rates.py"
    assert compare(state["rows"], state["cases"], "faulty_function_identified")["agentic_only"] == [
        "shipping-threshold",
    ]
    defect = load_dataset(options.dataset).cases[1].defect
    citation = agent["report"]["hypotheses"][0]["citations"][-1]
    assert defect.covers(citation, require_symbol=True)
    assert defect.covers({**citation, "symbol_name": "unrelated"})
    assert not defect.covers({**citation, "symbol_name": "unrelated"}, require_symbol=True)
    assert not defect.covers({**citation, "start_line": defect.start_line + 1})


@pytest.mark.parametrize("strategy", ["single", "agentic"])
def test_invalid_citations_are_counted_and_output_rejected(options, strategy):
    class BadCitations(ScriptedProvider):
        def generate(self, payload, **kwargs):
            reply = super().generate(payload, **kwargs)
            document = json.loads(reply.text)
            citations = document["hypotheses"][0]["citations"]
            citations.extend([{**citations[0], "source_id": "unknown"},
                              {**citations[0], "quote": "a fabricated quote"}])
            return replace(reply, text=json.dumps(document))

    [row] = execute(replace(options, max_cases=1, strategy=strategy), mock_factory=BadCitations)["rows"]
    assert row["status"] == "failed" and row["stop_reason"] == "invalid_report"
    assert row["report"] is None and row["metrics"]["file_localized"] is None
    assert row["metrics"]["invalid_citations"] == 2
    assert row["metrics"]["valid_citation_rate"] == pytest.approx(1 / 3)


def test_failure_is_not_abstention_and_resume_reuses_completed_rows(options):
    class FailedPlanner(ScriptedProvider):
        def plan(self, payload, history, *, allow_remote_code):
            raise ProviderError("unexpected failure, private data", usage=TokenUsage(17))

    options = replace(options, case_ids=("receipt-missing-input",))
    state = execute(options, mock_factory=FailedPlanner)
    single, failure = state["rows"]
    assert failure["metrics"]["unsupported_abstained"] is None
    assert failure["metrics"]["token_usage"]["input"]["tokens"] == 17
    assert failure["metrics"]["token_usage"]["output"]["status"] == "unavailable"
    assert "private data" not in (options.output / "results.json").read_text()
    with pytest.raises(FileExistsError):
        execute(options)
    resumed = execute(replace(options, resume=True))
    assert resumed["rows"][0]["trace"] == json.loads(json.dumps(single["trace"]))
    assert resumed["rows"][1]["metrics"]["unsupported_abstained"] is True
    assert len(resumed["history"]) == 1 and resumed["history"][0]["status"] == "failed"
    assert resumed["setup_ms"] == state["setup_ms"]


def test_partial_token_aggregation(options):
    class PartialUsage(ScriptedProvider):
        def plan(self, payload, history, *, allow_remote_code):
            turn = super().plan(payload, history, allow_remote_code=allow_remote_code)
            return replace(turn, usage=TokenUsage(input_tokens=11))

    state = execute(replace(options, max_cases=1, strategy="agentic"), mock_factory=PartialUsage)
    usage = state["rows"][0]["metrics"]["token_usage"]
    assert usage["request_count"] == 2 and usage["input"]["status"] == "complete"
    assert usage["output"]["status"] == "partial" and usage["total"]["reported_requests"] == 1


def test_parseable_report_rejected_for_execution_claim_keeps_citation_counts(options):
    class FalselyVerified(ScriptedProvider):
        def generate(self, payload, **kwargs):
            reply = super().generate(payload, **kwargs)
            document = json.loads(reply.text)
            document["hypotheses"][0]["reasoning"] = "We ran the test."
            return replace(reply, text=json.dumps(document))

    [row] = execute(replace(options, max_cases=1, strategy="single"), mock_factory=FalselyVerified)["rows"]
    assert row["report"] is None and row["stop_reason"] == "invalid_report"
    assert row["metrics"]["valid_citations"] == 1 and row["metrics"]["invalid_citations"] == 0


def test_interrupted_investigation_is_resumable(options):
    class Interrupted(ScriptedProvider):
        def generate(self, payload, **kwargs):
            raise KeyboardInterrupt

    options = replace(options, max_cases=1, strategy="single")
    with pytest.raises(KeyboardInterrupt):
        execute(options, mock_factory=Interrupted)
    checkpoint = json.loads((options.output / "results.json").read_text())
    assert checkpoint["active"]["case_id"] == "receipt-quantity" and not checkpoint["rows"]
    finished = execute(replace(options, resume=True))
    assert finished["rows"][0]["status"] == "complete" and finished["active"] is None
    assert finished["history"][0]["status"] == "interrupted"


@pytest.mark.parametrize("target", ["original", "staged", "config"])
def test_resume_rejects_changed_inputs(options, tmp_path, target):
    root = tmp_path / "fixtures"
    copytree(DEFAULT_FIXTURES / "buggy_receipts", root / "buggy_receipts",
             ignore=ignore_patterns(".reposherlock", "__pycache__"))
    options = replace(options, fixtures=root, max_cases=1)
    execute(options)
    resumed = replace(options, resume=True)
    if target == "original":
        path = root / "buggy_receipts" / "receipts.py"
    elif target == "staged":
        path = options.output / "snapshots" / "buggy_receipts" / "receipts.py"
    else:
        with pytest.raises(ValueError, match="configuration changed"):
            execute(replace(resumed, context=ContextLimits(max_sources=2)))
        return
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n# changed\n")
    with pytest.raises(ValueError, match="changed|stale"):
        execute(resumed)


def test_live_permission_is_checked_before_configuration_or_disk(options, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as error:
        main(["benchmark", "--provider", "gemini", "--output-dir", str(options.output)])
    assert error.value.code == 1 and "--allow-remote-code" in capsys.readouterr().err
    assert not options.output.exists()
    with pytest.raises(ValueError, match="maximum case count"):
        execute(replace(options, max_cases=0))


def test_benchmark_output_cannot_be_placed_inside_the_fixture_tree(options):
    with pytest.raises(ValueError, match="outside the original fixtures"):
        execute(replace(options, output=options.fixtures / "accidental-run", max_cases=1))


def test_live_retry_budget_and_resume_using_stub_http(options, monkeypatch, stub_http):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    options = replace(options, provider="gemini", max_cases=1, request_budget=2, allow_remote_code=True,
                      strategy="single")
    errors = iter((_error(429), _error(503)))
    requests, _ = stub_http(lambda _: next(errors))
    state = execute(options)
    [row] = state["rows"]
    assert len(requests) == state["requests_used"] == 2
    assert row["status"] == "budget_exhausted" and row["metrics"]["physical_model_attempts"] == 2
    assert state["expected_uninterrupted_physical_upper_bound"] == 3
    assert row["metrics"]["usage_kind"] == "unavailable"
    assert row["provider"] == "gemini" and row["report"] is None

    def generated(request):
        outbound = json.loads(request.content)
        payload = outbound["contents"][0]["parts"][0]["text"]
        assert set(json.loads(payload)) == {"issue", "sources"}
        assert "ground truth" not in payload and "inclusion rationale" not in payload
        generated = ScriptedProvider().generate(payload, instruction="", schema={}, allow_remote_code=True)
        return _plan_response([{"text": generated.text}])

    requests, _ = stub_http(generated)
    resumed = execute(replace(options, resume=True, request_budget=3))
    [row] = resumed["rows"]
    assert len(requests) == 1 and resumed["requests_used"] == 3
    assert row["status"] == "complete" and row["metrics"]["usage_kind"] == "provider_reported"
    assert resumed["history"][0]["status"] == "budget_exhausted"
    raw = (options.output / "results.json").read_text()
    assert FAKE_KEY not in raw and '"snippet"' not in raw


def test_live_timeout_persists_failure_and_does_not_fabricate_abstention(options, monkeypatch, stub_http):
    def timeout(request: httpx.Request):
        raise httpx.ReadTimeout("sensitive details", request=request)

    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    requests, _ = stub_http(timeout)
    options = replace(options, provider="gemini", allow_remote_code=True, strategy="agentic",
                      case_ids=("receipt-missing-input",), request_budget=1)
    state = execute(options)
    [row] = state["rows"]
    assert len(requests) == 1 and state["requests_used"] == 1
    assert row["status"] == "failed" and row["stop_reason"] == "planner_provider_failure"
    assert row["metrics"]["unsupported_abstained"] is None


@pytest.mark.parametrize("strategy", ["single", "agentic"])
@pytest.mark.parametrize("changed", [False, True])
def test_live_benchmark_guards_snapshot_before_reserving_retry(options, monkeypatch, stub_http, strategy, changed):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    options = replace(options, provider="gemini", allow_remote_code=True, strategy=strategy,
                      case_ids=("receipt-quantity",), request_budget=3)
    reports = 0

    def handler(request):
        nonlocal reports
        outbound = json.loads(request.content)
        if "tools" in outbound:
            return _plan_response([{"text": "READY"}])
        reports += 1
        if reports == 1:
            if changed:
                path = options.output / "snapshots" / "buggy_receipts" / "receipts.py"
                with path.open("a", encoding="utf-8") as source:
                    source.write("\n# changed benchmark snapshot after first report attempt\n")
                build_semantic_index(path.parent, embeddings=FixtureEmbeddings())
            return _error(503)
        payload = outbound["contents"][0]["parts"][0]["text"]
        reply = ScriptedProvider().generate(payload, instruction="", schema={}, allow_remote_code=True)
        return _plan_response([{"text": reply.text}])

    traffic, waits = stub_http(handler)
    state = execute(options)
    [row] = state["rows"]
    assert waits == [0.25]
    assert reports == (1 if changed else 2)
    assert len(traffic) == reports + (strategy == "agentic")
    assert state["requests_used"] == row["metrics"]["physical_model_attempts"] == len(traffic)
    usage = row["metrics"]["token_usage"]["total"]
    if changed:
        assert row["status"] == "failed" and row["stop_reason"] == "snapshot_changed"
        assert row["report"] is None
        assert usage["tokens"] == (None if strategy == "single" else 12)
    else:
        assert row["status"] == "complete" and row["report"]["status"] == "hypotheses"
        assert usage["tokens"] == (12 if strategy == "single" else 24)


def test_human_review_is_bound_to_results_and_keeps_raw_data_unchanged(options):
    state = execute(replace(options, max_cases=1))
    raw = (options.output / "results.json").read_bytes()
    path = options.output / "completed-review.json"
    template = json.loads((options.output / "review-template.json").read_text())
    template["rows"][0].update(diagnosis="Correct", fix="Partially correct",
                               regression="Incorrect", unsupported_claims=2, notes="Reviewer checked both claims.")
    template["rows"][1]["diagnosis"] = "Incorrect"
    path.write_text(json.dumps(template), encoding="utf-8")
    destination = options.output / "adjudicated.md"
    apply_review(options.output, path, destination)
    text = destination.read_text(encoding="utf-8").split("## Paired human diagnosis judgments")[1]
    assert "Single only — receipt-quantity" in text
    assert "Partially correct" in text and "Reviewer checked both claims." in text
    assert raw == (options.output / "results.json").read_bytes()
    assert summary(state) == (options.output / "summary.md").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        apply_review(options.output, path, destination)
    template["results_sha256"] = "different"
    path.write_text(json.dumps(template), encoding="utf-8")
    with pytest.raises(ValueError, match="different version"):
        apply_review(options.output, path, destination)
