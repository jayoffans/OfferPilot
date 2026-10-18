# 文档索引

## 当前作品集入口

| 文档 | 用途 |
|---|---|
| [README](../README.md) | 产品、截图、架构与上手总览 |
| [PLAN](PLAN.md)、[roadmap](roadmap.md) | 作品集交付方向、保留边界及取消事项 |
| [LOCAL_SETUP](LOCAL_SETUP.md)、[TESTING](TESTING.md) | 隔离启动、演示种子、测试/CI 命令 |
| [DEMO](DEMO.md) | 现场路线、11 张真实合成截图与来源 |
| [ARCHITECTURE_GUIDE](ARCHITECTURE_GUIDE.md) | 当前落地系统和代码阅读路线 |
| [INTERVIEW](INTERVIEW.md) | 技术问答、贡献边界及介绍稿 |
| [SECURITY_REVIEW](SECURITY_REVIEW.md) | 工作树和 Git 历史安全审查 |
| [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES.md) | PyMuPDF 与第三方依赖许可 |
| [PORTFOLIO-acceptance](PORTFOLIO-acceptance.md) | 本轮实际验证、Git 差异与审阅事项 |
| [GITHUB-RELEASE-CHECKLIST](GITHUB-RELEASE-CHECKLIST.md) | 最终本地提交、许可/历史审查、复验及待授权发布顺序 |
| [GITHUB-FINAL-RELEASE-REVIEW](GITHUB-FINAL-RELEASE-REVIEW.md) | 最新 AGPLv3 候选、历史隐私复核、免费测试及未放行事项 |

## 产品与设计依据

[PRD](PRD.md) · [架构决策记录](architecture.md) · [数据库设计](database.md) · [简历抽取设计](resume-extraction-design.md) · [岗位条件设计](job-structure-design.md) · [前端设计](../frontend/DESIGN.md)

以上保留各批次上下文。旧版目标态（Worker/Redis/云/Browser 等）没有自动成为当前实现；读当前能力优先看架构导读与最新阶段验收。

## S1～S5-2 验收历史

| 阶段 | 记录 |
|---|---|
| S1 | [中文工作台与既有身份/文件](S1-3-acceptance.md) |
| S2-1 | [正式画像与候选审核](S2-1-acceptance.md) |
| S2-2A | [Mock 持久抽取与证据](S2-2A-acceptance.md) |
| S2-2B | [会话 BYOK 与免费边界验收](S2-2B-acceptance.md)、[模型准备](S2-2B-readiness-review.md)、[首次预检](S2-2B-first-real-preflight-2026-10-14.md)、[入口修复](S2-2B-first-real-preflight-fix.md)、[唯一真实单样本](S2-2B-single-real-evaluation-2026-10-14.md) |
| S3-1 | [职位与审核](S3-1-acceptance.md)、[安全审查](S3-1-security-review.md) |
| S3-2 | [可信匹配](S3-2-acceptance.md) |
| S4-1 | [申请时间线](S4-1-acceptance.md) |
| S4-2 | [工作台与完整闭环](S4-2-acceptance.md) |
| S5-1 | [PostgreSQL 可靠性](S5-1-acceptance.md) |
| S5-2 | [导出/注销与恢复](S5-2-acceptance.md)、[数据依赖清单](S5-2-data-inventory.md) |

供应商相关历史见 [政策审查](S2-2B-provider-policy-review.md) 与[受限真实评测手册](S2-2B-real-evaluation.md)。手册不提供本轮付费授权，日常演示及 CI 不使用真实模型。

## 调整前存档

[原总计划](PLAN-history.md) · [原路线图](roadmap-history.md) · [原 README](README-history.md)

这些文档保存当时决策与停止点，不能覆盖 2026-10-18 的作品集计划，也不代表生产成果。
