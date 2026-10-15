import type { Metadata } from "next";
import { JobsPage } from "@/components/jobs-page";

export const metadata: Metadata = { title: "职位" };
export default function Page() { return <JobsPage />; }
