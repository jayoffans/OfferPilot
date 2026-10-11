import type { Metadata } from "next";
import { ResumesPage } from "@/components/resumes-page";

export const metadata: Metadata = { title: "我的简历" };
export default function Page() { return <ResumesPage />; }
