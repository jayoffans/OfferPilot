"""Repeatable, free, synthetic-only field and evidence evaluation of V2."""

import argparse
import json
from pathlib import Path

from offerpilot_api.resume_ingestion.evidence import build_text_layout
from offerpilot_api.resume_ingestion.extractor import ExtractionDiagnostics, ExtractionError
from offerpilot_api.resume_ingestion.extractor_v2 import extract_student_profile_v2
from offerpilot_api.resume_ingestion.synthetic_fixtures import fixture_cases, fixture_response

try:
    from .resume_extraction_v2_gold import golden_case
except ImportError:
    from resume_extraction_v2_gold import golden_case


class FixtureResponseProvider:
    """Only simulates supplied fixed responses; has no network capability."""
    def __init__(self, case):
        self.response = fixture_response(case)

    def generate_json(self, *, system_prompt, user_prompt):
        return self.response


def candidate_key(category, value):
    identity = value.get({"education": "school", "skill": "name", "project": "name", "experience": "organization"}[category])
    return f"{category}:{identity}"


def flatten(candidates):
    result = {}
    for item in candidates:
        key = candidate_key(item["category"], item["value"])
        for field, value in item["value"].items():
            result[f"{key}.{field}"] = value
    return result


def evaluate():
    cases = []
    total_correct = total_wrong = total_missing = total_extra = located = fields = 0
    nonnull_correct = nonnull_wrong = nonnull_missing = null_correct = overblocked = 0
    healthy_overblocked = adversarial_blocked_truth = 0
    healthy_cases = {"normal_chinese", "missing_graduation", "multiple_entries_dates", "duplicate_model_items"}
    for case in fixture_cases():
        gold, diagnostics = golden_case(case["id"]), ExtractionDiagnostics()
        result = None
        try:
            result = extract_student_profile_v2(case["text"], FixtureResponseProvider(case), text_layout=build_text_layout(case["text"], [{"page": 1, "start": 0, "end": len(case["text"])}]), diagnostics=diagnostics)
        except ExtractionError:
            pass
        row = {"case_id": case["id"], "attempt_count": len(diagnostics.attempts), "failure_reason": diagnostics.failure_reason,
               "field_correct": 0, "field_wrong": 0, "field_missing": 0, "field_extra": 0, "field_errors": [], "guard_failures": [], "blocked_candidates": [], "located_fields": 0, "proposed_fields": 0}
        row.update(nonnull_expected_correct=0, nonnull_expected_wrong=0, nonnull_expected_missing=0, null_expected_correct=0, expected_supported_but_blocked=[])
        if "failure_reason" in gold:
            if diagnostics.failure_reason != gold["failure_reason"]:
                row["guard_failures"].append("expected_structural_failure")
        elif result is None:
            row["guard_failures"].append("unexpected_extraction_failure")
        else:
            actual_candidates = [{"category": item.category, "value": item.value.model_dump()} for item in result.batch.candidates] if result.batch else []
            actual, expected = flatten(actual_candidates), flatten(gold["candidates"])
            for path, value in expected.items():
                if path not in actual:
                    row["field_missing"] += 1
                    row["field_errors"].append({"path": path, "kind": "missing"})
                    row["nonnull_expected_missing"] += int(value is not None)
                elif value != actual[path]:
                    row["field_wrong"] += 1
                    row["field_errors"].append({"path": path, "kind": "wrong", "expected": value, "actual": actual[path]})
                    row["nonnull_expected_wrong"] += int(value is not None)
                else:
                    row["field_correct"] += 1
                    row["nonnull_expected_correct"] += int(value is not None)
                    row["null_expected_correct"] += int(value is None)
            for path in actual.keys() - expected.keys():
                row["field_extra"] += 1
                row["field_errors"].append({"path": path, "kind": "extra"})
            statuses = {}
            for item, metadata in zip(actual_candidates, result.candidate_metadata):
                key = candidate_key(item["category"], item["value"])
                statuses[key] = metadata["evidence_status"]
                if metadata["evidence_status"] != "supported":
                    row["blocked_candidates"].append({"candidate": key, "status": metadata["evidence_status"], "reasons": metadata["review_reasons"]})
                for trace in metadata["fields"]:
                    row["proposed_fields"] += 1
                    valid = bool(trace["locations"]) and all(case["text"][location["start"]:location["end"]] == trace["source_text"] for location in trace["locations"])
                    row["located_fields"] += int(valid)
            for key in gold.get("must_block", []):
                if statuses.get(key) == "supported":
                    row["guard_failures"].append(f"unsafe_confirmation_eligibility:{key}")
            for item in gold["candidates"]:
                key = candidate_key(item["category"], item["value"])
                if item["evidence_status"] == "needs_review" and statuses.get(key) == "supported":
                    row["guard_failures"].append(f"negation_or_plan_became_supported:{key}")
                if item["evidence_status"] == "supported" and key in statuses and statuses[key] != "supported":
                    row["expected_supported_but_blocked"].append({"candidate": key, "actual": statuses[key]})
        total_correct += row["field_correct"]
        total_wrong += row["field_wrong"]
        total_missing += row["field_missing"]
        total_extra += row["field_extra"]
        fields += row["proposed_fields"]
        located += row["located_fields"]
        nonnull_correct += row["nonnull_expected_correct"]
        nonnull_wrong += row["nonnull_expected_wrong"]
        nonnull_missing += row["nonnull_expected_missing"]
        null_correct += row["null_expected_correct"]
        overblocked += len(row["expected_supported_but_blocked"])
        if case["id"] in healthy_cases:
            healthy_overblocked += len(row["expected_supported_but_blocked"])
        else:
            adversarial_blocked_truth += len(row["expected_supported_but_blocked"])
        cases.append(row)
    return {"schema_version": "2.0", "provider": "fixed_synthetic_response", "paid_calls": 0,
            "interpretation": "This measures deterministic fixture mapping, evidence location and conservative guards; it does not establish real model semantic accuracy.",
            "field_count_interpretation": "Total comparisons include nulls; cases reuse synthetic variants and are not an independent real-resume distribution.",
            "case_count": len(cases), "field_correct": total_correct, "field_wrong": total_wrong, "field_missing": total_missing, "field_extra": total_extra,
            "located_fields": located, "proposed_fields": fields, "evidence_location_coverage": located / fields if fields else None,
            "nonnull_expected_correct": nonnull_correct, "nonnull_expected_wrong": nonnull_wrong, "nonnull_expected_missing": nonnull_missing,
            "null_expected_correct": null_correct, "expected_supported_but_blocked": overblocked,
            "healthy_case_supported_but_blocked": healthy_overblocked,
            "adversarial_supported_truth_but_faulty_output_blocked": adversarial_blocked_truth,
            "guard_failures": sum(len(row["guard_failures"]) for row in cases), "cases": cases}


def main():
    parser = argparse.ArgumentParser(description="Evaluate only fixed synthetic V2 responses without network or API keys")
    parser.add_argument("--output", type=Path, help="Optional local JSON report path")
    args = parser.parse_args()
    report = evaluate()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return int(report["guard_failures"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
