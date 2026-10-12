import type { Metadata } from "next";
import { ProfilePage } from "@/components/profile-page";

export const metadata: Metadata = { title: "我的资料" };
export default function Page() { return <ProfilePage />; }
