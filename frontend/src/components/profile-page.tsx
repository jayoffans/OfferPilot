"use client";

import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { BriefcaseBusiness, Check, Code2, FolderGit2, GraduationCap, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { api, ApiError, formatError, type CareerProfile, type ProfileCandidate, type ProfileReview } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { forgetProfileDraft, readProfileDraft, rememberProfileDraft } from "@/lib/profile-drafts";
import { WorkspaceShell } from "@/components/workspace-shell";
import { formatDate, Notice, PageHeading, Spinner } from "@/components/ui";
import { emptyEntry, entrySummary, FieldEditor, fields, groupNames, profileDraft, SourceDetails, validateDraft, type ProfileDraft, type ProfileGroup } from "@/components/profile-editor";
import { ProfileCandidates } from "@/components/profile-candidates";
import { AiResumePanel } from "@/components/ai-resume-panel";

const groups: ProfileGroup[] = ["education", "skills", "projects", "experiences"];
const limits: Record<ProfileGroup, number> = { education: 20, skills: 100, projects: 30, experiences: 40 };
const addLabels: Record<ProfileGroup, string> = { education: "添加教育经历", skills: "添加技能", projects: "添加项目经历", experiences: "添加实习 / 工作经历" };
const groupIcons = { education: GraduationCap, skills: Code2, projects: FolderGit2, experiences: BriefcaseBusiness };
const hints: Record<ProfileGroup, string> = { education: "学校、学历、专业和毕业时间可以分次补充。", skills: "填写实际接触或掌握的技能，未填写掌握情况时保持未知。", projects: "记录真实参与的项目、你的角色和实际完成的内容。", experiences: "实习和工作可以分别记录，不需要一次填完全部字段。" };

function missingLabel(value: string): string {
  const normalized = value.replace(/\[\d+\]/g, "");
  const [group, key] = normalized.split(".");
  if (groups.includes(group as ProfileGroup)) {
    const title = groupNames[group as ProfileGroup];
    const field = fields[group as ProfileGroup].find(item => item.key === key);
    return field ? `${title}的${field.label}` : title;
  }
  return "仍有资料可补充";
}

export function ProfilePage() {
  const { status, user } = useAuth();
  const userId = user?.id;
  const [profile, setProfile] = useState<CareerProfile | null>(null);
  const [draft, setDraft] = useState<ProfileDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [latest, setLatest] = useState<CareerProfile | null>(null);
  const [latestLoading, setLatestLoading] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [candidateDirty, setCandidateDirty] = useState(false);
  const [profileUserId, setProfileUserId] = useState<string | null>(null);
  const [restored, setRestored] = useState(false);
  const [draftBaseRevision, setDraftBaseRevision] = useState<number | null>(null);
  const [reloadCount, setReloadCount] = useState(0);
  const busy = useRef(false);
  const actionController = useRef<AbortController | null>(null);
  const baseline = profile ? profileDraft(profile) : null;
  const dirty = !!draft && !!baseline && JSON.stringify(draft) !== JSON.stringify(baseline);
  const disabled = saving || !!reviewing || conflict;

  useEffect(() => () => actionController.current?.abort(), []);
  useEffect(() => {
    if (status !== "authenticated" || !userId) return;
    const controller = new AbortController();
    async function load() {
      try {
        const data = await api.profile(controller.signal);
        if (controller.signal.aborted) return;
        const cached = readProfileDraft(userId!);
        const changed = !!cached && cached.revision !== data.revision;
        setProfile(data); setProfileUserId(userId!); setDraft(cached?.draft || profileDraft(data)); setDraftBaseRevision(cached?.revision ?? data.revision); setRestored(!!cached); setLoadError(null);
        setError(changed ? "服务器资料已更新，已恢复本次会话的编辑以供核对。请查看最新内容后重新载入，当前禁止覆盖保存。" : null);
        setConflict(changed); setLatest(changed ? data : null);
      } catch (cause) { if (!controller.signal.aborted) setLoadError(formatError(cause)); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [reloadCount, status, userId]);

  useEffect(() => {
    if (!userId || status !== "authenticated" || userId !== profileUserId || draftBaseRevision === null || !draft) return;
    if (dirty) rememberProfileDraft(userId, draftBaseRevision, draft);
    else forgetProfileDraft(userId);
  }, [userId, status, profileUserId, draftBaseRevision, draft, dirty]);

  function apply(data: CareerProfile) { if (userId) forgetProfileDraft(userId); setProfile(data); setDraft(profileDraft(data)); setDraftBaseRevision(data.revision); setConflict(false); setLatest(null); setRestored(false); }
  function reload() { if (busy.current) return; if (dirty) setResetOpen(true); else { setLoading(true); setReloadCount(current => current + 1); } }
  function discard() { if (userId) forgetProfileDraft(userId); if (profile) { setDraft(profileDraft(profile)); setDraftBaseRevision(profile.revision); } setRestored(false); }
  function discardAndReload() { discard(); setResetOpen(false); setLoading(true); setSuccess(null); setReloadCount(current => current + 1); }
  function change(group: ProfileGroup, index: number, key: string, value: string | boolean | null) {
    setDraft(current => current ? { ...current, [group]: current[group].map((entry, row) => row === index ? { ...entry, [key]: value } : entry) } : current);
    setSuccess(null); setError(null);
  }
  function add(group: ProfileGroup) {
    setDraft(current => current ? { ...current, [group]: [...current[group], emptyEntry(group)] } : current);
    setSuccess(null); setError(null);
  }
  function remove(group: ProfileGroup, index: number) {
    setDraft(current => current ? { ...current, [group]: current[group].filter((_entry, row) => row !== index) } : current);
    setSuccess(null); setError(null);
  }
  function handleFailure(cause: unknown) {
    if (cause instanceof ApiError && cause.status === 409) {
      setConflict(true);
      setError("资料已在其他页面更新，或来源已发生变化。本次编辑已保留，尚未覆盖服务器资料。请查看最新内容后重新载入。");
    } else setError(formatError(cause));
  }
  async function save() {
    if (!profile || !draft || busy.current || conflict) return;
    const issue = validateDraft(draft);
    if (issue) { setError(issue); setSuccess(null); return; }
    busy.current = true; setSaving(true); setError(null); setSuccess(null);
    const controller = new AbortController(); actionController.current = controller;
    try {
      const data = await api.saveProfile({ revision: profile.revision, ...draft }, controller.signal);
      if (!controller.signal.aborted) { apply(data); setSuccess("资料已保存。你可以稍后继续补充，刷新或重新登录后仍会保留。"); }
    } catch (cause) { if (!controller.signal.aborted) handleFailure(cause); }
    finally { busy.current = false; if (!controller.signal.aborted) setSaving(false); }
  }
  async function review(candidate: ProfileCandidate, action: ProfileReview["action"], value?: ProfileReview["value"]): Promise<boolean> {
    if (!profile || dirty || busy.current || conflict) return false;
    busy.current = true; setReviewing(candidate.id); setError(null); setSuccess(null);
    const controller = new AbortController(); actionController.current = controller;
    try {
      const data = await api.reviewCandidate(candidate.id, { revision: profile.revision, action, ...(value ? { value } : {}) }, controller.signal);
      if (controller.signal.aborted) return false;
      apply(data); setSuccess(action === "reject" ? "候选已拒绝，未写入正式资料。" : action === "modify" ? "修改后的事实已确认，来源标记为用户修订。" : "事实已确认并加入正式资料。");
      return true;
    } catch (cause) { if (!controller.signal.aborted) handleFailure(cause); return false; }
    finally { busy.current = false; if (!controller.signal.aborted) setReviewing(null); }
  }
  async function readLatest() {
    if (busy.current) return;
    busy.current = true; setLatestLoading(true);
    const controller = new AbortController(); actionController.current = controller;
    try { const data = await api.profile(controller.signal); if (!controller.signal.aborted) setLatest(data); }
    catch (cause) { if (!controller.signal.aborted) setError(`最新资料暂时无法读取。${formatError(cause)} 本次编辑仍保留。`); }
    finally { busy.current = false; if (!controller.signal.aborted) setLatestLoading(false); }
  }

  return <WorkspaceShell hasUnsavedChanges={profileUserId === user?.id && (dirty || candidateDirty)} onDiscard={discard}><PageHeading eyebrow="真实经历，逐项确认" title="我的资料" description="建立属于你的求职画像。可以先保存已有内容，空白信息保持未知。" action={<button className="button button-secondary" disabled={loading || saving || !!reviewing || latestLoading} onClick={reload}><RefreshCw size={16} />重新载入</button>} />{loading || (profile && profileUserId !== user?.id && !loadError) ? <div className="loading-card"><Spinner label="正在读取你的求职资料" /></div> : loadError || !profile || !draft ? <section className="panel profile-load-error"><h2>资料暂时无法读取</h2><Notice>{loadError || "请重新连接后再试。"}</Notice><button className="button button-secondary" onClick={reload}>重试</button></section> : <><AiResumePanel onCandidates={reload} disabled={dirty || candidateDirty || disabled} />{restored && <Notice tone="info">恢复了本次会话尚未保存的编辑。请检查后保存；关闭此浏览器页面或退出登录会清除这份临时编辑。</Notice>}{success && <Notice tone="success">{success}</Notice>}{error && <Notice>{error}</Notice>}{conflict && <section className="profile-conflict"><div className="profile-conflict-actions"><button className="button button-secondary" disabled={latestLoading} onClick={() => { void readLatest(); }}>{latestLoading ? <Spinner label="正在读取" /> : "查看最新保存内容"}</button><button className="text-button" onClick={() => setResetOpen(true)}>放弃本次修改，重新载入</button></div>{latest && <details open className="profile-latest"><summary>服务器最新资料（只读）</summary>{groups.map(group => <div key={group}><strong>{groupNames[group]}</strong>{latest[group].length ? latest[group].map(entry => <p key={entry.id}>{entrySummary(group, entry)}</p>) : <p>尚未填写</p>}</div>)}</details>}</section>}<section className="panel profile-overview" aria-labelledby="profile-status-heading"><div><h2 id="profile-status-heading">{profile.updated_at ? "资料已保存" : "从一项真实经历开始"}</h2><p>{profile.updated_at ? `最近保存：${formatDate(profile.updated_at, true)}` : "暂无已保存的经历，可以从教育或技能开始。"}</p></div><div className="profile-group-status">{groups.map(group => <span key={group}><span className={`status-dot${profile[group].length ? "" : " profile-dot-empty"}`} />{groupNames[group]}<strong>{profile[group].length ? `${profile[group].length} 项` : "未填写"}</strong></span>)}</div>{!!profile.completion.missing_fields.length && <p className="profile-completion-note">可以继续补充：{Array.from(new Set(profile.completion.missing_fields.map(missingLabel))).join("、")}。资料不完整也可以保存。</p>}</section><div className="profile-layout"><div className="profile-form-area"><form onSubmit={event => { event.preventDefault(); void save(); }}><fieldset className="profile-form-fieldset" disabled={saving || !!reviewing}>{groups.map(group => {
    const Icon = groupIcons[group];
    return <section key={group} className="panel profile-group" aria-label={groupNames[group]}><div className="section-heading"><div className="profile-group-title"><span className="profile-section-icon"><Icon size={21} /></span><div><h2>{groupNames[group]}</h2><p>{hints[group]}</p></div></div><span className="badge badge-neutral">{draft[group].length} 项</span></div>{draft[group].length ? <div className="profile-entry-list">{draft[group].map((entry, index) => {
      const original = profile[group].find(saved => saved.id === entry.id);
      return <article key={entry.id || `${group}-${index}`} className="profile-entry" aria-label={`${groupNames[group]}第 ${index + 1} 项`}><div className="profile-entry-heading"><strong>{groupNames[group]} {index + 1}</strong><div><span className={`badge ${entry.id ? "badge-success" : "badge-neutral"}`}>{entry.id ? "已确认资料" : "用户填写 · 待保存"}</span><button type="button" className="icon-button" aria-label={`移除${groupNames[group]}第 ${index + 1} 项`} disabled={disabled} onClick={() => remove(group, index)}><Trash2 size={16} /></button></div></div><FieldEditor group={group} entry={entry} original={original} source={original?.source} prefix={`${group}-${entry.id || index}`} disabled={saving || !!reviewing} onChange={(key, value) => change(group, index, key, value)} /><SourceDetails group={group} source={original?.source} entry={entry} original={original} /></article>;
    })}</div> : <p className="profile-group-empty">还没有{groupNames[group]}，可按本人实际情况添加。</p>}<div className="profile-add"><button type="button" className="text-button" disabled={disabled || draft[group].length >= limits[group]} onClick={() => add(group)}><Plus size={16} />{addLabels[group]}</button>{draft[group].length >= limits[group] && <span>已达到当前数量上限</span>}</div></section>;
  })}</fieldset><div className="profile-savebar"><div><strong>{dirty ? "有修改尚未保存" : "当前资料已与服务器同步"}</strong><p>保存已有内容即可，不要求一次填写全部信息。</p></div><button className="button button-primary" type="submit" disabled={!dirty || disabled}>{saving ? <Spinner label="正在保存" /> : <><Save size={16} />保存资料</>}</button></div></form></div><aside className="profile-guidance"><section className="panel profile-guidance-card"><span className="profile-section-icon"><Check size={21} /></span><h3>你的资料，由你确认</h3><p>手动填写的内容保存后标记为用户填写。简历候选只有经过审核，才会进入正式资料。</p><p>未知信息可以留空。请仅填写真实参与的经历、职责与成果。</p></section><section className="panel profile-guidance-card"><h3>关于来源</h3><p>每项资料可以展开查看来源和原文证据。修改后的内容会标记为用户修订。</p><p>删除简历会清除候选和对应证据；已确认资料继续保留，并说明来源简历已删除。</p></section></aside></div>{dirty && !!profile.completion.pending_count && <Notice tone="info">先保存当前资料修改，再审核候选，以免丢失本次编辑。</Notice>}<ProfileCandidates candidates={profile.candidates} disabled={dirty || disabled} reviewing={reviewing} error={error} onReview={review} onDraftChange={setCandidateDirty} /></>}<Dialog.Root open={resetOpen} onOpenChange={setResetOpen}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content role="alertdialog" className="dialog-content compact-dialog"><Dialog.Title>重新载入最新资料？</Dialog.Title><Dialog.Description className="delete-description">本次尚未保存的编辑将被放弃，页面会重新读取服务器资料。需要保留的内容请先复制。</Dialog.Description><div className="dialog-actions"><Dialog.Close className="button button-primary">保留本次编辑</Dialog.Close><button className="button button-secondary" onClick={discardAndReload}>放弃修改并重新载入</button></div></Dialog.Content></Dialog.Portal></Dialog.Root></WorkspaceShell>;
}
