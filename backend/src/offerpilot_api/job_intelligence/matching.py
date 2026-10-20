"""Bounded, offline matching of confirmed facts and reviewed requirements.

This module intentionally recognises a small grammar. Unsupported wording stays
unknown. A missing entry never proves inability, and candidates are never read.
Changing the grammar or alias relationships requires a new RULE_VERSION.
"""

import re
from dataclasses import dataclass, field

from offerpilot_api.job_catalog.schemas import JobDetailResponse, JobRequirementResponse
from offerpilot_api.job_intelligence.match_schemas import (
    ExcludedRequirement, JDMatchEvidence, MatchEvaluation, MatchItem,
    MatchStatus, ProfileMatchEvidence,
)
from offerpilot_api.profile.schemas import CareerProfileResponse

RULE_VERSION = "match-rules-1.0"
SKILL_ALIAS_VERSION = "skill-aliases-1.0"

# Directed compatibility: Python 3 proves Python, never the reverse. Names such
# as JavaScript / Java and React Native / React are distinct whole tokens.
_SKILLS = {
    "python": "python", "python3": "python3", "python 3": "python3",
    "python 2": "python2", "python2": "python2",
    "java": "java", "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript", "react": "react",
    "react native": "react_native", "reactnative": "react_native",
    "vue": "vue", "vue.js": "vue", "vuejs": "vue", "sql": "sql",
    "mysql": "mysql", "postgresql": "postgresql", "redis": "redis",
    "linux": "linux", "git": "git", "go": "go", "golang": "go",
    "c++": "cpp", "c#": "csharp", "docker": "docker",
}
_SKILL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(k) for k in sorted(_SKILLS, key=len, reverse=True)) + r")(?![A-Za-z0-9_.])", re.I,
)
_DEGREE_NAMES = {"博士": 5, "硕士": 4, "研究生": 4, "本科": 3, "学士": 3, "大专": 2, "专科": 2, "高中": 1, "中专": 1}
_DEGREE_PATTERN = re.compile("|".join(_DEGREE_NAMES))
_NEGATIVE = re.compile(r"不会|未掌握|不掌握|未学过|没有学过|未接触|没有接触|从未学|不具备|不熟悉|没有.*(?:经验|经历)|无.*(?:经验|经历)|\b(?:never learned|do not know|don't know|no experience)\b", re.I)
_PLANNED = re.compile(r"计划|准备学|将学习|打算|希望学习|待学习|尚在学习|正在学习|学习中|\b(?:plan(?:ning)? to learn|intend to learn|will learn|learning)\b", re.I)
_UNCLEAR_SKILL = re.compile(r"零基础|没有基础|未学习|尚未学习|\b(?:not proficient|not familiar|not yet|beginner)\b", re.I)
_EXPERIENCE_NEGATION = re.compile(r"未|没有|不曾|不负责|不使用|不用于|并非|不是|\b(?:not|never|no|without|didn['’]t|haven['’]t|hasn['’]t|cannot)\b", re.I)
_EXPERIENCE_ATTRIBUTION = re.compile(r"旁观|同学|同事|他人|其他人|仅阅读|只阅读|观察|围观|非本人|别人|\b(?:observer|observed|colleague|classmate|other people)\b", re.I)
_PART_TIME = re.compile(r"兼职|非全职|part[ -]?time", re.I)
_AMBIGUOUS = re.compile(r"视情况|可协商|面议|不限|相关专业|同等|等同|之一|至少一种|需核实|待核实|待确认|可能|最好|尽量|不限于|等技术|等技能")
_PUNCTUATION = re.compile(r"[\s，,。；;：:、（）()\[\]【】.!！]")


@dataclass
class _Judgment:
    status: MatchStatus
    reason: str
    evidence: list[ProfileMatchEvidence] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)


def _unknown(reason: str, *verification: str, evidence=None) -> _Judgment:
    return _Judgment("unknown", reason, evidence or [], list(verification) or ["请核实该条件的具体含义，并补充相应的正式画像事实。"])


def _evidence(entry, category: str, *fields: str) -> list[ProfileMatchEvidence]:
    result = []
    for name in fields:
        value = getattr(entry, name, None)
        if value is None:
            continue
        source = entry.source
        quote = next((e.source_text[:500] for e in source.evidence if e.field == name and e.supports_current_value), None)
        result.append(ProfileMatchEvidence(
            entry_id=entry.id, category=category, field=name,
            value=str(value).lower() if isinstance(value, bool) else str(value),
            source_kind=source.field_sources.get(name, source.kind), resume_id=source.resume_id,
            source_text=quote,
        ))
    return result


def _plain(text: str) -> str:
    return _PUNCTUATION.sub("", text).strip()


def _fully_consumed(text: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.I)
    return not _plain(text)


def _unique_evidence(items: list[ProfileMatchEvidence]) -> list[ProfileMatchEvidence]:
    return list({(e.entry_id, e.category, e.field, e.value): e for e in items}.values())


def _combine(parts: list[_Judgment], priority: str) -> _Judgment:
    evidence = _unique_evidence([e for p in parts for e in p.evidence])
    verification = list(dict.fromkeys(v for p in parts for v in p.verification))
    if any(p.status == "unmet" for p in parts) and priority == "must":
        status = "unmet"
    elif all(p.status == "met" for p in parts):
        status = "met"
    elif any(p.status in {"met", "partial"} for p in parts):
        status = "partial"
    else:
        status = "unknown"
    return _Judgment(status, "；".join(p.reason for p in parts), evidence, verification)


def _choose(parts: list[_Judgment], priority: str) -> _Judgment:
    """An OR across independent entries; never splice fields between entries."""
    for status in ("met", "partial", "unknown"):
        matches = [p for p in parts if p.status == status]
        if matches:
            return matches[0]
    if parts:
        chosen = parts[0]
        if priority != "must" and chosen.status == "unmet":
            return _Judgment("partial", chosen.reason, chosen.evidence, chosen.verification)
        return chosen
    return _unknown("正式画像尚无对应事实；未填写不代表不具备。", "请补充或确认对应的个人资料。")


def _degree_value(value: str | None, *, formal_fact=False):
    value = (value or "").replace("博士研究生", "博士").replace("硕士研究生", "硕士")
    if formal_fact and "研究生" in value:
        # Generic postgraduate education does not identify master's/doctorate.
        return None
    names = _DEGREE_PATTERN.findall(value or "")
    levels = {_DEGREE_NAMES[name] for name in names}
    if len(levels) != 1 or _NEGATIVE.search(value or "") or _PLANNED.search(value or ""):
        return None
    if formal_fact and not _fully_consumed(value or "", [r"最高学历|学历|学位|博士|硕士|研究生|本科|学士|大专|专科|高中|中专|在读|已取得|已获得|已毕业"]):
        # A degree word inside '非本科', '本科肄业', '本科及以上',
        # '本科未毕业' etc. is not a confirmed degree fact.
        return None
    return next(iter(levels))


def _education_state(entry) -> str:
    text = entry.degree or ""
    # Actual and expected are explicit facts, not inferred from today's date.
    if entry.end_date_kind == "actual" and "在读" in text or entry.end_date_kind == "expected" and re.search(r"已取得|已毕业|已获得", text):
        return "conflicting"
    if entry.end_date_kind == "expected" or "在读" in text:
        return "enrolled"
    if entry.end_date_kind == "actual" or re.search(r"已取得|已毕业|已获得", text):
        return "acquired"
    return "unknown"


def _year_range(text: str):
    interval = re.search(r"((?:19|20|21)\d{2})\s*(?:届)?\s*[-—–~～至到]\s*((?:19|20|21)\d{2})\s*(?:届|年)", text)
    if interval:
        lo, hi = map(int, interval.groups())
        return (lo, hi) if lo <= hi else None
    years = re.findall(r"(?:19|20|21)\d{2}", text)
    if len(years) == 1 and re.search(r"(?:届|年.*毕业|毕业.*年)", text):
        return int(years[0]), int(years[0])
    return None


_YEAR_GRAMMAR = [r"(?:19|20|21)\d{2}\s*(?:届|年)?", r"[-—–~～至到]", r"预计|预期|实际|已|于|在|的|毕业生|毕业|应届|届别|届|时间|年份|为|须|必须|要求|仅限|面向|限|且|以及|及|年"]


def _graduation_entry(entry, text: str, priority: str) -> _Judgment:
    evidence = _evidence(entry, "education", "degree", "end_date", "end_date_kind")
    if _education_state(entry) == "conflicting":
        return _unknown("该正式教育条目的在读/已毕业描述与毕业类型冲突，不能据此确定届别。", "请先核对教育经历的预计/实际毕业状态。", evidence=evidence)
    interval = _year_range(text)
    if interval is None:
        return _unknown("“应届”的时间范围未明确，不能自行定义届别。", "请向招聘方核实适用的毕业年份及预计毕业是否可接受。", evidence=evidence)
    if not entry.end_date or entry.end_date_kind is None:
        return _unknown("正式教育条目的毕业时间或预计/实际语义未知。", "请补充该教育经历的毕业年月及预计/实际毕业类型。", evidence=evidence)
    year = int(entry.end_date[:4])
    requires_actual = bool(re.search(r"实际|已经|已.*毕业|已取得|已获得", text))
    if requires_actual and re.search(r"预计|预期", text):
        return _unknown("条件同时包含预计与实际毕业措辞，时间含义需要核实。", "请明确要求预计毕业还是已经毕业。", evidence=evidence)
    if requires_actual and entry.end_date_kind != "actual":
        return _Judgment("partial", "该条目记录的是预计毕业，不能作为已经毕业的证据。", evidence, ["请核实岗位是否接受预计毕业；实际毕业后更新正式画像。"])
    if interval[0] <= year <= interval[1]:
        return _Judgment("met", f"正式画像的{'预计' if entry.end_date_kind == 'expected' else '实际'}毕业年份为{year}，处于岗位明确的届别范围内。", evidence)
    return _Judgment("unmet" if priority == "must" else "partial", f"正式画像的{'预计' if entry.end_date_kind == 'expected' else '实际'}毕业年份为{year}，与岗位明确要求的{interval[0]}—{interval[1]}届冲突。", evidence, ["请核实招聘方是否允许该届别的例外；系统不会替你拒绝岗位。"])


def _graduation(profile, req) -> _Judgment:
    if not _fully_consumed(req.content, _YEAR_GRAMMAR):
        return _unknown("毕业条件含有当前规则不能可靠判断的附加要求。", "请将届别和其他资格拆为独立条件后审核。")
    return _choose([_graduation_entry(e, req.content, req.priority) for e in profile.education], req.priority)


def _education(profile, req) -> _Judgment:
    text = req.content
    target = _degree_value(text)
    grammar = [r"博士|硕士|研究生|本科|学士|大专|专科|高中|中专", r"学历|学位|及以上|以上|至少|不低于|最低|在读|学生|已取得|已获得|已毕业|取得|获得|完成|具有|具备|持有|为|须|必须|要求|且|的|及|及其"] + _YEAR_GRAMMAR
    if target is None or not _fully_consumed(text, grammar):
        return _unknown("学历条件包含未支持的层级或附加资格，不能只识别一个学历词就判定满足。", "请核实并拆分全日制、学校类别、专业等附加要求。")
    need_enrolled = "在读" in text
    interval = _year_range(text)
    if "应届" in text and interval is None:
        return _unknown("岗位的“应届”范围未定义，学历和届别需要一并核实。", "请核实招聘方接受的毕业年份及学历取得时间。")
    known = [(e, _degree_value(e.degree, formal_fact=True)) for e in profile.education]
    for entry, level in known:
        if "最高学历" not in (entry.degree or "") or level is None:
            continue
        contradictory = [other for other, other_level in known if other.id != entry.id and other_level is not None and other_level > level and _education_state(other) == "acquired"]
        if contradictory:
            return _unknown("正式画像的最高学历声明与另一条已取得学历互相冲突，不能只选择有利的条目。", "请先核对并修正最高学历声明和对应教育经历。", evidence=_unique_evidence([e for item in [entry] + contradictory for e in _evidence(item, "education", "degree", "end_date", "end_date_kind")]))
    minimum = bool(re.search(r"以上|至少|不低于|最低", text))
    parts = []
    for entry in profile.education:
        evidence = _evidence(entry, "education", "degree", "end_date", "end_date_kind")
        level = _degree_value(entry.degree, formal_fact=True)
        state = _education_state(entry)
        if state == "conflicting":
            degree = _unknown("同一正式教育条目的在读/已取得描述与毕业类型互相冲突。", "请先修正该教育条目的学历状态与预计/实际毕业类型。", evidence=evidence)
        elif level is None:
            degree = _unknown("该教育条目的学历层级未知。", "请确认学历层级。", evidence=evidence)
        elif level < target:
            # A lower recorded degree is not proof of the user's highest degree.
            if "最高学历" in (entry.degree or "") and req.priority == "must":
                degree = _Judgment("unmet", "用户明确声明的最高学历低于岗位硬性要求。", evidence, ["请核实招聘方是否接受同等条件或例外。"])
            else:
                degree = _Judgment("partial", "当前教育条目的学历低于要求，但不能据此断言没有其他学历。", evidence, ["请补充是否还有满足要求的已取得学历；未发现证据不代表不具备。"])
        elif level > target and not minimum:
            degree = _Judgment("partial", "岗位未明确接受更高学历；更高层次的教育条目不能证明特定层次的在读身份或已取得学历。", evidence, ["请核实岗位是否接受更高层次学历，或补充指定层次的教育经历。"])
        elif need_enrolled and state == "enrolled":
            degree = _Judgment("met", "该正式教育条目明确为在读，学历层级达到岗位要求。", evidence)
        elif need_enrolled:
            degree = _unknown("岗位要求在读，当前条目未明确符合在读身份。", "请核实是否接受已毕业者或补充当前在读经历。", evidence=evidence)
        elif state == "acquired":
            degree = _Judgment("met", "该正式教育条目记录了已取得的学历，层级达到岗位要求。", evidence)
        elif interval and state == "enrolled":
            # Explicit graduate cohort recruitment accepts expected graduation;
            # the result describes that fact instead of claiming a degree held.
            degree = _Judgment("met", "该教育条目的在读学历层级达到明确届别招聘要求；并未将预计毕业写作已取得学历。", evidence)
        elif state == "enrolled":
            degree = _Judgment("partial", "正式画像记录的是在读/预计毕业，不能等同于已经取得该学历。", evidence, ["请核实岗位是否接受在读学历，或补充已取得学历。"])
        else:
            degree = _unknown("学历名称已记录，但是否已取得或当前在读未知。", "请补充预计/实际毕业时间或明确在读/已取得状态。", evidence=evidence)
        parts.append(_combine([degree, _graduation_entry(entry, text, req.priority)] if interval else [degree], req.priority))
    return _choose(parts, req.priority)


def _major(profile, req) -> _Judgment:
    text = req.content
    if re.search(r"相关|类别|大类|或|/|之一|及|与.*专业.*和", text):
        return _unknown("当前没有经过验证的专业分类表，不能自行解释“相关专业”或专业类别范围。", "请向招聘方核实可接受的具体专业；可将确认的精确专业要求另存为条件。")
    target = re.sub(r"^(?:必须|要求|须|限|仅限|为|具有|具备|专业要求[：:]?)*", "", text).strip()
    target = re.sub(r"(?:(?:专业|毕业生|优先考虑|优先|背景|的))+$", "", target).strip()
    if not target or re.search(r"要求|经验|学历|熟悉|掌握|本科|硕士|博士|具备|以及|且", target):
        return _unknown("专业条件含有未支持的复合语义。", "请将精确专业名称和其他要求分别审核。")
    evidence = []
    for entry in profile.education:
        evidence += _evidence(entry, "education", "major")
        if entry.major and _plain(entry.major.removesuffix("专业")) == _plain(target):
            return _Judgment("met", "正式教育条目的专业名称与岗位要求精确一致；未进行专业类别推断。", _evidence(entry, "education", "major"))
    return _unknown("未发现与岗位精确专业要求一致的正式画像事实；其他专业名称不自动证明不符合。", "请核实招聘方接受的专业范围，或补充对应教育条目。", evidence=evidence)


def _skill_name(value: str | None):
    return _SKILLS.get((value or "").strip().lower())


def _skill_implies(actual: str, target: str) -> bool:
    return actual == target or target == "python" and actual in {"python2", "python3"}


def _skill_level(value: str | None):
    if not value:
        return None
    if re.fullmatch(r"精通", value):
        return 4
    if re.fullmatch(r"熟练|熟练掌握|熟练使用", value):
        return 3
    if re.fullmatch(r"熟悉|掌握|能够使用|能使用", value):
        return 2
    if re.fullmatch(r"了解|基础|入门|初学", value):
        return 1
    return None


def _skill_entry(entry, target: str, required_level: int, priority: str) -> _Judgment | None:
    raw = " ".join(v for v in (entry.name, entry.level) if v)
    name = _skill_name(entry.name)
    # Guard negation/plans even if embedded in the name rather than level.
    if name is None:
        tokens = [_SKILLS[m.group().lower()] for m in _SKILL_PATTERN.finditer(entry.name or "")]
        if len(tokens) != 1 or not (_PLANNED.search(raw) or _NEGATIVE.search(raw) or _UNCLEAR_SKILL.search(raw)):
            return None
        name = tokens[0]
    if not _skill_implies(name, target):
        return None
    evidence = _evidence(entry, "skill", "name", "level")
    level_text = entry.level or ""
    level_tokens = [_SKILLS[m.group().lower()] for m in _SKILL_PATTERN.finditer(level_text)]
    if any(not (_skill_implies(actual, name) or _skill_implies(name, actual)) for actual in level_tokens):
        return _unknown("该技能的程度字段混入其他技术，无法可靠确定否定或程度的归属。", "请将每种技术的能力及程度分别记录到独立技能条目。", evidence=evidence)
    if _PLANNED.search(raw) or _NEGATIVE.search(raw) or _UNCLEAR_SKILL.search(raw):
        statement = _plain(_SKILL_PATTERN.sub("", raw)).lower()
        direct = {
            "计划学习", "计划", "准备学习", "准备学", "将学习", "打算学习", "希望学习", "待学习", "尚在学习", "正在学习", "学习中",
            "不会", "未掌握", "尚未掌握", "不掌握", "未学过", "尚未学过", "没有学过", "未接触", "没有接触", "从未学过", "不具备", "不熟悉",
            "零基础", "没有基础", "未学习", "尚未学习", "notproficient", "notfamiliar", "notyetfamiliar", "notyet", "beginner",
            "plantolearn", "planningtolearn", "intendtolearn", "willlearn", "learning", "neverlearned", "donotknow", "don'tknow", "noexperience",
        }
        if statement not in direct:
            return _unknown("技能字段中的否定或计划含有多分句/额外语义，不能把否定自动归到该技术。", "请将技能名称与实际程度分别填写，避免混合多个事实。", evidence=evidence)
    if _PLANNED.search(raw):
        return _unknown("该正式技能条目表达的是计划或正在学习，不能当作已经掌握。", "请在确实具备后明确更新技能及程度；目前不能据此判定满足。", evidence=evidence)
    if _NEGATIVE.search(raw):
        return _Judgment("unmet" if priority == "must" else "partial", "用户已确认的技能条目明确否定该能力，与岗位要求冲突。", evidence, ["请核实该技术是否为不可替代的硬性要求。"])
    if _UNCLEAR_SKILL.search(raw):
        return _unknown("该技能条目表达基础不足或程度尚待确认，不能只凭技术名称认定具备。", "请核实该技术的实际能力及岗位所需程度。", evidence=evidence)
    if entry.level is not None and _skill_level(entry.level) is None:
        return _unknown("该技能的程度字段不属于已支持的明确程度词，不能只凭名称忽略该字段的含义。", "请确认实际程度，并使用了解、熟悉、掌握、熟练或精通等明确表述。", evidence=evidence)
    if required_level:
        level = _skill_level(entry.level)
        if level is None:
            return _Judgment("partial", "技能名称有正式证据，但熟练程度未知，不能确认岗位所需程度。", evidence, ["请核实并更新该技能的实际熟练程度。"])
        if level < required_level:
            return _Judgment("partial", "已确认该技能，但目前声明的程度低于岗位描述；程度词并非客观考试结论。", evidence, ["请结合实际面试或作品要求核实技术深度。"])
    return _Judgment("met", f"正式技能条目与岗位技术名称及已明确的程度相符（别名表 {SKILL_ALIAS_VERSION}）。", evidence)


def _skills(profile, req) -> _Judgment:
    text = req.content
    matches = list(_SKILL_PATTERN.finditer(text))
    targets = list(dict.fromkeys(_SKILLS[m.group().lower()] for m in matches))
    scrubbed = _SKILL_PATTERN.sub("", text)
    grammar = [r"必须|须|必备|要求|具备|具有|拥有|能够|能|至少|熟练掌握|熟练使用|精通|熟练|熟悉|掌握|了解|会|使用|应用|优先考虑|优先|技术|技能|开发语言|编程语言|语言|和|与|及|且|或|/|&|\+"]
    # Any unconsumed experience duration, technology, framework or qualifier
    # makes the entire clause unknown, even if a familiar skill was found.
    if not targets or not _fully_consumed(scrubbed, grammar):
        return _unknown("技术条件含有当前规则未覆盖的技术、年限或复合要求，不能仅命中一个关键词就判定满足。", "请将技术名称、熟练程度、经验年限等拆为独立条件并审核。")
    if re.search(r"或|/", scrubbed) and re.search(r"和|与|及|且|&|\+|[,，、;；]", scrubbed):
        return _unknown("技术条件同时包含“并且”和“或者”，选择范围不明确。", "请明确括号和选择范围，或拆分为独立技术条件。")
    if re.search(r"精通", text):
        level = 4
    elif re.search(r"熟练", text):
        level = 3
    elif re.search(r"熟悉|掌握", text):
        level = 2
    elif re.search(r"了解", text):
        level = 1
    else:
        level = 0
    parts = []
    for target in targets:
        entry_results = [(e, result) for e in profile.skills if (result := _skill_entry(e, target, level, req.priority)) is not None]
        positive = [r for _, r in entry_results if r.status == "met"]
        # State and the presence of an explicit level determine conflicts.
        # Never classify facts by searching the user-facing explanation text.
        # An absent level is only missing detail; a provided unknown/negative/
        # planned level cannot be silently overridden by a favourable row.
        conflicting = [r for e, r in entry_results if r.status in {"unmet", "unknown"} or r.status == "partial" and e.level is not None]
        if positive and conflicting:
            parts.append(_unknown("正式技能条目对同一技术存在程度差异、未知、否定或计划表述，不能选择有利的一条掩盖冲突。", "请核实并修正互相冲突或含义不清的正式技能条目。", evidence=_unique_evidence([e for r in positive + conflicting for e in r.evidence])))
        else:
            parts.append(_choose([r for _, r in entry_results], req.priority))
    if re.search(r"或|/", scrubbed) and len(parts) > 1:
        return _choose(parts, req.priority)
    return _combine(parts, req.priority)


def _months(entry):
    # Ongoing and incomplete dates cannot be projected using the current date.
    if not entry.start_date or not entry.end_date or entry.is_current is True:
        return None
    sy, sm = map(int, entry.start_date.split("-"))
    ey, em = map(int, entry.end_date.split("-"))
    # Only fully intervening months are guaranteed. Jan -> Apr could mean
    # Jan 31 -> Apr 1, so a three-month difference does not prove three months.
    return max(0, (ey - sy) * 12 + em - sm - 1)


def _duration_required(text: str):
    match = re.search(r"(\d{1,2})\s*(?:个)?\s*(月|年)", text)
    if match:
        return int(match[1]) * (12 if match[2] == "年" else 1)
    return None


def _technology_usage(text: str, target: str) -> bool:
    """Require a direct action adjoining the technology in this entry.

    Merely mentioning another person's stack or a technical keyword is not
    evidence that the user used it. Complex usage prose stays unknown.
    """
    for match in _SKILL_PATTERN.finditer(text):
        actual = _SKILLS[match.group().lower()]
        if not _skill_implies(actual, target):
            continue
        before, after = text[:match.start()], text[match.end():]
        if re.search(r"(?:使用|采用|基于|用|通过|以)\s*(?:技术|语言|框架|数据库|工具)?\s*$", before) or re.match(r"\s*(?:开发|编写|实现|应用|维护|构建|搭建|部署|调试|优化)", after):
            return True
        if re.search(r"\b(?:used|using|with|in)\s+$", before, re.I):
            return True
    return False


def _experience(profile, req) -> _Judgment:
    text = req.content
    kind = "project" if req.type == "project" else "internship" if req.type == "internship_experience" else "work"
    # A course project is a project, but never an employment fact. Requirement
    # qualifiers (real/production/commercial) cannot be silently discarded.
    grammar = [r"必须|须|要求|至少|不少于|具备|具有|拥有|有|累计|优先考虑|优先|一段|一项|项目|经历|经验|实习|全职|正式工作|工作|个月|月|年|\d+|的|及以上|以上|和|与|及|且"]
    targets = list(dict.fromkeys(_SKILLS[m.group().lower()] for m in _SKILL_PATTERN.finditer(text)))
    scrubbed = _SKILL_PATTERN.sub("", text)
    if not _fully_consumed(scrubbed, grammar) or "累计" in text:
        return _unknown("经历条件含有相关性、真实业务、累计年限或其他尚不能可靠判定的要求。", "请核实经历的具体范围；系统不会合并不同经历的技术与成果。")
    threshold = _duration_required(text)
    entries = profile.projects if kind == "project" else [e for e in profile.experiences if e.experience_type == ("internship" if kind == "internship" else "work")]
    parts = []
    for entry in entries:
        category = "project" if kind == "project" else "experience"
        fields = ("name", "role", "description", "start_date", "end_date", "is_current") if kind == "project" else ("organization", "role", "experience_type", "description", "start_date", "end_date", "is_current")
        evidence = _evidence(entry, category, *fields)
        own_text = " ".join(str(getattr(entry, name) or "") for name in ("role", "description"))
        full_text = " ".join(str(getattr(entry, name) or "") for name in fields)
        if _NEGATIVE.search(full_text) or _PLANNED.search(full_text) or _EXPERIENCE_NEGATION.search(full_text):
            parts.append(_unknown("该条经历包含否定或计划表述，不能作为已完成经验。", "请确认该条经历中实际完成的职责。", evidence=evidence))
            continue
        if _EXPERIENCE_ATTRIBUTION.search(full_text):
            parts.append(_unknown("经历中提及旁观或他人行为，不能把技术使用归到用户本人。", "请明确该条经历中本人实际完成的职责和使用的技术。", evidence=evidence))
            continue
        if kind == "work" and _PART_TIME.search(full_text):
            parts.append(_unknown("该工作条目含兼职或非全职描述，不能作为全职工作年限证据。", "请核实工作性质和全职年限口径。", evidence=evidence))
            continue
        if kind == "work" and re.search(r"全职|正式工作", text) and not re.search(r"全职|full[ -]?time", own_text, re.I):
            parts.append(_Judgment("partial", "正式条目仅标为工作，尚未明确全职性质，不能据此计算全职工作年限。", evidence, ["请在该条经历中确认全职性质，或向招聘方核实工作年限口径。"] ))
            continue
        if not own_text.strip():
            parts.append(_Judgment("partial", "正式条目仅有名称或时间，尚无具体职责或完成内容的证据。", evidence, ["请在对应条目补充实际职责或完成内容。"] ))
            continue
        # Exact technology mentions remain attached to this entry. They prove
        # usage only, never skill proficiency or another entry's achievements.
        if targets and not all(_technology_usage(own_text, target) for target in targets):
            parts.append(_unknown("同一条正式经历中未发现全部要求的技术证据；不会拼接不同条目的技术。", "请补充该条经历实际使用的技术，或核实招聘方接受的相关经历。", evidence=evidence))
            continue
        if threshold is not None:
            duration = _months(entry)
            if duration is None:
                parts.append(_Judgment("partial", "存在该类经历，但起止月份不完整或仍在进行，不能推算已完成时长。", evidence, ["请补充完整起止年月，或向招聘方核实计算口径。社招年限不计入实习。"] ))
            elif duration >= threshold:
                parts.append(_Judgment("met", f"同一条正式{'项目' if kind == 'project' else '实习' if kind == 'internship' else '工作'}经历有对应证据，按起止月份保守可确认至少{duration}个完整月，达到{threshold}个月要求。", evidence))
            else:
                parts.append(_Judgment("partial", f"该条经历按起止月份保守可确认至少{duration}个完整月，尚不足以证明{threshold}个月；不能断言没有其他未记录经历。", evidence, ["请核实具体起止日、其他相关经历及招聘方的累计/重叠时长口径。"] ))
        else:
            parts.append(_Judgment("met", f"发现同一条正式{'项目' if kind == 'project' else '实习' if kind == 'internship' else '工作'}经历的具体事实；未把项目或实习折算为全职工作。", evidence))
    return _choose(parts, req.priority)


def _group(kind: str):
    if kind in {"education", "graduation", "major"}:
        return "education"
    if kind in {"technical_skill", "language"}:
        return "skills"
    if kind in {"experience", "internship_experience", "project"}:
        return "experience"
    return "work_conditions"


def _judge(profile, req) -> _Judgment:
    if req.priority == "uncertain":
        return _unknown("该条件虽然已审核，但分类仍为“待核实”，不能当作明确硬性条件。", "请向招聘方核实要求及必须/优先分类。")
    if _AMBIGUOUS.search(req.content):
        return _unknown("该条件的适用范围或含义仍有歧义，当前规则不能给出确定判断。", "请核实条件的具体范围，并拆分成明确要求。")
    if _PLANNED.search(req.content) or _NEGATIVE.search(req.content):
        return _unknown("该条件包含计划或否定语义，当前规则不将其改写成正向资格。", "请核实招聘方实际要求，并保留原意拆分审核。")
    evaluator = {"education": _education, "graduation": _graduation, "major": _major,
                 "technical_skill": _skills, "project": _experience,
                 "experience": _experience, "internship_experience": _experience}.get(req.type)
    if evaluator:
        return evaluator(profile, req)
    return _unknown("当前正式画像没有可可靠判定该类资格的结构化事实，保留为未知。", "请与招聘方核实该要求；工作条件与个人求职偏好在下方分别对比。")


def _jd_evidence(req):
    return JDMatchEvidence(**{name: getattr(req, name) for name in ("evidence_text", "source_jd_version", "source_start", "source_end", "line_start", "line_end", "source_kind")})


def _pref_evidence(field_name, value):
    return [ProfileMatchEvidence(entry_id=None, category="preference", field=field_name, value=str(value), source_kind="manual")]


def _salary(text: str | None):
    """Only explicit CNY, period and tax basis; no inferred conversions."""
    if not text:
        return None
    compact = re.sub(r"\s+", "", text)
    currency = "CNY" if re.search(r"人民币|CNY|RMB|元", compact, re.I) else None
    periods = [name for name, pattern in (("month", r"/月|每月|月薪"), ("day", r"/天|/日|每天|每日|日薪"), ("year", r"/年|每年|年薪")) if re.search(pattern, compact)]
    taxes = [name for name, marker in (("pre_tax", "税前"), ("after_tax", "税后")) if marker in compact]
    ranges = list(re.finditer(r"(?<!\d)(\d+(?:\.\d+)?)\s*[-—–~～至到]\s*(\d+(?:\.\d+)?)(?!\d)", compact))
    if not currency or len(periods) != 1 or len(taxes) != 1 or len(ranges) != 1:
        return None
    period, tax, numbers = periods[0], taxes[0], ranges[0]
    if not _fully_consumed(compact, [r"人民币|CNY|RMB|元|税前|税后|月薪|日薪|年薪|/月|/天|/日|/年|每月|每天|每日|每年", r"\d+(?:\.\d+)?", r"[-—–~～至到]"]):
        return None
    low, high = map(float, numbers.groups())
    return (low, high, currency, period, tax) if low <= high else None


def _preferences(profile, job) -> list[MatchItem]:
    prefs = getattr(profile, "preferences", None)
    if prefs is None:
        return []
    items = []

    def add(kind, content, field_name, value, judgment, source):
        items.append(MatchItem(
            requirement_id=None, type=kind, content=content, priority="preferred", group="preferences", scope="preference",
            status=judgment.status, reason=judgment.reason,
            profile_evidence=_pref_evidence(field_name, value),
            jd_evidence=JDMatchEvidence(evidence_text=source, source_jd_version=job.jd_version, source_kind="job_field"),
            rule_version=RULE_VERSION, verification=judgment.verification,
        ))

    if prefs.locations:
        if job.location is None:
            judgment = _unknown("岗位城市尚未填写，无法与城市偏好比较。", "请确认岗位实际工作城市。")
        elif any(_plain(location) == _plain(job.location) for location in prefs.locations):
            judgment = _Judgment("met", "岗位城市与用户明确填写的城市偏好精确一致。")
        else:
            judgment = _Judgment("partial", "岗位城市与已填偏好未精确一致；这属于偏好差异，不代表资格不符。", verification=["请确认可接受城市或远程安排；未做行政区/都市圈推断。"])
        add("location_preference", "工作城市偏好", "locations", "、".join(prefs.locations), judgment, job.location)
    if prefs.work_types:
        if job.work_type == "unknown":
            judgment = _unknown("岗位工作性质未知，无法与个人偏好比较。", "请确认岗位是实习、全职还是校招。")
        elif job.work_type in prefs.work_types:
            judgment = _Judgment("met", "岗位工作性质在用户明确选择的求职偏好中。")
        else:
            judgment = _Judgment("partial", "岗位工作性质与个人偏好不同；不作为硬性资格冲突。", verification=["请确认是否愿意考虑此类工作。"])
        add("work_type_preference", "工作性质偏好", "work_types", "、".join(prefs.work_types), judgment, job.work_type)
    if prefs.target_roles:
        if job.title in prefs.target_roles:
            judgment = _Judgment("met", "岗位名称与已确认的目标岗位名称精确一致。")
        else:
            judgment = _unknown("岗位方向只支持精确名称，不能仅凭名称推断职能相关性。", "请阅读岗位职责，核实是否符合你的目标方向。")
        add("role_preference", "岗位方向偏好", "target_roles", "、".join(prefs.target_roles), judgment, job.title)
    if prefs.salary:
        value = prefs.salary
        parsed = _salary(job.salary)
        if not parsed or value.tax == "unknown" or (parsed[2], parsed[3], parsed[4]) != (value.currency, value.period, value.tax):
            judgment = _unknown("薪资币种、周期或税前税后口径不完整/不一致，不能可靠换算。", "请明确双方薪资币种、日/月/年周期及税前税后口径。")
        elif (value.min is not None and parsed[1] < value.min) or (value.max is not None and parsed[0] > value.max):
            judgment = _Judgment("partial", "同币种、同周期、同税前税后口径的薪资区间与个人期望无重叠；属于偏好差异。", verification=["请核实薪资弹性、具体待遇及个人可接受范围。"])
        else:
            judgment = _Judgment("met", "口径明确且一致的岗位薪资区间与用户期望范围存在重叠；不保证最终待遇。")
        add("salary_preference", "薪资偏好", "salary", value.model_dump_json(), judgment, job.salary)
    # Availability is a confirmed user preference, never inferred from old work
    # durations. Only current reviewed, unambiguous duration requirements count.
    for field_name, marker, unit, label in (("weekly_days", r"每周(?:至少|不少于|最少)?\s*(\d+)\s*天", "天/周", "每周可工作时间"), ("internship_months", r"(?:实习)?(?:时长)?(?:至少|不少于|最少)?\s*(\d+)\s*(?:个)?月", "个月", "可实习时长")):
        value = getattr(prefs, field_name)
        if value is None:
            continue
        reqs = [r for r in job.requirements if r.type == "internship_duration" and r.review_status == "confirmed" and not r.stale and r.source_jd_version == job.jd_version and r.priority != "uncertain"]
        compared = False
        for req in reqs:
            match = re.search(marker, req.content)
            if not match or not _fully_consumed(req.content, [marker, r"必须|须|要求|可|到岗|工作|实习|时长|至少|不少于|最少|每周|持续|的"]):
                continue
            minimum = int(match[1])
            judgment = _Judgment("met" if value >= minimum else "partial", f"用户明确填写可提供{value}{unit}，岗位已审核条件要求{minimum}{unit}；仅作为可用时间偏好比较。", verification=[] if value >= minimum else ["请核实是否能调整时间安排或与招聘方协商。"])
            add(field_name + "_preference", label, field_name, value, judgment, req.evidence_text or req.content)
            compared = True
        if not compared:
            add(field_name + "_preference", label, field_name, value, _unknown("未找到可可靠比较的已审核工作时间要求。", "请核实每周工作天数和可接受的实习时长。"), None)
    return items


def evaluate_match(profile: CareerProfileResponse, job: JobDetailResponse) -> MatchEvaluation:
    """Evaluate only server-loaded formal facts; callers own auth/CAS/storage."""
    items = []
    excluded = []
    for req in job.requirements:
        if req.stale or req.source_jd_version != job.jd_version:
            excluded.append(ExcludedRequirement(requirement_id=req.id, content=req.content, reason="stale_jd"))
            continue
        if req.review_status != "confirmed":
            excluded.append(ExcludedRequirement(requirement_id=req.id, content=req.content, reason="pending_review"))
            continue
        result = _judge(profile, req)
        items.append(MatchItem(
            requirement_id=req.id, type=req.type, content=req.content, priority=req.priority,
            group=_group(req.type), status=result.status, reason=result.reason,
            profile_evidence=result.evidence, jd_evidence=_jd_evidence(req),
            rule_version=RULE_VERSION, verification=result.verification,
        ))
    items.extend(_preferences(profile, job))
    conflicts = [i for i in items if i.scope == "qualification" and i.priority == "must" and i.status == "unmet"]
    key_unknowns = [i for i in items if i.scope == "qualification" and (i.priority == "must" and i.status in {"unknown", "partial"} or i.priority == "uncertain")]
    if conflicts:
        conclusion = "hard_conflict"
        reasons = [f"{i.content}：{i.reason}" for i in conflicts]
    elif key_unknowns:
        conclusion = "verify_first"
        reasons = [f"{i.content}：{i.reason}" for i in key_unknowns]
    else:
        conclusion = "consider"
        reasons = ["当前已审核条件中未发现明确的硬性冲突，可以结合具体职责和个人意愿进一步考虑。"]
        if not any(i.scope == "qualification" for i in items):
            conclusion = "verify_first"
            reasons = ["暂无可用于正式匹配的当前已审核条件，请先审核 JD。"]
    next_steps = list(dict.fromkeys(v for i in items for v in i.verification))
    if excluded:
        next_steps.append(f"还有{len(excluded)}条未审核或旧版条件未参与匹配，请返回审核 JD。")
    next_steps += ["需要补充事实时返回“我的资料”，保存后重新计算。", "匹配结果仅用于辅助决策，不是录用概率，也不会替你拒绝或投递岗位。"]
    return MatchEvaluation(rule_version=RULE_VERSION, conclusion=conclusion, reasons=reasons, items=items, excluded_requirements=excluded, next_steps=next_steps)
