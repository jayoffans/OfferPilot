"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, RefreshCw, ShieldCheck } from "lucide-react";
import { api, ApiError, formatError, type AccountDeletion } from "@/lib/api";
import { Brand, formatDate, Notice, Spinner } from "./ui";

const labels: Record<AccountDeletion["status"], string> = { pending: "注销已受理", processing: "正在清理账户数据", failed: "数据清理尚未完成", manual_review: "数据清理需要人工处理", completed: "账户注销已完成" };

export function AccountDeletionStatus() {
  const [record, setRecord] = useState<AccountDeletion | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function query() {
      if (controller.signal.aborted || ++attempts > 20) return;
      try {
        const result = await api.deletionStatus(controller.signal);
        if (controller.signal.aborted) return;
        setRecord(result); setError(null); setExpired(false);
        if (["pending", "processing"].includes(result.status)) timer = setTimeout(() => { void query(); }, 1500);
      } catch (cause) {
        if (!controller.signal.aborted) { setError(formatError(cause)); setExpired(cause instanceof ApiError && [401, 404, 410].includes(cause.status)); }
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void query();
    return () => { clearTimeout(timer); controller.abort(); };
  }, [reload]);
  const complete = record?.status === "completed";
  return <main className="account-deletion-page"><div className="account-deletion-brand"><Brand linked={false} /><span>账户数据管理</span></div><section className="panel account-deletion-result"><span className={`account-result-icon${complete ? " account-result-complete" : ""}`}>{complete ? <Check size={27} /> : <ShieldCheck size={27} />}</span><p className="eyebrow">你的数据，妥善处理</p><h1>{record ? labels[record.status] : loading ? "正在查询注销状态" : "暂时无法查询注销状态"}</h1>{loading && <Spinner label="正在读取安全处理状态" />}{record && <><p className="account-deletion-intro">{complete ? "账户、业务记录与私有文件的主存储清理已完成。该账户已无法登录。" : "账户的普通业务访问已关闭。尚未完成的清理不会被标记为注销完成。"}</p><dl className="account-deletion-times"><div><dt>受理时间</dt><dd>{formatDate(record.created_at, true)}（北京时间）</dd></div>{record.completed_at && <div><dt>完成时间</dt><dd>{formatDate(record.completed_at, true)}（北京时间）</dd></div>}</dl>{["failed", "manual_review"].includes(record.status) && <Notice>部分清理未完成，需要维护者恢复处理。请保留处理编号 {record.id}，联系维护者核查；此编号本身不能用于查询或接管账户。</Notice>}{["pending", "processing"].includes(record.status) && <Notice tone="info">正在处理数据库与文件清理。自动查询有次数上限，你可以稍后手动刷新。</Notice>}<div className="account-retention"><strong>备份与第三方数据</strong><p>备份副本不会在此刻全部消失。当前环境尚未启用自动到期清理，具体保留期限需由维护者公布；恢复后开放访问前必须重放删除清单。最小删除及故障记录用于阻止数据恢复和处理清理失败。</p><p>已发送的外部模型请求可能无法撤回；第三方数据保留由供应商政策决定，不在本次主存储清理完成的承诺范围内。</p></div></>}{error && <Notice>{expired ? "安全查询凭据不存在或已过期，无法在此浏览器继续查询。不能据此判断注销是否完成，请联系维护者核查。" : error}</Notice>}<div className="account-deletion-actions"><button className="button button-secondary" disabled={loading} onClick={() => { setLoading(true); setReload(value => value + 1); }}><RefreshCw size={16} />刷新处理状态</button>{complete && <Link className="button button-primary" href="/login">返回登录页</Link>}</div><p className="account-status-privacy">此页只显示必要处理状态。安全查询权限短期有效，不保存账户密码，也不允许恢复账户或执行业务操作。</p></section></main>;
}
