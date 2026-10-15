import type { Metadata } from "next";
import { ApplicationsPage } from "@/components/applications-page";
export const metadata: Metadata = { title: "投递进度" };
export default function Page() { return <ApplicationsPage />; }
