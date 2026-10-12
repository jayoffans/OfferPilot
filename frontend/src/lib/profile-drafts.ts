import { SESSION_RESET_EVENT, type ProfileSave } from "@/lib/api";

type Draft = Pick<ProfileSave, "education" | "skills" | "projects" | "experiences">;
type Snapshot = { revision: number; draft: Draft };
// Keep only business fields in this tab's memory. Sources, candidates and
// credentials are never cached, and every visit still reads the real API.
const drafts = new Map<string, Snapshot>();
const copy = (draft: Draft): Draft => JSON.parse(JSON.stringify(draft)) as Draft;

export function rememberProfileDraft(userId: string, revision: number, draft: Draft) {
  if (typeof window !== "undefined") drafts.set(userId, { revision, draft: copy(draft) });
}
export function readProfileDraft(userId: string): Snapshot | null {
  const snapshot = typeof window !== "undefined" ? drafts.get(userId) : null;
  return snapshot ? { revision: snapshot.revision, draft: copy(snapshot.draft) } : null;
}
export function forgetProfileDraft(userId: string) { drafts.delete(userId); }
export function clearProfileDrafts() { drafts.clear(); }

if (typeof window !== "undefined") window.addEventListener(SESSION_RESET_EVENT, clearProfileDrafts);
