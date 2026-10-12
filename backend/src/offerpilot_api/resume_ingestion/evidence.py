"""Bounded lexical evidence checks and server-authored locations, not NLU."""

import hashlib
import json
import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_OCCURRENCES = 20
DATE_RE = re.compile(r"(?<!\d)((?:19|20|21)\d{2})\s*(?:年|[-/.])\s*(0?[1-9]|1[0-2])\s*月?(?!\d)")
UNCERTAIN_RE = re.compile(
    r"\b(?:not|never|no|without|lack|unable|unfamiliar|unknown|maybe|planned|planning|intend)\b"
    r"|不|未|没有|无经验|无基础|计划|拟学习|待学习|准备学习|可能|未知|待确认", re.I,
)
INJECTION_RE = re.compile(r"忽略.{0,20}(?:指令|要求)|系统提示|system\s*prompt|ignore.{0,30}instructions|自动确认|直接确认", re.I)
ALIASES = {"js": "JavaScript", "javascript": "JavaScript", "ts": "TypeScript", "typescript": "TypeScript",
           "python": "Python", "java": "Java", "golang": "Go", "go": "Go", "c++": "C++", "cpp": "C++"}


class TraceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceLocation(TraceModel):
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=1)


class ExtractedFieldTrace(TraceModel):
    path: str = Field(min_length=1, max_length=100, pattern=r"^[a-z_0-9.\[\]]+$")
    mapped_field: str | None = Field(default=None, max_length=40, pattern=r"^[a-z_]+$")
    raw_value: str = Field(min_length=1, max_length=500)
    source_text: str = Field(min_length=1, max_length=500)
    reported_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    assertion: Literal["affirmed", "negated", "planned", "uncertain"]
    normalized_value: str | bool | None = None
    locations: list[EvidenceLocation] = Field(default_factory=list, max_length=20)
    review_reasons: list[str] = Field(default_factory=list, max_length=15)


class CandidateExtractionMetadata(TraceModel):
    schema_version: Literal["2.0"] = "2.0"
    prompt_version: Literal["resume_extract_v2"] = "resume_extract_v2"
    provider: Literal["mock"] = "mock"
    test_result: Literal[True] = True
    source_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_layout_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_file_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    text_layout_version: Literal["1.0"] = "1.0"
    category: Literal["education", "skill", "project", "experience"]
    item_path: str = Field(min_length=1, max_length=100, pattern=r"^[a-z_0-9.\[\]]+$")
    scope_text: str | None = Field(default=None, min_length=1, max_length=3000)
    scope_locations: list[EvidenceLocation] = Field(default_factory=list, max_length=20)
    fields: list[ExtractedFieldTrace] = Field(max_length=60)
    review_reasons: list[str] = Field(default_factory=list, max_length=20)
    evidence_status: Literal["supported", "unsupported", "needs_review"] = "needs_review"
    duplicate_proposals_removed: int = Field(default=0, ge=0, le=100)
    confidence_interpretation: Literal["model_reported_not_truth_probability"] = "model_reported_not_truth_probability"

    @model_validator(mode="after")
    def field_ownership(self):
        groups = {"education": "education", "skill": "skills", "project": "projects", "experience": "experiences"}
        if not re.fullmatch(re.escape(groups[self.category]) + r"\[\d{1,2}\]", self.item_path):
            raise ValueError("Extraction item path does not match category")
        if (self.category == "skill") != (self.scope_text is None):
            raise ValueError("Only skills may omit an experience scope")
        direct = {
            "education": {"school": "school", "degree": "degree", "major": "major", "start_date": "start_date", "end_date": None, "graduation_date": "end_date", "graduation_status": "end_date_kind"},
            "skill": {"name": "name", "level": "level"},
            "project": {"name": "name", "role": "role", "start_date": "start_date", "end_date": "end_date", "is_current": "is_current"},
            "experience": {"organization": "organization", "role": "role", "experience_type": "experience_type", "start_date": "start_date", "end_date": "end_date", "is_current": "is_current"},
        }[self.category]
        collections = {"project": ("technology_stack", "work_items"), "experience": ("responsibilities", "outcomes")}.get(self.category, ())
        paths = set()
        for trace in self.fields:
            if not trace.path.startswith(self.item_path + ".") or trace.path in paths:
                raise ValueError("Extraction field ownership is invalid")
            paths.add(trace.path)
            relative = trace.path[len(self.item_path) + 1:]
            if relative in direct:
                expected = direct[relative]
            elif any(re.fullmatch(re.escape(name) + r"\[(?:[0-9]|1[0-9])\]", relative) for name in collections):
                expected = "description"
            else:
                raise ValueError("Extraction field path is not in V2")
            if trace.mapped_field != expected:
                raise ValueError("Extraction field mapping is not in V2")
        return self


def text_fingerprint(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def layout_fingerprint(raw_text: str, layout: dict | None = None) -> str:
    return text_fingerprint(json.dumps(_valid_layout(raw_text, layout), sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def build_text_layout(raw_text: str, page_spans: list[dict] | None = None) -> dict:
    """Offsets refer to exactly the saved Python string. Legacy pages stay unknown."""
    pages = page_spans or []
    segments = []
    offset = 0
    for line in raw_text.splitlines(keepends=True):
        end = offset + len(line.rstrip("\r\n"))
        if raw_text[offset:end].strip():
            page = next((item["page"] for item in pages if item["start"] <= offset and end <= item["end"]), None)
            segments.append({"paragraph": len(segments) + 1, "page": page, "start": offset, "end": end})
        offset += len(line)
    return {"layout_version": "1.0", "text_sha256": text_fingerprint(raw_text), "pages": pages, "segments": segments}


def _valid_layout(raw_text: str, layout: dict | None) -> dict:
    if not isinstance(layout, dict) or layout.get("layout_version") != "1.0" or layout.get("text_sha256") != text_fingerprint(raw_text):
        return build_text_layout(raw_text)
    for group, identifier in (("pages", "page"), ("segments", "paragraph")):
        if not isinstance(layout.get(group), list) or len(layout[group]) > 50_000:
            return build_text_layout(raw_text)
        for item in layout[group]:
            if (not isinstance(item, dict) or type(item.get("start")) is not int or type(item.get("end")) is not int
                or not 0 <= item["start"] < item["end"] <= len(raw_text)
                or type(item.get(identifier)) is not int or item[identifier] < 1):
                return build_text_layout(raw_text)
    return layout


def locate_quote(raw_text: str, quote: str, layout: dict | None = None, *, lower: int = 0, upper: int | None = None) -> list[dict]:
    layout = _valid_layout(raw_text, layout)
    result = []
    position = lower
    upper = len(raw_text) if upper is None else upper
    while len(result) < MAX_OCCURRENCES:
        start = raw_text.find(quote, position, upper)
        if start < 0:
            break
        end = start + len(quote)
        page = next((item["page"] for item in layout["pages"] if item["start"] <= start and end <= item["end"]), None)
        paragraph = next((item["paragraph"] for item in layout["segments"] if item["start"] <= start and end <= item["end"]), None)
        result.append({"start": start, "end": end, "page": page, "paragraph": paragraph})
        position = start + 1
    return result


def normalize_month(value: str) -> str | None:
    match = DATE_RE.fullmatch(value.strip())
    return f"{match[1]}-{int(match[2]):02}" if match else None


def normalized_field_value(field: str | None, raw_value: str) -> str | bool | None:
    if field in ("start_date", "end_date"):
        return normalize_month(raw_value)
    if field == "end_date_kind":
        return {"预计": "expected", "预计毕业": "expected", "expected": "expected", "已毕业": "actual", "实际毕业": "actual", "actual": "actual"}.get(raw_value.strip())
    if field == "is_current":
        return True if raw_value.strip() in ("至今", "进行中", "在职", "current", "present") else None
    if field == "experience_type":
        return {"实习": "internship", "实习经历": "internship", "internship": "internship", "工作": "work", "工作经历": "work", "work": "work"}.get(raw_value.strip())
    if field == "name":
        return ALIASES.get(raw_value.casefold(), raw_value)
    return raw_value


def _normalized(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", value) if not char.isspace() and unicodedata.category(char) != "Cf").casefold()


def _literal_supported(value: str, quote: str, raw_text: str, locations: list[dict]) -> bool:
    if _normalized(value) not in _normalized(quote):
        return False
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.+#-]*", value):
        matches = list(re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(value) + r"(?![A-Za-z0-9_])", quote, re.I))
        if not matches:
            return False
        for location in locations:
            if not any(
                (location["start"] + match.start() == 0 or not _ascii_word(raw_text[location["start"] + match.start() - 1]))
                and (location["start"] + match.end() == len(raw_text) or not _ascii_word(raw_text[location["start"] + match.end()]))
                for match in matches
            ):
                return False
    return True


def _ascii_word(char: str) -> bool:
    return (char.isascii() and (char.isalnum() or char == "_")) or unicodedata.category(char).startswith("M") or unicodedata.category(char) == "Cf" or (unicodedata.normalize("NFKC", char).isascii() and unicodedata.normalize("NFKC", char).isalnum())


def _context(raw_text: str, start: int, end: int) -> str:
    lower, upper = max(0, start - 500), min(len(raw_text), end + 500)
    left = max((raw_text.rfind(mark, lower, start) for mark in "\n\r。！？;；"), default=-1)
    rights = [point for mark in "\n\r。！？;；" if (point := raw_text.find(mark, end, upper)) >= 0]
    return raw_text[max(lower, left + 1):min(rights) if rights else upper]


def _context_truncated(raw_text: str, start: int, end: int) -> bool:
    lower, upper = max(0, start - 500), min(len(raw_text), end + 500)
    breaks = "\n\r。！？;；"
    return (lower > 0 and not any(mark in raw_text[lower:start] for mark in breaks)) or (upper < len(raw_text) and not any(mark in raw_text[end:upper] for mark in breaks))


def evaluate_metadata(metadata: CandidateExtractionMetadata, raw_text: str, layout: dict | None = None) -> tuple[dict, list[dict], CandidateExtractionMetadata]:
    """Recompute every quote/location/value from raw leaves at the ingestion fence."""
    metadata = CandidateExtractionMetadata.model_validate(metadata.model_dump(warnings=False))
    scope_locations = locate_quote(raw_text, metadata.scope_text, layout) if metadata.scope_text else []
    metadata.scope_locations = [EvidenceLocation.model_validate(item) for item in scope_locations]
    reasons = [reason for reason in metadata.review_reasons if reason in ("overlapping_experience_scopes", "conflicting_proposals")]
    if metadata.scope_text and not scope_locations:
        reasons.append("scope_not_found")
    elif metadata.scope_text and len(scope_locations) != 1:
        reasons.append("ambiguous_scope")
    if metadata.scope_text and INJECTION_RE.search(metadata.scope_text):
        reasons.append("instruction_like_source")
    value: dict[str, Any] = {}
    evidence = []
    description_fields = []
    unsupported = "scope_not_found" in reasons
    for trace in metadata.fields:
        locations = locate_quote(raw_text, trace.source_text, layout)
        outside_scope = False
        if metadata.scope_text and len(scope_locations) == 1:
            scoped = locate_quote(raw_text, trace.source_text, layout, lower=scope_locations[0]["start"], upper=scope_locations[0]["end"])
            outside_scope = bool(locations) and not scoped
            locations = scoped
        trace.locations = [EvidenceLocation.model_validate(item) for item in locations]
        field_reasons = []
        if not locations:
            field_reasons.append("source_text_not_found")
        elif not _literal_supported(trace.raw_value, trace.source_text, raw_text, locations):
            field_reasons.append("value_not_supported_by_quote")
        if outside_scope or (metadata.scope_text and any(not any(scope["start"] <= loc["start"] and loc["end"] <= scope["end"] for scope in scope_locations) for loc in locations)):
            field_reasons.append("evidence_outside_entry_scope")
        if len(locations) > 1:
            field_reasons.append("ambiguous_quote_occurrences")
        source_limit = scope_locations[0]["end"] if metadata.scope_text and len(scope_locations) == 1 else len(raw_text)
        if len(locations) == MAX_OCCURRENCES and raw_text.find(trace.source_text, locations[-1]["start"] + 1, source_limit) >= 0:
            field_reasons.append("quote_occurrence_limit")
        if any(_context_truncated(raw_text, loc["start"], loc["end"]) for loc in locations):
            field_reasons.append("context_truncated")
        if trace.assertion != "affirmed":
            field_reasons.append(f"model_assertion_{trace.assertion}")
        contexts = [_context(raw_text, loc["start"], loc["end"]) for loc in locations]
        if any(UNCERTAIN_RE.search("".join(char for char in unicodedata.normalize("NFKC", context) if unicodedata.category(char) != "Cf")) for context in contexts):
            field_reasons.append("negative_planned_or_uncertain_context")
        if any(INJECTION_RE.search(context) for context in contexts):
            field_reasons.append("instruction_like_source")
        trace.normalized_value = normalized_field_value(trace.mapped_field, trace.raw_value)
        if trace.mapped_field and trace.normalized_value is None:
            field_reasons.append("unrecognized_normalization")
        trace.review_reasons = field_reasons
        unsupported |= any(reason in field_reasons for reason in ("source_text_not_found", "value_not_supported_by_quote", "evidence_outside_entry_scope"))
        reasons.extend(field_reasons)
        if trace.mapped_field == "description":
            description_fields.append(trace)
        elif trace.mapped_field and trace.normalized_value is not None:
            if trace.mapped_field in value and value[trace.mapped_field] != trace.normalized_value:
                reasons.append("conflicting_field_values")
            else:
                value[trace.mapped_field] = trace.normalized_value
                evidence.append({"field": trace.mapped_field, "source_text": trace.source_text})
    if description_fields:
        description_locations = [trace.locations[0] for trace in description_fields if len(trace.locations) == 1]
        if len(description_locations) == len(description_fields):
            start = min(item.start for item in description_locations)
            end = max(item.end for item in description_locations)
            if end - start <= 500:
                description = raw_text[start:end]
                value["description"] = description
                evidence.append({"field": "description", "source_text": description})
            else:
                reasons.append("description_requires_user_edit")
        else:
            reasons.append("description_requires_user_edit")
    # S2-1 uses graduation month; graduation kind cannot exist without it.
    if "end_date_kind" in value and "end_date" not in value:
        value.pop("end_date_kind")
        evidence = [item for item in evidence if item["field"] != "end_date_kind"]
        reasons.append("graduation_month_missing")
    if value.get("is_current") is True and value.get("end_date") is not None:
        value.pop("is_current")
        evidence = [item for item in evidence if item["field"] != "is_current"]
        reasons.append("conflicting_current_and_end_date")
    if value.get("start_date") and value.get("end_date") and value["start_date"] > value["end_date"]:
        value.pop("end_date")
        evidence = [item for item in evidence if item["field"] != "end_date"]
        reasons.append("conflicting_date_order")
    metadata.review_reasons = list(dict.fromkeys(reasons))[:20]
    metadata.evidence_status = "unsupported" if unsupported else "needs_review" if reasons else "supported"
    return value, evidence, metadata
