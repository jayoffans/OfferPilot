import type { Metadata } from "next";
import { ResumeDetail } from "@/components/resume-detail";

export const metadata: Metadata = { title: "简历详情" };
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ResumeDetail key={id} id={id} testMode={process.env.OFFERPILOT_MOCK_EXTRACTION_UI === "true"} />;
}
