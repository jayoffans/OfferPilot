import type { Metadata } from "next";
import { JobPage } from "@/components/job-page";

export const metadata: Metadata = { title: "添加职位" };
export default function Page() { return <JobPage />; }
