import { SESSION_RESET_EVENT } from "@/lib/api";
import type { JobDraft } from "@/components/job-editor";

type Snapshot = { revision: number | null; draft: JobDraft; step: number };
const drafts = new Map<string, Snapshot>();
const key = (userId: string, jobId?: string) => `${userId}:${jobId || "new"}`;
const copy = (snapshot: Snapshot): Snapshot => JSON.parse(JSON.stringify(snapshot)) as Snapshot;

// Session memory only. No browser storage, credentials or cached profile facts.
export function rememberJobDraft(userId: string, jobId: string | undefined, snapshot: Snapshot) { if (typeof window !== "undefined") drafts.set(key(userId, jobId), copy(snapshot)); }
export function readJobDraft(userId: string, jobId?: string): Snapshot | null { const snapshot = drafts.get(key(userId, jobId)); return snapshot ? copy(snapshot) : null; }
export function forgetJobDraft(userId: string, jobId?: string) { drafts.delete(key(userId, jobId)); }
export function clearJobDrafts() { drafts.clear(); }
if (typeof window !== "undefined") window.addEventListener(SESSION_RESET_EVENT, clearJobDrafts);
