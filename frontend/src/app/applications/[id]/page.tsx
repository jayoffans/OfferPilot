import type { Metadata } from "next";
import { ApplicationPage } from "@/components/application-page";
export const metadata: Metadata = { title: "申请详情" };
export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ returnTo?: string | string[] }> }) {
  const { id } = await params;
  const { returnTo } = await searchParams;
  const backHref = typeof returnTo === "string" && /^\/applications(?:\?[^#]*)?$/.test(returnTo) ? returnTo : "/applications";
  return <ApplicationPage key={id} id={id} backHref={backHref} />;
}
