import type { Metadata } from "next";
import { ApplicationPage } from "@/components/application-page";
export const metadata: Metadata = { title: "申请详情" };
export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ApplicationPage key={id} id={id} />;
}
