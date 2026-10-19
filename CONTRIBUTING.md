# 参与开发

项目当前用于本地作品展示和工程讨论。先读 [README](README.md)、[当前计划](docs/PLAN.md) 和 [AGENTS.md](AGENTS.md)，再按 [LOCAL_SETUP](docs/LOCAL_SETUP.md) 建立独立环境。不要连接自己的真实求职库做测试。

提交尽量围绕一个问题，说明用户可观察的前后行为、修改原因、验证命令与结果。数据库变化提供向前迁移；API 修改从真实 OpenAPI 重新生成客户端类型。业务规则放在领域服务，保持当前模块化单体边界。

按 [TESTING](docs/TESTING.md) 执行相称检查。授权、证据、幂等、删除/导出与恢复变化需验证错误路径及两用户边界；不要写只复述实现的测试。所有自动化只用合成资料和免费模型夹具，不提供真实 Key。

请勿提交真实简历、联系方式、认证信息、数据库、ZIP、日志或备份。提交前阅读[安全与历史审查](docs/SECURITY_REVIEW.md)。安全问题按 [SECURITY](SECURITY.md) 处理，公开 Issue 不粘贴秘密或个人信息。

README/面试材料中的实现和贡献要能由代码、提交或验收支持，不写用户量、部署或模型准确率的推测。原创内容已按 [AGPL-3.0-only](LICENSE) 授权，范围及第三方例外见 [COPYRIGHT](COPYRIGHT.md)；提交贡献表示你有权提供相应内容，并同意原创贡献采用相同许可。保留第三方许可和来源，不要求版权转让。Git 作者或 AI 辅助记录不能代替授权依据。发布状态见[发布准备报告](docs/GITHUB-RELEASE-READY.md)。
