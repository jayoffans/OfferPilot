import type { ProfileCandidate } from "@/lib/api";

const labels: Record<string, string> = { school: "学校", degree: "学历", major: "专业", start_date: "开始时间", end_date: "结束时间", graduation_date: "毕业时间", graduation_status: "毕业状态", end_date_kind: "毕业时间类型", name: "名称", level: "掌握情况", role: "角色 / 岗位", technology_stack: "技术栈", technologies: "技术栈", tech_stack: "技术栈", work_items: "真实工作内容", outcomes: "可证实成果", work_content: "真实工作内容", description: "内容", organization: "单位名称", company: "公司", experience_type: "经历类型", responsibilities: "职责", achievements: "可证实成果", is_current: "当前状态" };
const assertions: Record<string, string> = { positive: "正向描述", affirmed: "正向描述", negative: "否定描述，不能当作已掌握", negated: "否定描述，不能当作已掌握", planned: "学习计划，不能当作已掌握", uncertain: "含义不确定，需核实" };
const reasons: Record<string, string> = { overlapping_experience_scopes: "不同经历片段重叠", scope_not_found: "未找到经历归属片段", ambiguous_scope: "经历归属不明确", instruction_like_source: "原文含指令式内容", source_text_not_found: "未找到引文", value_not_supported_by_quote: "引文不能支持字段值", evidence_outside_entry_scope: "引文属于其他经历", ambiguous_quote_occurrences: "引文出现多处，归属待核实", quote_occurrence_limit: "引文出现次数过多", context_truncated: "上下文范围不足", model_assertion_negated: "模型标记否定", model_assertion_planned: "模型标记学习计划", model_assertion_uncertain: "模型标记不确定", negative_planned_or_uncertain_context: "原文可能包含否定、计划或不确定表达", unrecognized_normalization: "无法可靠规范化", conflicting_field_values: "字段值冲突", description_requires_user_edit: "工作内容需用户核实后修订", graduation_month_missing: "毕业月份缺失", conflicting_current_and_end_date: "当前状态与结束时间冲突", conflicting_date_order: "起止时间冲突" };
const extraReasons: Record<string, string> = { source_version_changed: "来源简历版本已改变", conflicting_proposals: "同一经历存在冲突候选" };
const reasonText = (value: string) => extraReasons[value] || reasons[value] || "需要核实原文";
function display(value: unknown): string {
  if (value === null || value === undefined) return "未知";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map(display).join("、") || "未提供";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function CandidateExtractionDetails({ candidate }: { candidate: ProfileCandidate }) {
  const metadata = candidate.extraction_metadata;
  if (!metadata) return null;
  return <details className="profile-source"><summary>查看抽取字段、归属与原文位置</summary><div className="profile-source-body"><p>{metadata.test_result ? "Mock 测试结果。" : "抽取候选。"}文字定位只证明引文在简历中出现，不能证明经历真实性或语义正确。</p>{metadata.scope_text && <div className="evidence-snippet"><strong>这条经历的归属片段</strong><blockquote>{metadata.scope_text}</blockquote></div>}{!!metadata.review_reasons?.length && <p className="candidate-warning">存在待核实情况：{metadata.review_reasons?.map(reasonText).join("、")}。请核对经历归属、否定表达、学习计划和冲突。</p>}{metadata.fields.map((field, index) => <div className="evidence-snippet" key={`${field.path}-${index}`}><strong>{labels[field.path.split(".").at(-1)?.replace(/\[\d+\]/g, "") || ""] || "抽取字段"}：{display(field.raw_value)}</strong>{field.normalized_value !== null && field.normalized_value !== undefined && <p>规范化候选：{display(field.normalized_value)}</p>}<p>{assertions[field.assertion] || "字段含义仍需用户审核"}{field.mapped_field ? ` · 对应画像字段：${labels[field.mapped_field] || field.mapped_field}` : " · 作为原始参考保留"}</p>{field.source_text && <blockquote>{field.source_text}</blockquote>}{!!field.locations?.length ? <p>服务器原文定位：{field.locations?.map(location => `${location.page === null ? "页码未知" : `第 ${location.page} 页`} / ${location.paragraph === null ? "段落未知" : `第 ${location.paragraph} 段`} / 字符 ${location.start}–${location.end}`).join("；")}</p> : <p className="candidate-warning">未找到可靠原文位置，请核实。</p>}{!!field.review_reasons?.length && <p className="candidate-warning">待核实：{field.review_reasons?.map(reasonText).join("、")}</p>}{field.reported_confidence !== null && field.reported_confidence !== undefined && <span>模型自报 confidence 仅是输出元数据，不代表真实性概率。</span>}</div>)}</div></details>;
}
