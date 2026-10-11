"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ArrowRight, ChevronDown, FileText, LayoutDashboard, LogOut, ShieldCheck } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { formatError } from "@/lib/api";
import { Brand, Notice, Spinner } from "@/components/ui";

export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const { user, status, error, reload, signOut } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [exiting, setExiting] = useState(false);
  const [exitError, setExitError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);

  useEffect(() => { if (status === "anonymous") router.replace("/login?reason=expired"); }, [status, router]);
  const nav = <><Link href="/" className={`nav-item${pathname === "/" ? " active" : ""}`} aria-current={pathname === "/" ? "page" : undefined}><LayoutDashboard size={19} />工作台</Link><Link href="/resumes" className={`nav-item${pathname.startsWith("/resumes") ? " active" : ""}`} aria-current={pathname.startsWith("/resumes") ? "page" : undefined}><FileText size={19} />我的简历</Link></>;

  if (status === "loading" || status === "anonymous") return <main className="standalone-state"><Brand linked={false} /><Spinner label="正在确认登录状态" /></main>;
  if (status === "error") return <main className="standalone-state"><Brand linked={false} /><h1>暂时无法连接工作台</h1><p>请检查网络连接，并确认本地服务正在运行。</p><Notice>{error || "连接服务失败，请稍后再试。"}</Notice><button className="button button-primary" disabled={reloading} onClick={async () => { setReloading(true); try { await reload(); } finally { setReloading(false); } }}>{reloading ? <Spinner label="正在重试" /> : "重新连接"}</button></main>;
  if (!user) return null;

  async function logout() {
    if (exiting) return;
    setExiting(true); setExitError(null);
    try { await signOut(); router.replace("/login?logged_out=1"); }
    catch (cause) { setExitError(formatError(cause)); }
    finally { setExiting(false); }
  }

  return <div className="workspace"><a href="#main-content" className="skip-link">跳到主要内容</a><aside className="sidebar"><Brand /><div className="workspace-label">个人工作空间</div><nav aria-label="主要导航">{nav}</nav><div className="sidebar-bottom"><span className="sidebar-shield"><ShieldCheck size={19} /></span><strong>由你掌握每一步</strong><p>资料属于你，重要决策<br />也始终由你来做。</p><div className="version-label"><span className="status-dot" />邀请制 · 本地工作台</div></div></aside><div className="workspace-body"><header className="topbar"><div className="desktop-breadcrumb"><span>个人工作空间</span><ArrowRight size={13} /><strong>{pathname === "/" ? "工作台" : "我的简历"}</strong></div><div className="mobile-brand"><Brand /></div><DropdownMenu.Root><DropdownMenu.Trigger className="account-trigger" aria-label="账户菜单" disabled={exiting}><span className="avatar">{user.email.charAt(0).toUpperCase()}</span><span className="account-email">{user.email}</span><ChevronDown size={14} /></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="account-menu" sideOffset={8} align="end"><DropdownMenu.Label className="account-menu-label">当前登录账户<strong>{user.email}</strong></DropdownMenu.Label><DropdownMenu.Separator className="menu-divider" /><DropdownMenu.Item className="menu-item" onSelect={() => { void logout(); }} disabled={exiting}><LogOut size={16} />{exiting ? "正在退出…" : "退出登录"}</DropdownMenu.Item></DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root></header><nav className="mobile-nav" aria-label="移动端主要导航">{nav}</nav><main id="main-content" className="main-content">{exitError && <Notice>{exitError} 退出尚未完成，请重试。</Notice>}{children}</main><footer className="workspace-footer"><span>OfferPilot</span><span>每一份准备，都算数。</span></footer></div></div>;
}
