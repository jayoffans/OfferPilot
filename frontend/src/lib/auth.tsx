'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { ApiError, api, clearSession, formatError, SESSION_EXPIRED_EVENT, type User } from './api';

type AuthStatus = 'loading' | 'authenticated' | 'anonymous' | 'error';
type AuthContextValue = { user: User | null; status: AuthStatus; error: string | null; reload(): Promise<void>; signIn(user: User): void; signOut(): Promise<void> };
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<AbortController | null>(null);
  const reload = useCallback(async () => {
    pending.current?.abort();
    const controller = new AbortController(); pending.current = controller;
    setStatus('loading'); setError(null);
    try {
      const current = await api.me(controller.signal);
      if (!controller.signal.aborted) { setUser(current); setStatus('authenticated'); }
    } catch (e) {
      if (controller.signal.aborted) return;
      setUser(null);
      if (e instanceof ApiError && e.status === 401) { clearSession(); setStatus('anonymous'); }
      else { setStatus('error'); setError(formatError(e)); }
    }
  }, []);
  const signIn = useCallback((current: User) => { pending.current?.abort(); setUser(current); setError(null); setStatus('authenticated'); }, []);
  const signOut = useCallback(async () => {
    try { await api.logout(); }
    catch (e) { if (!(e instanceof ApiError && e.status === 401)) throw e; }
    pending.current?.abort(); setUser(null); setError(null); setStatus('anonymous');
  }, []);
  useEffect(() => {
    // Restore the server session on mount; no background session polling.
    const timer = setTimeout(() => { void reload(); }, 0);
    const expired = () => {
      pending.current?.abort(); setUser(null); setError(null); setStatus('anonymous');
      if (!['/login', '/register'].includes(window.location.pathname)) window.location.replace('/login?reason=expired');
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    return () => { clearTimeout(timer); pending.current?.abort(); window.removeEventListener(SESSION_EXPIRED_EVENT, expired); };
  }, [reload]);
  return <AuthContext.Provider value={{ user, status, error, reload, signIn, signOut }}>{children}</AuthContext.Provider>;
}
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('AuthProvider is required');
  return context;
}
