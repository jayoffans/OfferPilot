"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, Eye, EyeOff, FileText, ShieldCheck } from "lucide-react";
import { api, ApiError, formatError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Brand, Notice, Spinner } from "@/components/ui";
import { LegalNotice } from "@/components/legal-notice";

export function AuthScreen({ mode, message = null }: { mode: "login" | "register"; message?: string | null }) {
  const register = mode === "register";
  const { status, signIn } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [invite, setInvite] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const request = useRef<AbortController | null>(null);
  const busy = useRef(false);

  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => { if (status === "authenticated") router.replace("/"); }, [status, router]);


  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current) return;
    const next: Record<string, string> = {};
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim()) || email.trim().length > 254) next.email = "请输入有效的邮箱地址。";
    const length = Array.from(password).length;
    if (length < (register ? 12 : 1) || length > 128) next.password = register ? "密码需要 12–128 个字符。" : "请输入密码，最多 128 个字符。";
    if (register && invite.trim().length !== 43) next.invite_token = "请输入受邀时收到的完整 43 位邀请码。";
    setErrors(next); setError(null);
    if (Object.keys(next).length) { document.getElementById(Object.keys(next)[0])?.focus(); return; }
    busy.current = true; setPending(true); const controller = new AbortController(); request.current = controller;
    try {
      if (register) { await api.register({ email: email.trim(), password, invite_token: invite.trim() }, controller.signal); setPassword(""); setInvite(""); router.replace("/login?registered=1"); }
      else { const result = await api.login({ email: email.trim(), password }, controller.signal); signIn(result.user); setPassword(""); router.replace("/"); }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(formatError(cause));
        if (cause instanceof ApiError && cause.status === 422) setErrors({ ...(cause.fields?.includes("email") ? { email: "请检查邮箱格式。" } : {}), ...(cause.fields?.includes("password") ? { password: "请检查密码长度。" } : {}), ...(cause.fields?.includes("invite_token") ? { invite_token: "请检查邀请码。" } : {}) });
      }
    } finally { if (!controller.signal.aborted) setPending(false); busy.current = false; }
  }

  if (status === "authenticated") return <main className="standalone-state"><Spinner label="正在进入工作台" /></main>;
  return <main className="auth-layout"><section className="auth-story"><Brand linked={false} /><div className="auth-story-body"><div className="auth-kicker"><span />为你的下一步，做好准备</div><h1>每一份准备，<br />离理想更近一步。</h1><p>从整理好一份简历开始。<br />把求职资料放在一个清晰、有序的工作空间里。</p><div className="auth-steps"><div><FileText size={19} />保存一份简历原件</div><div><Check size={19} />查看服务器实际处理状态</div><div><ShieldCheck size={19} />在自己的账户中安心管理</div></div></div><div className="auth-story-footer"><ShieldCheck size={17} /><span>资料由你管理 · 重要决策由你确认</span></div></section><section className="auth-form-panel"><div className="auth-mobile-brand"><Brand linked={false} /></div><div className="auth-form-wrap"><span className="auth-form-eyebrow">{register ? "开始你的工作空间" : "欢迎回到 OfferPilot"}</span><h2>{register ? "创建你的账户" : "登录工作台"}</h2><p className="auth-intro">{register ? "使用专属邀请码，开始整理你的求职资料。" : "继续整理简历，为下一次机会做好准备。"}</p>{message && <Notice tone="info">{message}</Notice>}{error && <Notice>{error}</Notice>}<form onSubmit={submit} noValidate><div className="form-field"><label htmlFor="email">邮箱地址</label><input id="email" type="email" autoComplete="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} disabled={pending} aria-invalid={!!errors.email} aria-describedby={errors.email ? "email-error" : undefined} maxLength={254} required />{errors.email && <p className="field-error" id="email-error">{errors.email}</p>}</div><div className="form-field"><label htmlFor="password">密码</label><div className="password-input"><input id="password" type={showPassword ? "text" : "password"} autoComplete={register ? "new-password" : "current-password"} placeholder={register ? "至少 12 个字符" : "请输入你的密码"} value={password} onChange={(event) => setPassword(event.target.value)} disabled={pending} aria-invalid={!!errors.password} aria-describedby={errors.password ? "password-error" : register ? "password-hint" : undefined} required /><button type="button" className="password-toggle" onClick={() => setShowPassword(!showPassword)} aria-label={showPassword ? "隐藏密码" : "显示密码"}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></div>{errors.password ? <p className="field-error" id="password-error">{errors.password}</p> : register && <p className="field-hint" id="password-hint">12–128 个字符，请使用独立且不易猜到的密码。</p>}</div>{register && <div className="form-field"><label htmlFor="invite_token">邀请码</label><input id="invite_token" type="password" autoComplete="off" spellCheck={false} placeholder="输入收到的专属邀请码" value={invite} onChange={(event) => setInvite(event.target.value)} disabled={pending} aria-invalid={!!errors.invite_token} aria-describedby={errors.invite_token ? "invite-error" : "invite-hint"} required />{errors.invite_token ? <p className="field-error" id="invite-error">{errors.invite_token}</p> : <p className="field-hint" id="invite-hint">邀请码仅适用于受邀邮箱，一次有效。</p>}</div>}<button className="button button-primary auth-submit" type="submit" disabled={pending || status === "loading"}>{pending ? <Spinner label={register ? "正在创建账户" : "正在登录"} /> : <>{register ? "创建账户" : "登录"}<ArrowRight size={17} /></>}</button></form><div className="auth-switch">{register ? "已经有账户？" : "收到邀请，还没有账户？"}<Link href={register ? "/login" : "/register"}>{register ? "前往登录" : "邀请注册"}<ArrowRight size={13} /></Link></div><div className="auth-note"><ShieldCheck size={16} /><p>OfferPilot 当前为邀请制本地工作台。<br />支持账户登录与简历文件管理。</p></div></div><LegalNotice className="auth-footer" /></section></main>;
}
