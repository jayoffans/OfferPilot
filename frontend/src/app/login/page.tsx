import type { Metadata } from "next";
import { AuthScreen } from "@/components/auth-screen";
export const metadata: Metadata = { title: "登录" };
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  const message = query.registered === "1" ? "账户已创建。请使用刚才的邮箱和密码登录。" : query.reason === "expired" ? "登录会话已失效，请重新登录后继续。" : query.logged_out === "1" ? "已安全退出登录。" : null;
  return <AuthScreen mode="login" message={message} />;
}
