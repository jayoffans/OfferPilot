"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatError, type Resume } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function useResumeList() {
  const { status, user } = useAuth();
  const ownerId = user?.id || "";
  const [data, setData] = useState<{ owner: string; resumes: Resume[] }>({ owner: "", resumes: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    controller.current?.abort();
    const active = new AbortController(); controller.current = active;
    setLoading(true); setError(null);
    try { const data = await api.resumes(active.signal); if (!active.signal.aborted) setData({ owner: ownerId, resumes: data }); }
    catch (cause) { if (!active.signal.aborted) setError(formatError(cause)); }
    finally { if (!active.signal.aborted) setLoading(false); }
  }, [ownerId]);

  useEffect(() => {
    const timer = setTimeout(() => { if (status === "authenticated") void refresh(); }, 0);
    return () => { clearTimeout(timer); controller.current?.abort(); };
  }, [status, user?.id, refresh]);

  return { resumes: status === "authenticated" && data.owner === user?.id ? data.resumes : [], loading, error, refresh };
}
