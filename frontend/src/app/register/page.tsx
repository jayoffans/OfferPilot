import type { Metadata } from "next";
import { AuthScreen } from "@/components/auth-screen";

export const metadata: Metadata = { title: "邀请注册" };
export default function Page() { return <AuthScreen mode="register" />; }
