"use client";

import { useEffect, useRef, useState } from "react";
import { Compass, Save } from "lucide-react";
import { api, type CareerProfile, type ProfilePreferences } from "@/lib/api";
import { Notice, Spinner } from "@/components/ui";

export const emptyPreferences: ProfilePreferences = { locations: [], work_types: [], target_roles: [], salary: null, weekly_days: null, internship_months: null };
type PreferenceDraft = { locations: string; work_types: NonNullable<ProfilePreferences["work_types"]>; target_roles: string; salary_min: string; salary_max: string; period: "month" | "day" | "year"; tax: "pre_tax" | "after_tax" | "unknown"; weekly_days: string; internship_months: string };
function draftOf(preferences: ProfilePreferences): PreferenceDraft {
  return { locations: (preferences.locations || []).join("、"), work_types: preferences.work_types || [], target_roles: (preferences.target_roles || []).join("、"), salary_min: preferences.salary?.min?.toString() || "", salary_max: preferences.salary?.max?.toString() || "", period: preferences.salary?.period || "month", tax: preferences.salary?.tax || "unknown", weekly_days: preferences.weekly_days?.toString() || "", internship_months: preferences.internship_months?.toString() || "" };
}
function list(value: string): string[] { return [...new Set(value.split(/[、,，\n]/).map(item => item.trim()).filter(Boolean))]; }

export function ProfilePreferencesEditor({ profile, disabled, onSaved, onFailure, onBusy, onDirty }: { profile: CareerProfile; disabled: boolean; onSaved(profile: CareerProfile): void; onFailure(cause: unknown): void; onBusy(value: boolean): void; onDirty(value: boolean): void }) {
  const baseline = draftOf(profile.preferences || emptyPreferences);
  const [draft, setDraft] = useState(baseline);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const lock = useRef(false);
  const action = useRef<AbortController | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => () => action.current?.abort(), []);
  function change<K extends keyof PreferenceDraft>(key: K, value: PreferenceDraft[K]) { setDraft(current => ({ ...current, [key]: value })); setError(null); setSuccess(false); }
  async function save() {
    if (lock.current || disabled || !dirty) return;
    const locations = list(draft.locations), target_roles = list(draft.target_roles);
    if (locations.length > 20 || target_roles.length > 20 || [...locations, ...target_roles].some(value => value.length > 100)) { setError("意向城市和岗位方向各最多 20 项，每项不超过 100 个字符。"); return; }
    const min = draft.salary_min === "" ? null : Number(draft.salary_min);
    const max = draft.salary_max === "" ? null : Number(draft.salary_max);
    if ([min, max].some(value => value !== null && (!Number.isInteger(value) || value < 0 || value > 100000000)) || (min !== null && max !== null && min > max)) { setError("请填写有效的薪资范围，下限不能高于上限。"); return; }
    const weekly_days = draft.weekly_days === "" ? null : Number(draft.weekly_days);
    const internship_months = draft.internship_months === "" ? null : Number(draft.internship_months);
    if ((weekly_days !== null && (!Number.isInteger(weekly_days) || weekly_days < 1 || weekly_days > 7)) || (internship_months !== null && (!Number.isInteger(internship_months) || internship_months < 1 || internship_months > 36))) { setError("每周可实习天数为 1～7，可实习月数为 1～36；未知时留空。"); return; }
    lock.current = true; setSaving(true); onBusy(true); setError(null); setSuccess(false);
    const controller = new AbortController(); action.current = controller;
    try {
      const data = await api.savePreferences({ revision: profile.revision, preferences: { locations, target_roles, work_types: draft.work_types, salary: min !== null || max !== null ? { min, max, currency: "CNY", period: draft.period, tax: draft.tax } : null, weekly_days, internship_months } }, controller.signal);
      if (!controller.signal.aborted) { onDirty(false); onSaved(data); setDraft(draftOf(data.preferences || emptyPreferences)); setSuccess(true); }
    } catch (cause) { if (!controller.signal.aborted) onFailure(cause); }
    finally { lock.current = false; if (!controller.signal.aborted) { setSaving(false); onBusy(false); } }
  }
  return <section className="panel profile-preferences" aria-label="求职偏好"><div className="section-heading"><div className="profile-group-title"><span className="profile-section-icon"><Compass size={21} /></span><div><h2>求职偏好</h2><p>按需补充，单独保存，无需重填已有经历。</p></div></div><span className="badge badge-neutral">个人选择</span></div><p className="job-section-intro">偏好与招聘硬性资格分别展示。未知时留空；保存后只用于本账户的匹配，旧匹配会提示依据变化。可随时清空并保存，不记录到日志。</p>
    {error && <Notice>{error}</Notice>}{success && <Notice tone="success">求职偏好已保存，已有匹配需要按最新依据重新计算。</Notice>}
    <form onSubmit={event => { event.preventDefault(); void save(); }}><fieldset className="profile-form-fieldset" disabled={disabled || saving}><div className="profile-fields">
      <div className="form-field"><label htmlFor="preferences-locations">意向城市</label><input id="preferences-locations" value={draft.locations} maxLength={2000} placeholder="例如：上海、杭州" onChange={event => change("locations", event.target.value)} /><p className="field-hint">多项用顿号或逗号分隔。</p></div>
      <div className="form-field"><label htmlFor="preferences-roles">岗位方向</label><input id="preferences-roles" value={draft.target_roles} maxLength={2000} placeholder="填写本人确认的岗位方向" onChange={event => change("target_roles", event.target.value)} /></div>
      <div className="form-field profile-field-wide"><span className="preferences-work-label" id="preferences-work-label">意向工作类型</span><div className="preferences-checks" role="group" aria-labelledby="preferences-work-label">{([['internship', '实习'], ['full_time', '全职'], ['campus', '校招']] as const).map(([value, label]) => <label key={value}><input type="checkbox" checked={draft.work_types.includes(value)} onChange={event => change("work_types", event.target.checked ? [...draft.work_types, value] : draft.work_types.filter(type => type !== value))} />{label}</label>)}</div></div>
      <div className="form-field"><label htmlFor="preferences-salary-min">期望薪资下限（人民币元）</label><input id="preferences-salary-min" type="number" min="0" max="100000000" step="1" value={draft.salary_min} onChange={event => change("salary_min", event.target.value)} /></div>
      <div className="form-field"><label htmlFor="preferences-salary-max">期望薪资上限（人民币元）</label><input id="preferences-salary-max" type="number" min="0" max="100000000" step="1" value={draft.salary_max} onChange={event => change("salary_max", event.target.value)} /></div>
      <div className="form-field"><label htmlFor="preferences-salary-period">薪资周期</label><select id="preferences-salary-period" value={draft.period} onChange={event => change("period", event.target.value as PreferenceDraft["period"])}><option value="month">月薪</option><option value="day">日薪</option><option value="year">年薪</option></select></div>
      <div className="form-field"><label htmlFor="preferences-salary-tax">薪资税费口径</label><select id="preferences-salary-tax" value={draft.tax} onChange={event => change("tax", event.target.value as PreferenceDraft["tax"])}><option value="unknown">尚未明确</option><option value="pre_tax">税前</option><option value="after_tax">税后</option></select></div>
      <div className="form-field"><label htmlFor="preferences-weekly-days">每周可实习天数</label><input id="preferences-weekly-days" type="number" min="1" max="7" step="1" value={draft.weekly_days} onChange={event => change("weekly_days", event.target.value)} /></div>
      <div className="form-field"><label htmlFor="preferences-internship-months">可实习月数</label><input id="preferences-internship-months" type="number" min="1" max="36" step="1" value={draft.internship_months} onChange={event => change("internship_months", event.target.value)} /></div>
    </div></fieldset><p className="field-hint">日薪、月薪、年薪及税前税后口径不明时，匹配会提示核实，不自动换算。</p><div className="preferences-save"><button className="button button-primary" disabled={disabled || saving || !dirty} type="submit">{saving ? <Spinner label="正在保存偏好" /> : <><Save size={16} />保存求职偏好</>}</button>{disabled && !saving && <span>先处理已有资料编辑或版本冲突，再修改偏好。</span>}</div></form>
  </section>;
}
