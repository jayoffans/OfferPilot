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
    parser = build_parser()
    args = parser.parse_args()

    try:
        pdf_path = args.pdf_path.expanduser().resolve(strict=True)
    except OSError:
        parser.error("PDF path does not exist or cannot be accessed")
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        parser.error("Input must be a local .pdf file")

    api_key = os.environ.get("OFFERPILOT_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        parser.error("Set OFFERPILOT_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY in the environment")

    diagnostics = ExtractionDiagnostics()
    try:
        raw_text = _extract_pdf_text(pdf_path)
        provider = DeepSeekProvider(api_key=api_key, model=args.model)
        profile = extract_student_profile(raw_text, provider, diagnostics=diagnostics)
    except ResumeUploadError as exc:
        print(f"PDF extraction failed: {exc.detail}", file=sys.stderr)
        return 1
    except ExtractionError:
        print(
            f"Resume extraction failed: {diagnostics.failure_reason or 'extraction_failed'}",
            file=sys.stderr,
        )
        _print_diagnostics(diagnostics)
        return 1

    _print_diagnostics(diagnostics)
    print(profile.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
