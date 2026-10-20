import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Brand } from "@/components/ui";

export default function NotFound() {
  return <main className="standalone-state"><Brand /><div className="empty-symbol">404</div><h1>这一页暂时找不到</h1><p>页面可能已移动。返回工作台继续整理你的资料。</p><Link href="/" className="button button-primary"><ArrowLeft size={16} />返回工作台</Link></main>;
}
