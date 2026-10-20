import { NextRequest } from 'next/server';
import { proxyApi } from '@/lib/proxy';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
type Context = { params: Promise<{ path: string[] }> };
async function handler(request: NextRequest, context: Context) { return proxyApi(request, (await context.params).path); }
export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE };
