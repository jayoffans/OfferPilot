"""Conservative lexical parser, no tools, providers, prompts, or network calls.

Only verbatim clauses are proposed. Scope ambiguity and mixed must/preferred
wording are left uncertain. Every proposal remains pending human review.
"""

import re
from typing import Protocol

from offerpilot_api.job_catalog.schemas import JobParsePreviewResponse, JobRequirementInput

PARSER_VERSION = "jd-rules-1.0"
MAX_REQUIREMENTS = 100


class JDParser(Protocol):
    version: str

    def parse(self, jd_text: str) -> JobParsePreviewResponse: ...


def _type(text: str, section: str) -> str:
    if re.search(r"薪资|薪酬|[元万].*[月年天]|\d\s*[-~～]\s*\d+\s*[kK]", text):
        return "salary"
    if ("实习" in text and re.search(r"时长|至少|不少于|最短|\d+个月|\d+月|\d+天", text)) or re.search(r"每周.*\d.*天", text):
        return "internship_duration"
    if re.search(r"专业", text):
        return "major"
    if re.search(r"学历|本科|硕士|博士|大专|学士", text):
        return "education"
    if re.search(r"毕业|\d{4}届|应届", text):
        return "graduation"
    if re.search(r"实习.*(?:经历|经验)|(?:经历|经验).*实习", text):
        return "internship_experience"
    if re.search(r"项目.*(?:经历|经验)|(?:经历|经验).*项目", text):
        return "project"
    if re.search(r"英语|英文|语言能力|CET|四级|六级|日语|口语", text, re.I):
        return "language"
    if re.search(r"工作经验|\d+年.*经验|经验.*\d+年", text):
        return "experience"
    if re.search(r"到岗|坐班|工作地点|办公地点|出差|远程|工作城市", text):
        return "location"
    if re.search(r"工作性质|全职|校招|正式工作|实习岗位", text):
        return "work_type"
    if section == "responsibility" or re.search(r"^负责|^参与.*(?:开发|维护|设计)", text):
        return "responsibility"
    if re.search(r"Java\b|Python\b|SQL\b|JavaScript\b|TypeScript\b|C\+\+|Go\b|React\b|Vue\b|Linux\b|MySQL\b|Redis\b|算法|数据结构|编程|技术栈|开发语言|数据库", text, re.I):
        return "technical_skill"
    return "other"


class RuleJDParser:
    version = PARSER_VERSION

    def parse(self, jd_text: str) -> JobParsePreviewResponse:
        items = []
        section = "unknown"
        offset = 0
        warnings = ["使用本地确定性规则整理，不调用 AI；未识别的内容请手动补充，所有条件都需要用户审核。"]
        # splitlines(keepends) and clause offsets retain original, including CRLF.
        for line in jd_text.splitlines(keepends=True):
            visible = line.strip()
            heading = re.sub(r"^[\s\d.、)（(\-•]+", "", visible).rstrip("：:")
            if heading in {"任职要求", "岗位要求", "职位要求", "招聘要求", "基本要求", "必备条件", "硬性要求"}:
                section = "must"
                offset += len(line)
                continue
            if heading in {"加分项", "优先条件", "优先要求", "加分条件"}:
                section = "preferred"
                offset += len(line)
                continue
            if heading in {"岗位职责", "工作职责", "职位职责"}:
                section = "responsibility"
                offset += len(line)
                continue
            if heading in {"职位信息", "岗位信息", "福利待遇", "薪资待遇", "其他", "其他信息", "公司简介", "职位名称", "岗位名称"}:
                section = "unknown"
                offset += len(line)
                continue
            for match in re.finditer(r"[^；;。\n\r]+", line):
                raw = match.group()
                prefix = re.match(r"^\s*(?:(?:\d+[.、)]|[（(]\d+[）)]|[\-•])\s*)?", raw)
                left = len(prefix.group()) if prefix else 0
                quote = raw[left:].strip()
                if not quote:
                    continue
                if len(quote) > 2000:
                    warnings.append("有超过2000字符的整段原文，规则未拆解该段，请手工分成条件。")
                    continue
                start = offset + match.start() + left
                kind = _type(quote, section)
                preferred = bool(re.search(r"优先|加分|更佳|优先考虑|prefer|nice.to.have", quote, re.I))
                explicit_must = bool(re.search(r"必须|须具备|必备|不得|不少于|至少|required|must\b", quote, re.I))
                ambiguous = bool(re.search(r"相关|视情况|不限|或|之一|最好|尽量|可协商|可否|是否|需核实|待确认|核实|待定|面议|无需|不要求|非必须|非必需|不限|not.required|根据.*安排|ignore|system|执行命令|忽略.*指令|<script|javascript:", quote, re.I))
                if ambiguous or (explicit_must and (preferred or section == "preferred")):
                    priority = "uncertain"
                elif kind in {"salary", "work_type", "responsibility"}:
                    priority = "uncertain"
                elif preferred or section == "preferred":
                    priority = "preferred"
                elif explicit_must or section == "must":
                    priority = "must"
                else:
                    priority = "uncertain"
                # Unknown free text outside condition sections is not invented
                # into a requirement. Injection-shaped text may be represented
                # only as inert, uncertain evidence for user removal.
                if kind == "other" and section == "unknown" and not (preferred or explicit_must or ambiguous):
                    continue
                items.append(JobRequirementInput(type=kind, content=quote, priority=priority,
                                                 evidence_text=quote, source_start=start))
                if len(items) == MAX_REQUIREMENTS:
                    warnings.append("规则候选已达100条上限，其余原文仍完整保存；请人工检查遗漏。")
                    return JobParsePreviewResponse(parser_version=self.version, requirements=items, warnings=warnings)
            offset += len(line)
        return JobParsePreviewResponse(parser_version=self.version, requirements=items, warnings=warnings)


def parse_jd(jd_text: str) -> JobParsePreviewResponse:
    return RuleJDParser().parse(jd_text)
