"""Run the current resume extraction core against a local PDF and DeepSeek."""

import argparse
import json
import os
import sys
from pathlib import Path

from offerpilot_api.agent_runtime.providers.deepseek import DeepSeekProvider
from offerpilot_api.resume_ingestion.extractor import (
    ExtractionDiagnostics,
    ExtractionError,
    extract_student_profile,
)
from offerpilot_api.services.resume import ResumeUploadError, _extract_pdf_text

PROMPT_VERSION = "resume_extract_v1"
DEFAULT_MODEL = "deepseek-flash"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract an evidence-backed profile from a local PDF using DeepSeek."
    )
    parser.add_argument("pdf_path", type=Path, help="Path to a local, selectable-text PDF resume")
    parser.add_argument(
        "--model",
        default=os.environ.get("OFFERPILOT_DEEPSEEK_MODEL", DEFAULT_MODEL),
        help=f"DeepSeek model name (default: {DEFAULT_MODEL}, configurable by OFFERPILOT_DEEPSEEK_MODEL)",
    )
    return parser


def _print_diagnostics(diagnostics: ExtractionDiagnostics) -> None:
    """Print only status codes and schema field paths, never input or model output."""

    print(
        "Redacted extraction diagnostics:\n"
        + json.dumps(diagnostics.to_dict(), ensure_ascii=False, indent=2),
        file=sys.stderr,
    )


def main() -> int:
    print("Legacy V1 paid evaluation is disabled; use evaluate_deepseek_v2.py with explicit consent and budgets.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
