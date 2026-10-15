import type { Metadata } from "next";
import { JobPage } from "@/components/job-page";

export const metadata: Metadata = { title: "职位详情" };
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <JobPage key={id} id={id} />;
}
