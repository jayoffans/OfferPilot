"""One-off local launcher for the explicitly authorized 2026-10-14 evaluation.

Only the two non-secret confirmations already approved in this chat are supplied.
The existing CLI, provider and PDF business pipeline stay intact. The user asked
to replace console input with a local masked dialog; credentials stay in memory.
No credential is accepted in arguments or recorded in the launcher's state file.
"""

import builtins
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from secure_key_input import _terminal_is_secure
from masked_key_dialog import read_api_key_dialog

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "backend" / "evaluations"
OUTPUT = EVAL / "s2-2b-single-real-dialog-report-2026-10-14.json"
STATE = EVAL / "s2-2b-single-real-dialog-state-2026-10-14.json"
SHA = "1b4bb1d1f681092c0de6946913588d45c30bf543ed73994d0c40b64b2e025b0d"
AUTHORIZATION = "S2-2B-user-authorized-single-2026-10-14"


def save(state, *, exclusive=False):
    with STATE.open("x" if exclusive else "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    os.chdir(ROOT)
    state = {"authorization_record": AUTHORIZATION, "max_total_real_calls": 1,
             "key_input": "local_masked_dialog_user_requested", "secure_terminal": False,
             "nonsecret_confirmations": 0, "status": "checking_native_terminal"}
    save(state, exclusive=True)
    if OUTPUT.exists() or not _terminal_is_secure():
        state["status"] = "refused_before_key_or_request"
        save(state)
        print("Native secure terminal could not be verified; no Key or request.")
        return 2
    state["secure_terminal"] = True
    try:
        for prior_path in EVAL.glob("s2-2b-single-real*report-2026-10-14.json"):
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
            if any(prior[key] != 0 for key in ("actual_calls", "paid_calls", "reserved_calls")) or prior["cases"]:
                raise ValueError
        preflight = json.loads((EVAL / "s2-2b-single-real-continued-preflight-2026-10-14.json").read_text(encoding="utf-8"))
        planned = preflight["planned_cases"]
        if len(planned) != 1 or planned[0]["case_id"] != "normal_chinese":
            raise ValueError
        approved = planned[0]
        fixture = (EVAL / "fixtures" / "normal_chinese.synthetic.pdf").read_bytes()
        if hashlib.sha256(fixture).hexdigest() != approved["pdf"]["sha256"]:
            raise ValueError
        if approved["preview"]["sent_text_sha256"] != SHA or approved["preview"]["max_input_tokens"] != 16548:
            raise ValueError
        prompt = ROOT / "backend/src/offerpilot_api/resume_ingestion/prompts/resume_extract_v2.txt"
        if hashlib.sha256(prompt.read_text(encoding="utf-8").encode()).hexdigest() != preflight["prompt_sha256"]:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        state["status"] = "refused_preflight_changed_no_key_or_request"
        save(state)
        print("Approved preflight changed or prior call unknown; no Key or request.")
        return 2

    import evaluate_deepseek_v2 as cli
    original_key_read, original_main_pdf = cli.read_api_key, cli._main_pdf

    def tracked_key_read():
        key = read_api_key_dialog()
        state["masked_dialog_input_completed"] = True
        save(state)
        return key

    def tracked_main_pdf():
        try:
            return original_main_pdf()
        except (cli.UnsafeKeyInputError, cli.PdfEvaluationError) as error:
            code = getattr(error, "code", None)
            state["safe_error_code"] = code if code in {"terminal_unavailable", "secure_input_failed", "invalid_key"} else "pdf_pipeline_safe_failure"
            save(state)
            raise
    confirmations = [
        ("负责人另行授权后请输入：同意付费评测\n", "同意付费评测"),
        ("确认上述最终发送内容，请输入其完整SHA256：\n", SHA),
    ]

    def authorized_nonsecret_input(prompt):
        index = state["nonsecret_confirmations"]
        if index >= len(confirmations) or prompt != confirmations[index][0]:
            raise OSError("Unexpected confirmation prompt")
        state["nonsecret_confirmations"] += 1
        save(state)
        print("复用本次已明确授权的非秘密确认：" + confirmations[index][1])
        return confirmations[index][1]

    original_input, original_argv = builtins.input, sys.argv
    state["status"] = "waiting_user_local_masked_dialog"
    save(state)
    print("将在本机密码弹窗输入新 Key，支持 Ctrl+V。请点击“继续这一次评测”。", flush=True)
    sys.argv = ["evaluate_deepseek_v2.py", "--mode", "real", "--authorize-paid-calls",
                "--authorization-record", AUTHORIZATION, "--case-id", "normal_chinese",
                "--max-calls", "1", "--max-output-tokens", "4096", "--max-budget-cny", "1",
                "--input-price-per-million-cny", "2", "--output-price-per-million-cny", "8",
                "--price-audit-date", "2026-10-14", "--output", str(OUTPUT)]
    builtins.input = authorized_nonsecret_input
    cli.read_api_key, cli._main_pdf = tracked_key_read, tracked_main_pdf
    try:
        code = cli.main()
        state["status"] = "cli_exited_do_not_replay"
        state["exit_code"] = code
        save(state)
        if code == 2:
            print("安全停止，错误码已写入无凭据记录。窗口将在20秒后关闭，不会自动重试。", flush=True)
            time.sleep(20)
        return code
    finally:
        builtins.input, sys.argv = original_input, original_argv
        cli.read_api_key, cli._main_pdf = original_key_read, original_main_pdf


if __name__ == "__main__":
    try:
        result = main()
    except (Exception, KeyboardInterrupt):
        print("评测安全停止；请核对账本，禁止自动重启。未输出异常或凭据。", file=sys.stderr)
        result = 2
    raise SystemExit(result)
