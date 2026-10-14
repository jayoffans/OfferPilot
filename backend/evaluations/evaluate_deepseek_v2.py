"""Synthetic-only real V2 quality entry; dry-run never constructs a provider.

Paid execution requires explicit flags, a fresh output ledger, interactive
confirmation, getpass, call and token budgets, and operator-verified prices.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from offerpilot_api.agent_runtime.byok import MODEL, redact_resume, input_token_upper
from offerpilot_api.agent_runtime.providers.deepseek import DeepSeekProvider
from offerpilot_api.resume_ingestion.extractor import PROMPT_PACKAGE
from offerpilot_api.resume_ingestion.extractor_v2 import PROMPT_VERSION
from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from offerpilot_api.resume_ingestion.evidence import locate_quote, text_fingerprint
try:
    from .pdf_evaluation import PdfEvaluationSession, PdfEvaluationError, MockResponseSpy
    from .secure_key_input import read_api_key, UnsafeKeyInputError
    from .deepseek_v2_cases import CASESET_VERSION, CASE_IDS, evaluation_cases
    from .evaluate_resume_extraction_v2 import candidate_key
except ImportError:
    from pdf_evaluation import PdfEvaluationSession, PdfEvaluationError, MockResponseSpy
    from secure_key_input import read_api_key, UnsafeKeyInputError
    from deepseek_v2_cases import CASESET_VERSION, CASE_IDS, evaluation_cases
    from evaluate_resume_extraction_v2 import candidate_key


def _maximum_weight_pairs(weights):
    """Hungarian assignment, padded with unmatched rows/columns; no dependency."""
    if not weights or not weights[0]:
        return []
    rows, columns = len(weights), len(weights[0])
    size = max(rows, columns)
    costs = [[-weights[row][column] if row < rows and column < columns else 0
              for column in range(size)] for row in range(size)]
    left, right, assigned, previous = ([0] * (size + 1) for _ in range(4))
    for row in range(1, size + 1):
        assigned[0] = row
        minimum = [float("inf")] * (size + 1)
        used = [False] * (size + 1)
        column = 0
        while True:
            used[column] = True
            current_row, next_column, delta = assigned[column], 0, float("inf")
            for candidate_column in range(1, size + 1):
                if used[candidate_column]:
                    continue
                cost = costs[current_row - 1][candidate_column - 1] - left[current_row] - right[candidate_column]
                if cost < minimum[candidate_column]:
                    minimum[candidate_column], previous[candidate_column] = cost, column
                if minimum[candidate_column] < delta:
                    delta, next_column = minimum[candidate_column], candidate_column
            for candidate_column in range(size + 1):
                if used[candidate_column]:
                    left[assigned[candidate_column]] += delta
                    right[candidate_column] -= delta
                else:
                    minimum[candidate_column] -= delta
            column = next_column
            if assigned[column] == 0:
                break
        while column:
            prior_column = previous[column]
            assigned[column] = assigned[prior_column]
            column = prior_column
    return [(assigned[column] - 1, column - 1) for column in range(1, size + 1)
            if 0 < assigned[column] <= rows and column <= columns]


def _match_candidates(proposals, gold):
    """Identity groups, then most equal non-null fields; each gold used once."""
    matched = {}
    for key in dict.fromkeys(candidate_key(item["category"], item["value"]) for item in proposals):
        actual_ids = [index for index, item in enumerate(proposals) if candidate_key(item["category"], item["value"]) == key]
        gold_ids = [index for index, item in enumerate(gold) if candidate_key(item["category"], item["value"]) == key]
        weights = []
        for actual_id in actual_ids:
            actual = proposals[actual_id]["value"]
            weights.append([1000 * sum(value is not None and actual.get(field) == value for field, value in gold[gold_id]["value"].items())
                            + int(actual == gold[gold_id]["value"]) for gold_id in gold_ids])
        for actual_index, gold_index in _maximum_weight_pairs(weights):
            matched[actual_ids[actual_index]] = gold_ids[gold_index]
    return matched


def _signature(item):
    return item["category"], json.dumps(item["value"], ensure_ascii=False, sort_keys=True)


def quality(result, expected, raw):
    proposals = [{"category": item.category, "value": item.value.model_dump()} for item in result.batch.candidates] if result and result.batch else []
    gold = expected["candidates"]
    matched = _match_candidates(proposals, gold)
    reverse = {gold_index: actual_index for actual_index, gold_index in matched.items()}
    actual_fields = sum(value is not None for item in proposals for value in item["value"].values())
    gold_fields = sum(value is not None for item in gold for value in item["value"].values())
    correct, errors, missing = 0, [], []
    for index, item in enumerate(proposals):
        truth = gold[matched[index]] if index in matched else None
        for field, value in item["value"].items():
            if value is None:
                continue
            if truth is not None and truth["value"].get(field) == value:
                correct += 1
            else:
                # Model values never become report paths or log metadata.
                errors.append({"path": f"{item['category']}[{index}].{field}", "kind": "wrong_or_extra"})
    for index, item in enumerate(gold):
        actual = proposals[reverse[index]]["value"] if index in reverse else {}
        for field, value in item["value"].items():
            if value is not None and actual.get(field) != value:
                missing.append(f"{item['category']}[{index}].{field}")
    actual_counts, gold_counts = Counter(map(_signature, proposals)), Counter(map(_signature, gold))
    duplicate_candidates = sum(max(0, count - max(1, gold_counts[signature])) for signature, count in actual_counts.items())
    located = proposed = severe = changed = attributed = scoped_fields = null_correct = unexpected_nonnull = 0
    candidate_errors, overblocked = [], []
    for index, (item, meta) in enumerate(zip(proposals, result.candidate_metadata if result else [])):
        key = candidate_key(item["category"], item["value"])
        truth = gold[matched[index]] if index in matched else None
        changed += int(truth is None or truth["value"] != item["value"])
        if truth is None or truth["value"] != item["value"]:
            candidate_errors.append({"path": f"{item['category']}[{index}]", "kind": "wrong_or_extra_candidate",
                                     "evidence_status": meta["evidence_status"]})
        if truth:
            for field, value in truth["value"].items():
                if value is None:
                    null_correct += int(item["value"].get(field) is None)
                    unexpected_nonnull += int(item["value"].get(field) is not None)
            if truth["evidence_status"] == "supported" and meta["evidence_status"] != "supported":
                overblocked.append(f"{item['category']}[{index}]")
        # Repeated known facts count as extras/edits, not fabricated facts.
        factual_truth = truth or next((candidate for candidate in gold if _signature(candidate) == _signature(item)), None)
        if meta["evidence_status"] == "supported" and (factual_truth is None or factual_truth["value"] != item["value"] or key in expected.get("must_block", []) or (factual_truth and factual_truth["evidence_status"] != "supported")):
            severe += 1
        for trace in meta["fields"]:
            proposed += 1
            located += int(bool(trace["locations"]) and all(raw[pos["start"]:pos["end"]] == trace["source_text"] for pos in trace["locations"]))
            if item["category"] != "skill":
                scoped_fields += 1
                expected_scope = locate_quote(raw, truth["scope_text"]) if truth and truth.get("scope_text") else []
                attributed += int(bool(trace["locations"]) and all(any(scope["start"] <= pos["start"] and pos["end"] <= scope["end"] for scope in expected_scope) for pos in trace["locations"]))
    return dict(correct=correct, proposed_nonnull_fields=actual_fields, expected_nonnull_fields=gold_fields,
        precision=correct / actual_fields if actual_fields else None, recall=correct / gold_fields if gold_fields else None,
        false_positive_fields=len(errors), false_negative_fields=len(missing), duplicate_candidates=duplicate_candidates,
        extra_candidates=len(proposals) - len(matched), missing_candidates=len(gold) - len(matched),
        field_errors=errors, missing_fields=missing, candidate_errors=candidate_errors,
        null_expected_correct=null_correct, unsupported_nonnull_for_unknown=unexpected_nonnull,
        expected_supported_but_blocked=overblocked, located_fields=located, proposed_fields=proposed,
        evidence_location_accuracy=located / proposed if proposed else None, severe_errors=severe,
        attributed_fields=attributed, scoped_fields=scoped_fields,
        evidence_attribution_accuracy=attributed / scoped_fields if scoped_fields else None,
        candidates=len(proposals), candidates_requiring_gold_modification=changed,
        candidate_modification_ratio=changed / len(proposals) if proposals else None,
        evidence_note="Literal quote location coverage only; not independent proof of semantic attribution or resume truth.",
        human_review_note="Gold mismatch is a proxy for edits; real user editing effort must be measured separately.")




def _write_report(path, report, *, exclusive=False):
    """Persist before each possible charge; existing ledgers are never replayed."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def _safe_response_metadata(provider):
    # Provider sanitizes these untrusted metadata values; raw responses are not
    # saved here. A missing value is unknown, never inferred from the alias.
    value = getattr(provider, "last_response_model", None)
    return {"response_model": value if isinstance(value, str) and len(value) <= 100 and
            re.fullmatch(r"deepseek-(?:flash|v\d+(?:\.\d+)?-(?:flash|pro)(?:-\d{4})?(?:-preview)?)", value, re.I) else None}


def _aggregate(rows):
    correct = sum(row["correct"] for row in rows)
    proposed = sum(row["proposed_nonnull_fields"] for row in rows)
    expected = sum(row["expected_nonnull_fields"] for row in rows)
    located, traces = sum(row["located_fields"] for row in rows), sum(row["proposed_fields"] for row in rows)
    return {"correct": correct, "proposed_nonnull_fields": proposed, "expected_nonnull_fields": expected,
            "precision": correct / proposed if proposed else None, "recall": correct / expected if expected else None,
            "located_fields": located, "proposed_fields": traces,
            "evidence_location_accuracy": located / traces if traces else None,
            "severe_errors": sum(row["severe_errors"] for row in rows)}


def _execution_date():
    # Current mainland-China/Asia-Shanghai civil time; no host timezone or
    # Windows tzdata dependency is needed for the current UTC+8 policy.
    return datetime.now(timezone(timedelta(hours=8))).date()


class _CountedProvider:
    """Count at the provider boundary; pre-call ledger reservations stay distinct."""

    def __init__(self, provider, report, max_calls):
        self.provider, self.report, self.max_calls = provider, report, max_calls

    @property
    def last_usage(self):
        return getattr(self.provider, "last_usage", None)

    @property
    def last_response_model(self):
        return getattr(self.provider, "last_response_model", None)

    def close(self):
        if hasattr(self.provider, "close"):
            self.provider.close()

    def generate_json(self, *, system_prompt, user_prompt):
        if self.report["actual_calls"] >= self.max_calls:
            raise LLMProviderError("Evaluation call budget exhausted", code="evaluation_call_budget_exhausted")
        # No fallible file IO between this boundary and invoking the provider.
        # After a crash the earlier durable reservation remains unknown; it
        # must never be treated as evidence that no charge happened.
        self.report["actual_calls"] += 1
        self.report["paid_calls"] += 1
        return self.provider.generate_json(system_prompt=system_prompt, user_prompt=user_prompt)


def _concrete_errors(result, expected):
    """Public synthetic field values and evidence for human review, not logs."""
    proposals = [{"category": item.category, "value": item.value.model_dump()}
                 for item in result.batch.candidates] if result and result.batch else []
    matched = _match_candidates(proposals, expected["candidates"])
    reverse = {gold_index: actual_index for actual_index, gold_index in matched.items()}
    rows = []
    for index, proposal in enumerate(proposals):
        truth = expected["candidates"][matched[index]]["value"] if index in matched else {}
        metadata = result.candidate_metadata[index]
        for field, actual in proposal["value"].items():
            if actual != truth.get(field):
                rows.append({"category": proposal["category"], "candidate_index": index,
                             "field": field, "expected": truth.get(field), "actual": actual,
                             "evidence_status": metadata["evidence_status"],
                             "evidence": [{"source_text": trace["source_text"], "locations": trace["locations"],
                                           "review_reasons": trace["review_reasons"]}
                                          for trace in metadata["fields"] if trace.get("mapped_field") == field]})
    for index, truth in enumerate(expected["candidates"]):
        if index not in reverse:
            rows.append({"category": truth["category"], "kind": "missing_candidate",
                         "expected": truth["value"], "actual": None, "evidence": []})
    return rows


def _display_preview(prepared):
    print("以下为实际PDF解析后的最终发送预览（固定合成资料；PDF原件不发送）。")
    print(json.dumps({"pdf": prepared.pdf, **prepared.preview}, ensure_ascii=False, indent=2))


def main():
    try:
        return _main_pdf()
    except (UnsafeKeyInputError, PdfEvaluationError) as error:
        # Fixed, safe evaluation messages. No exception/headers/key traceback.
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, EOFError, KeyboardInterrupt):
        print("评测已安全停止；未完成的已存在账本不得重放，已发送请求可能收费。", file=sys.stderr)
        return 2


def _main_pdf():
    parser = argparse.ArgumentParser(description="合成PDF→正式业务候选池；默认免费预览，真实调用需未来独立授权")
    parser.add_argument("--mode", choices=("plan", "mock", "real"))
    parser.add_argument("--authorize-paid-calls", action="store_true")
    parser.add_argument("--authorization-record")
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--case-id", action="append", choices=CASE_IDS)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-budget-cny", type=Decimal)
    parser.add_argument("--input-price-per-million-cny", type=Decimal)
    parser.add_argument("--output-price-per-million-cny", type=Decimal)
    parser.add_argument("--price-audit-date")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--demonstrate-review", action="store_true",
                        help="仅免费Mock：显式演示确认一项技能、拒绝一项项目；真实模式不自动审核")
    args = parser.parse_args()
    mode = args.mode or ("real" if args.authorize_paid_calls else "plan")
    if (mode == "real") != args.authorize_paid_calls:
        parser.error("真实模式必须显式authorize；免费模式不得携带收费授权")
    if args.case_id and args.case_limit is not None:
        parser.error("case-id 与 case-limit不能同时使用")
    limit = args.case_limit if args.case_limit is not None else (5 if mode == "mock" else 1)
    if not 1 <= limit <= 5 or not 1 <= args.max_output_tokens <= 4096 or len(set(args.case_id or [])) != len(args.case_id or []):
        parser.error("case-limit=1..5、case-id不得重复、output tokens=1..4096")
    cases = [case for case in evaluation_cases() if case["id"] in args.case_id] if args.case_id else evaluation_cases()[:limit]
    if args.demonstrate_review and mode != "mock":
        parser.error("审核演示只允许显式免费Mock；真实候选保持pending")
    if args.output and args.output.exists():
        parser.error("报告已存在，拒绝覆盖或重放；请使用新的评测账本")
    if mode == "real":
        if len(cases) != 1 or cases[0]["id"] != "normal_chinese" or args.max_calls != 1 or not args.output:
            parser.error("首次真实评测仅normal_chinese、max-calls=1及全新output账本")
        if not args.authorization_record or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.authorization_record):
            parser.error("必须记录负责人本批明确授权的authorization-record")
        if any(value is None or not value.is_finite() or value <= 0 for value in
               (args.max_budget_cny, args.input_price_per_million_cny, args.output_price_per_million_cny)):
            parser.error("预算与核实的当前峰值单价必须为正数")
        try:
            price_audit = date.fromisoformat(args.price_audit_date or "")
        except ValueError:
            parser.error("必须提供执行当天的price-audit-date YYYY-MM-DD")
        if price_audit != _execution_date():
            parser.error("价格必须在执行当天（Asia/Shanghai）核实")
    report = {"provider": "deepseek" if mode == "real" else "mock_synthetic_pdf" if mode == "mock" else "none",
              "mode": mode, "test_result": mode != "real", "model": MODEL, "request_model": MODEL,
              "notice": "Mock 测试结果，不代表 DeepSeek 真实模型质量。" if mode != "real" else "真实合成评测，需逐字段审核。",
              "model_alias_audit_date": "2026-10-14", "model_version_at_audit": "DeepSeek-V4.1-Flash",
              "model_version_note": "Official alias snapshot; response model does not prove immutable weights.",
              "openai_sdk_version": version("openai"), "provider_version": "deepseek-openai-json-v1.1",
              "caseset_version": CASESET_VERSION, "prompt_version": PROMPT_VERSION,
              "prompt_sha256": text_fingerprint(files(PROMPT_PACKAGE).joinpath("resume_extract_v2.txt").read_text(encoding="utf-8")),
              "schema_version": "2.0", "redaction_version": "resume-redaction-v1",
              "reserved_calls": 0, "actual_calls": 0, "paid_calls": 0, "protocol_calls": 0,
              "actual_cost_cny": None if mode == "real" else "0", "estimated_usage_cost_cny": None,
              "status": "预检准备中；未获得本提示之外的收费授权", "cases": [], "planned_cases": []}
    _write_report(args.output, report, exclusive=True)
    failed = False
    for case in cases:
        # Every sample gets an independent account/store/DB; no prior review
        # can make a later sample's "formal profile empty" check misleading.
        with PdfEvaluationSession() as session:
            prepared = session.prepare(case["id"])
            report["planned_cases"].append({"case_id": case["id"], "coverage": case["coverage"],
                                            "pdf": prepared.pdf, "preview": prepared.preview})
            if mode == "plan":
                _write_report(args.output, report)
                continue
            key = None
            if mode == "real":
                projected = (Decimal(prepared.preview["max_input_tokens"]) * args.input_price_per_million_cny
                             + Decimal(args.max_output_tokens) * args.output_price_per_million_cny) / 1000000
                if projected > args.max_budget_cny:
                    parser.error("实际PDF发送范围的保守输入/输出估算超过预算，拒绝调用")
                report.update(authorization_record=args.authorization_record, declared_max_calls=1,
                              declared_budget_cny=str(args.max_budget_cny), price_audit_date=price_audit.isoformat(),
                              price_source="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                              input_price_per_million_cny=str(args.input_price_per_million_cny),
                              output_price_per_million_cny=str(args.output_price_per_million_cny),
                              estimated_upper_cny=str(projected))
                _display_preview(prepared)
                print(f"最多一次请求、输出{args.max_output_tokens}token，峰值估算上界{projected}元；超时也可能收费，不重试。")
                if input("负责人另行授权后请输入：同意付费评测\n") != "同意付费评测":
                    report["status"] = "未确认收费；0调用"
                    _write_report(args.output, report)
                    return 2
                if input("确认上述最终发送内容，请输入其完整SHA256：\n") != prepared.preview["sent_text_sha256"]:
                    report["status"] = "未确认发送预览；0调用"
                    _write_report(args.output, report)
                    return 2
                # The helper verifies a native hidden-input terminal first.
                key = read_api_key()
            run = session.create_run(prepared, mode="deepseek" if mode == "real" else "mock",
                                     confirmed_sha256=prepared.preview["sent_text_sha256"], api_key=key)
            row = {"case_id": case["id"], "state": "outcome_unknown", "request_model": MODEL,
                   "run_id": run["id"], "token_usage": None, "estimated_usage_cost_cny": None}
            report["cases"].append(row)
            _write_report(args.output, report)
            provider = None

            def factory(**_kwargs):
                nonlocal provider
                if mode == "real":
                    provider = DeepSeekProvider(api_key=_kwargs["api_key"], model=MODEL, timeout_seconds=10,
                                                max_output_tokens=args.max_output_tokens)
                    return _CountedProvider(provider, report, 1)
                provider = MockResponseSpy(prepared.case["response"])
                return provider

            def reserve():
                if mode == "real":
                    if report["reserved_calls"] >= 1:
                        raise PdfEvaluationError("Single-call reservation exhausted")
                    report["reserved_calls"] += 1
                _write_report(args.output, report)  # fsync before delegate invocation
            key = None
            success = session.execute(prepared, run, provider_factory=factory, before_call=reserve)
            details, result = session.collect(prepared, run, evaluation_kind="real" if mode == "real" else "mock")
            row.update(details)
            metrics = quality(result, prepared.case["gold"], prepared.raw_text)
            row["candidate_count"] = metrics.pop("candidates")
            row.update(metrics)
            row["concrete_error_cases"] = _concrete_errors(result, prepared.case["gold"])
            row.update(_safe_response_metadata(provider))
            row["attempt_count"] = row["run"]["attempt_count"]
            row["state"] = "validated" if success else "failed"
            if not success:
                row["failure_code"] = row["run"]["error_code"]
            restored = session.relogin()
            row["relogin_restores_candidate_ids"] = sorted(item["id"] for item in restored["candidates"]) == sorted(item["id"] for item in row["candidates"])
            if mode == "mock":
                report["protocol_calls"] += row["provider_invocations"]
            if row["severe_errors"]:
                row["state"] = "severe_quality_error"
                success = False
            usage = row["token_usage"]
            if mode == "real":
                if usage is None:
                    row["state"] = "billing_unknown"
                    success = False
                else:
                    row["estimated_usage_cost_cny"] = str((Decimal(usage["prompt_tokens"]) * args.input_price_per_million_cny
                                                         + Decimal(usage["completion_tokens"]) * args.output_price_per_million_cny) / 1000000)
                    report["estimated_usage_cost_cny"] = row["estimated_usage_cost_cny"]
                    if usage["completion_tokens"] > args.max_output_tokens or usage["prompt_tokens"] > prepared.preview["max_input_tokens"]:
                        row["state"] = "provider_usage_exceeded_bounds"
                        success = False
            if not row["preview_matches_actual_provider_input"] or not row["unconfirmed_candidates_not_in_profile"]:
                success = False
                row["state"] = "isolation_or_disclosure_failed"
            if args.demonstrate_review and success:
                skill = next((item for item in row["candidates"] if item["category"] == "skill" and item["evidence_status"] == "supported"), None)
                rejected = next((item for item in row["candidates"] if item["category"] == "project"), None)
                if skill and rejected:
                    row["explicit_mock_review"] = session.review_demonstration(run,
                        confirm_candidate_id=skill["id"], reject_candidate_id=rejected["id"])
                else:
                    success = False
                    row["state"] = "review_demonstration_unavailable"
            report["summary"] = _aggregate(report["cases"])
            report["status"] = "Mock 测试结果，不代表 DeepSeek 真实模型质量。" if mode == "mock" else "已执行一次合成真实评测，需人工审核与核对账单"
            _write_report(args.output, report)
            if not success:
                failed = True
                break
    if mode in ("plan", "mock"):
        if mode == "plan":
            report["status"] = "免费PDF预检准备完成；0 Provider/Key，真实调用待独立授权"
            _write_report(args.output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"已尝试{report['actual_calls']}次；实际金额待账单核对。候选保持pending，未自动审核。")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
