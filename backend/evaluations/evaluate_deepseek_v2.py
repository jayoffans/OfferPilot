"""Synthetic-only real V2 quality entry; dry-run never constructs a provider.

Paid execution requires explicit flags, a fresh output ledger, interactive
confirmation, getpass, call and token budgets, and operator-verified prices.
"""
import argparse
import getpass
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from offerpilot_api.agent_runtime.byok import MODEL, redact_resume, input_token_upper
from offerpilot_api.agent_runtime.providers.deepseek import DeepSeekProvider
from offerpilot_api.resume_ingestion.evidence import build_text_layout
from offerpilot_api.resume_ingestion.extractor import ExtractionDiagnostics, ExtractionError, PROMPT_PACKAGE
from offerpilot_api.resume_ingestion.extractor_v2 import extract_student_profile_v2, PROMPT_VERSION
from offerpilot_api.agent_runtime.llm_provider import LLMProviderError
from offerpilot_api.resume_ingestion.evidence import locate_quote, text_fingerprint
try:
    from .deepseek_v2_cases import CASESET_VERSION, CASE_IDS, evaluation_cases
    from .evaluate_resume_extraction_v2 import candidate_key
except ImportError:
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


class _ProtocolClient:
    """SDK-shaped fixed response client with no sockets or network transport."""

    def __init__(self, response, model):
        self.response, self.model, self.calls = response, model, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls += 1
        if self.calls != 1:
            raise AssertionError("Protocol fixture permits exactly one request")
        assert kwargs["model"] == self.model
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert kwargs["response_format"] == {"type": "json_object"} and kwargs["stream"] is False
        assert "JSON" in kwargs["messages"][0]["content"]
        return SimpleNamespace(
            model=self.model, id="00000000-0000-4000-8000-000000000001", system_fingerprint=None,
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(self.response, ensure_ascii=False)))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    def close(self):
        pass


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

    def generate_json(self, *, system_prompt, user_prompt):
        if self.report["actual_calls"] >= self.max_calls:
            raise LLMProviderError("Evaluation call budget exhausted", code="evaluation_call_budget_exhausted")
        # No fallible file IO between this boundary and invoking the provider.
        # After a crash the earlier durable reservation remains unknown; it
        # must never be treated as evidence that no charge happened.
        self.report["actual_calls"] += 1
        self.report["paid_calls"] += 1
        return self.provider.generate_json(system_prompt=system_prompt, user_prompt=user_prompt)


def main():
    parser = argparse.ArgumentParser(description="默认 dry-run（0 次调用）；仅负责人主动授权后真实调用合成样本")
    parser.add_argument("--mode", choices=("plan", "mock", "real"))
    parser.add_argument("--authorize-paid-calls", action="store_true")
    parser.add_argument("--authorization-record", help="负责人本批明确授权的记录标识，不含姓名或秘密")
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--case-id", action="append", choices=CASE_IDS, help="仅可选择固定合成白名单；可重复参数选择不同样本")
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-budget-cny", type=Decimal)
    parser.add_argument("--input-price-per-million-cny", type=Decimal)
    parser.add_argument("--output-price-per-million-cny", type=Decimal)
    parser.add_argument("--price-audit-date", help="负责人核实官方最高单价的日期 YYYY-MM-DD")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    mode = args.mode or ("real" if args.authorize_paid_calls else "plan")
    if (mode == "real") != args.authorize_paid_calls:
        parser.error("真实模式必须显式 --authorize-paid-calls；plan/mock 不允许付费授权标志")
    if args.case_id and args.case_limit is not None:
        parser.error("case-id 与 case-limit 不能同时使用")
    limit = args.case_limit if args.case_limit is not None else (5 if mode == "mock" else 1)
    if not 1 <= limit <= 5 or not 1 <= args.max_output_tokens <= 4096 or len(set(args.case_id or [])) != len(args.case_id or []):
        parser.error("case-limit=1..5，case-id 不得重复；output tokens=1..4096")
    available = evaluation_cases()
    cases = [case for case in available if case["id"] in args.case_id] if args.case_id else available[:limit]
    report = {"provider": "deepseek" if mode == "real" else "deepseek_protocol_mock" if mode == "mock" else "none",
              "mode": mode, "test_result": mode != "real", "model": MODEL, "request_model": MODEL,
              "model_alias_audit_date": "2026-10-12", "model_version_at_audit": "DeepSeek-V4.1-Flash",
              "model_version_note": "Official documentation snapshot; response model alias cannot independently establish deployed weights.",
              "openai_sdk_version": version("openai"), "caseset_version": CASESET_VERSION,
              "provider_version": "deepseek-openai-json-v1.1",
              "prompt_version": PROMPT_VERSION,
              "prompt_sha256": text_fingerprint(files(PROMPT_PACKAGE).joinpath("resume_extract_v2.txt").read_text(encoding="utf-8")),
              "redaction_version": "resume-redaction-v1",
              "schema_version": "2.0", "reserved_calls": 0, "actual_calls": 0, "paid_calls": 0,
              "protocol_calls": 0, "actual_cost_cny": None if mode == "real" else "0", "estimated_usage_cost_cny": None,
              "call_count_note": "Actual calls count one provider invocation, including unknown/failed responses; billable calls require the provider statement.",
              "status": "真实调用待验收", "cases": [],
              "planned_cases": [{"case_id": case["id"], "coverage": case["coverage"],
                                  "synthetic_text_sha256": text_fingerprint(case["text"]),
                                  "sent_text_sha256": text_fingerprint(redact_resume(case["text"]))} for case in cases]}
    if args.output and args.output.exists():
        parser.error("报告路径已存在，拒绝覆盖或重放；每批授权使用新账本")
    if mode == "plan":
        _write_report(args.output, report, exclusive=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if mode == "real":
        if not args.output or args.max_calls < len(cases) or args.max_calls > 5:
            parser.error("付费运行需全新 --output 账本及足够的 --max-calls（最多5）")
        if not args.authorization_record or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.authorization_record):
            parser.error("必须记录负责人本批明确授权的 --authorization-record 标识（仅字母、数字、_、-）")
        if any(value is None or not value.is_finite() or value <= 0 for value in (args.max_budget_cny, args.input_price_per_million_cny, args.output_price_per_million_cny)):
            parser.error("请显式设置预算及核实当前峰值输入/输出单价，均需正数")
        try:
            price_audit = date.fromisoformat(args.price_audit_date or "")
        except ValueError:
            parser.error("必须记录 --price-audit-date YYYY-MM-DD；不得沿用未经核实的价格")
        if price_audit != _execution_date():
            parser.error("单价必须在执行当天（Asia/Shanghai）核实；陈旧或未来日期拒绝调用")
        projected = sum(Decimal(input_token_upper(redact_resume(case['text']))) * args.input_price_per_million_cny / 1000000
                        + Decimal(args.max_output_tokens) * args.output_price_per_million_cny / 1000000 for case in cases)
        if projected > args.max_budget_cny:
            parser.error("按输入保守上界和输出上限估计会超过预算，拒绝调用")
        report.update(authorization_record=args.authorization_record, declared_max_calls=args.max_calls,
                      declared_budget_cny=str(args.max_budget_cny), price_audit_date=price_audit.isoformat(),
                      price_source="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                      input_price_per_million_cny=str(args.input_price_per_million_cny),
                      output_price_per_million_cny=str(args.output_price_per_million_cny), estimated_upper_cny=str(projected))
        print(f"将发送 {len(cases)} 个纯合成样本，最多 {args.max_calls} 次，不重试。按您核实的单价估计上界 {projected} 元；实际费用以供应商账单为准。")
        if input("负责人确认请完整输入：同意付费评测\n") != "同意付费评测":
            print("未授权，调用次数 0。")
            return 2
        key = getpass.getpass("DeepSeek API Key（不显示、不保存）：")
        if not key.strip():
            print("未配置 Key，调用次数 0。")
            return 2
    else:
        key = None
    _write_report(args.output, report, exclusive=True)
    provider = None
    failed = False
    try:
        if mode == "real":
            provider = DeepSeekProvider(api_key=key, model=MODEL, timeout_seconds=10, max_output_tokens=args.max_output_tokens)
            key = None
        for case in cases:
            if mode == "mock":
                client = _ProtocolClient(case["response"], MODEL)
                provider = DeepSeekProvider(api_key="synthetic-no-network-key", model=MODEL, max_output_tokens=args.max_output_tokens, client=client)
            row = {"case_id": case["id"], "state": "outcome_unknown", "request_model": MODEL,
                   "sent_text_sha256": text_fingerprint(redact_resume(case["text"])),
                   "token_usage": None, "token_usage_kind": "provider_reported" if mode == "real" else "simulated_protocol_usage",
                   "estimated_usage_cost_cny": None, "actual_cost_cny": None if mode == "real" else "0"}
            report["reserved_calls"] += int(mode == "real")
            report["cases"].append(row)
            _write_report(args.output, report)
            diagnostics = ExtractionDiagnostics()
            result = None
            try:
                counted = _CountedProvider(provider, report, args.max_calls) if mode == "real" else provider
                result = extract_student_profile_v2(case["text"], counted, max_attempts=1,
                    model_input_text=redact_resume(case["text"]), text_layout=build_text_layout(case["text"]), diagnostics=diagnostics)
                if result.batch is None:
                    diagnostics.failure_reason = "empty_extraction"
                    raise ExtractionError("No candidate facts returned")
                row["state"] = "validated"
            except ExtractionError:
                row["state"] = "failed"
                row["failure_code"] = diagnostics.failure_reason
                result = None
            row.update(quality(result, case["gold"], case["text"]))
            row.update(_safe_response_metadata(provider))
            row["attempt_count"] = len(diagnostics.attempts)
            if mode == "mock":
                report["protocol_calls"] += client.calls
            if row["severe_errors"]:
                row["state"] = "severe_quality_error"
                result = None
            usage = provider.last_usage
            if usage:
                row["token_usage"] = {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens}
                if mode == "real":
                    row["estimated_usage_cost_cny"] = str((Decimal(usage.prompt_tokens) * args.input_price_per_million_cny + Decimal(usage.completion_tokens) * args.output_price_per_million_cny) / 1000000)
            else:
                row["state"] = "billing_unknown"
                result = None  # Preserve quality, but stop further paid calls.
            if usage and (usage.completion_tokens > args.max_output_tokens or usage.prompt_tokens > input_token_upper(redact_resume(case["text"]))):
                row["state"] = "provider_usage_exceeded_bounds"
                result = None
            report["status"] = "已执行合成真实模型评测，需负责人审核；不代表真实简历质量或账单" if mode == "real" else "免费固定响应/协议桩验证；真实模型质量待验收"
            report["summary"] = _aggregate(report["cases"])
            if mode == "real" and all(row["estimated_usage_cost_cny"] is not None for row in report["cases"]):
                report["estimated_usage_cost_cny"] = str(sum(Decimal(row["estimated_usage_cost_cny"]) for row in report["cases"]))
            _write_report(args.output, report)
            if mode == "mock":
                provider.close()
                provider = None
            if result is None:
                failed = True
                break  # No further charges after any failure or unknown result.
    finally:
        if provider is not None:
            provider.close()
    if mode == "mock":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"已尝试调用 {report['paid_calls']} 次；实际费用请核对 DeepSeek 账单。报告不含Key、简历原文或模型原始输出。")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
