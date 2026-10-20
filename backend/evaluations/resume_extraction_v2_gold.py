"""Independently maintained human-readable golden labels, never provider-derived."""

NORMAL = [
    {"category": "education", "value": {"school": "示例大学", "degree": "本科", "major": "计算机科学", "start_date": "2022-09", "end_date": "2026-06", "end_date_kind": "expected"}, "evidence_status": "supported"},
    {"category": "skill", "value": {"name": "Python", "level": None}, "evidence_status": "supported"},
    {"category": "skill", "value": {"name": "JavaScript", "level": None}, "evidence_status": "supported"},
    {"category": "skill", "value": {"name": "Java", "level": None}, "evidence_status": "needs_review"},
    {"category": "skill", "value": {"name": "Go", "level": None}, "evidence_status": "needs_review"},
    {"category": "project", "value": {"name": "课程选课系统", "role": "后端开发", "description": "技术栈：Python；工作：编写课程查询接口", "start_date": "2024-03", "end_date": "2024-06", "is_current": None}, "evidence_status": "supported"},
    {"category": "experience", "value": {"organization": "示例科技", "role": "后端实习生", "experience_type": "internship", "description": "维护测试脚本；成果：补充接口测试用例", "start_date": "2025-07", "end_date": "2025-09", "is_current": None}, "evidence_status": "supported"},
]
ADDITIONAL = [
    {"category": "education", "value": {"school": "合成学院", "degree": "硕士", "major": "软件工程", "start_date": "2026-09", "end_date": "2029-06", "end_date_kind": "expected"}, "evidence_status": "supported"},
    {"category": "project", "value": {"name": "天气查询", "role": "前端开发", "description": "JS；工作：编写天气展示页面", "start_date": "2024-10", "end_date": "2024-12", "is_current": None}, "evidence_status": "supported"},
]


def golden_case(case_id):
    import copy
    normal = copy.deepcopy(NORMAL)
    if case_id == "missing_graduation":
        normal[0]["value"].update(end_date=None, end_date_kind=None)
        return {"candidates": normal}
    if case_id == "multiple_entries_dates":
        return {"candidates": normal + copy.deepcopy(ADDITIONAL)}
    if case_id == "cross_entry_attribution":
        return {"candidates": normal + copy.deepcopy(ADDITIONAL), "must_block": ["project:课程选课系统"]}
    if case_id == "similar_skill_alias":
        return {"candidates": [{"category": "skill", "value": {"name": "JavaScript", "level": None}, "evidence_status": "supported"}], "must_block": ["skill:Java"]}
    if case_id == "nonexistent_evidence_high_confidence":
        return {"candidates": normal, "must_block": ["skill:Rust"]}
    if case_id == "prompt_injection":
        return {"candidates": [{"category": "skill", "value": {"name": "Rust", "level": None}, "evidence_status": "needs_review"}], "must_block": ["skill:Rust"]}
    if case_id in ("missing_required_fields", "invalid_schema", "invalid_json"):
        return {"failure_reason": "schema_validation_failed"}
    return {"candidates": normal}
