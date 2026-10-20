const projectBranch = "https://github.com/jayoffans/OfferPilot/tree/codex/s5-1-postgresql";
const thirdPartyNotices = "https://github.com/jayoffans/OfferPilot/blob/codex/s5-1-postgresql/docs/THIRD_PARTY_LICENSES.md";

export function LegalNotice({ className }: { className: string }) {
  return <footer className={`legal-notice ${className}`} aria-label="开源许可与声明">
    <p>© 2026 OfferPilot 各贡献者 · 版权归各自权利人，集合署名不构成权利转让。</p>
    <p>本项目按 AGPL-3.0-only 许可，不提供任何担保；可依许可条款修改及再分发。</p>
    <nav aria-label="开源项目资料">
      <a href={projectBranch} target="_blank" rel="noreferrer noopener">项目源码</a>
      <a href="/LICENSE.txt" target="_blank" rel="noreferrer noopener">许可全文</a>
      <a href={thirdPartyNotices} target="_blank" rel="noreferrer noopener">第三方声明</a>
      <span>（新标签页打开）</span>
    </nav>
  </footer>;
}
