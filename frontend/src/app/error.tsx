"use client";

import { AlertCircle, RotateCcw } from "lucide-react";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return <main className="standalone-state"><AlertCircle size={36} /><h1>页面暂时无法显示</h1><p>请重新加载页面。如果问题持续，请联系维护者。</p><button className="button button-primary" onClick={reset}><RotateCcw size={16} />重新加载</button></main>;
}
