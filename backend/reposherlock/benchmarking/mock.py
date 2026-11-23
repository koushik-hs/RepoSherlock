"""Scripted model double for wiring, evidence and accounting checks."""

import json

from reposherlock.investigation.agent_provider import PlanningTurn, ToolRequest
from reposherlock.investigation.provider import ProviderReply, TokenUsage

MODEL = "scripted-investigator-v1"
CITATION_FIELDS = ("source_id", "file_path", "symbol_name", "symbol_type", "start_line", "end_line")

# Match a symptom and require the listed implementation fragments in the supplied context.
RULES = (
    ("multiple units", ("sum(item.unit_price_cents for item",),
     "The receipt adds each unit price once and omits quantities.",
     "Multiply unit price by quantity before adding each line.",
     "Combine a multi-unit line and a single-unit line and assert the full price."),
    ("exactly $50", ("return standard_fee(subtotal_cents)", "subtotal_cents > FREE_SHIPPING_AT_CENTS"),
     "Checkout routes the calculation to a tariff whose strict threshold excludes equality.",
     "Include equality in the free-shipping tariff comparison.",
     "Check the checkout fee one cent below, at, and one cent above the threshold."),
    ("posted slot capacity", ("return parcels < capacity",),
     "The capacity predicate excludes an exact fit.",
     "Allow the parcel count to equal the loading slot capacity.",
     "Check one below capacity, exactly at capacity, and one above."),
    ("default storefront", ('if product["archived"]',),
     "The default filter selects archived products.",
     "Keep unarchived products in the default branch and preserve the opt-in.",
     "Check default and opt-in listings against mixed product states."),
    ("admin price change", ("return self.cache[sku]", "self.prices[sku] = cents"),
     "The writer changes the backing price while the reader still uses its cached entry.",
     "Invalidate or refresh the cached SKU when its price changes.",
     "Prime the cache, update the same SKU, and assert the next read sees the new price."),
    ("second page", ("return products[offset:limit]",),
     "The slice treats the page size as an absolute endpoint.",
     "Set the slice endpoint to offset plus the page size.",
     "Check a page starting at a nonzero offset where sufficient products remain."),
    ("two-hour route", ("return minutes_from_hours(hours)", "return hours * 100"),
     "The display delegates to a helper that scales hours by 100.",
     "Convert hours to minutes with a factor of 60 in the helper.",
     "Assert that two hours display as 120 minutes and half an hour as 30."),
)

DELEGATES = (
    ("return standard_fee(subtotal_cents)", "subtotal_cents > FREE_SHIPPING_AT_CENTS", "standard_fee"),
    ("return minutes_from_hours(hours)", "return hours * 100", "minutes_from_hours"),
)


def _source(sources: list[dict], fragment: str) -> dict | None:
    return next((entry for entry in sources if fragment in entry["snippet"]), None)


def _usage(prompt: str, response: str) -> TokenUsage:
    inputs = (len(prompt) + 3) // 4
    outputs = (len(response) + 3) // 4
    return TokenUsage(inputs, outputs, inputs + outputs)


class ScriptedProvider:
    model = MODEL

    def plan(self, payload, history, *, allow_remote_code):
        sources = json.loads(payload)["sources"]
        sources += [source for exchange in history for result in exchange.results
                    for source in result.get("sources", ())]
        calls = ()
        if not history:
            for caller, implementation, symbol in DELEGATES:
                if _source(sources, caller) and not _source(sources, implementation):
                    calls = (ToolRequest("find_symbol", {"symbol": symbol}),)
                    break
        replay = json.dumps([exchange.results for exchange in history])
        return PlanningTurn(calls, usage=_usage(payload + replay, str(calls) if calls else "READY"))

    def generate(self, payload, *, instruction, schema, allow_remote_code):
        context = json.loads(payload)
        issue = context["issue"]
        hypotheses = []
        for symptom, fragments, cause, fix, test in RULES:
            if symptom not in issue:
                continue
            matched = [_source(context["sources"], fragment) for fragment in fragments]
            if all(matched):
                citations = [
                    {field: source[field] for field in CITATION_FIELDS}
                    for source in {source["source_id"]: source for source in matched}.values()
                ]
                hypotheses.append({
                    "suspected_cause": cause, "reasoning": cause, "citations": citations,
                    "suggested_fixes": [fix], "verification_tests": [test],
                })
            break
        reply = json.dumps({
            "status": "hypotheses" if hypotheses else "insufficient_evidence",
            "problem_summary": issue[:2000], "observations": [], "hypotheses": hypotheses,
            "uncertainties": [],
            "missing_evidence": [] if hypotheses else ["The excerpts do not establish the reported behavior."],
        })
        return ProviderReply(reply, "mock", self.model, _usage(payload, reply))
