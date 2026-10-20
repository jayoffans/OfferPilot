import type { Metadata } from "next";
import { Suspense } from "react";
import { ApplicationsPage } from "@/components/applications-page";
import { Spinner } from "@/components/ui";
export const metadata: Metadata = { title: "投递进度" };
export default function Page() { return <Suspense fallback={<main className="standalone-state"><Spinner label="正在读取申请筛选" /></main>}><ApplicationsPage /></Suspense>; }
