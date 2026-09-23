# 安全协议

## 适用范围

本协议覆盖 TokenFlow 源码及其默认本地 HTTP 服务。不自动覆盖 FreeToken、Codex、Claude、DSH、模型权重、操作系统、显卡驱动或第三方安装器。

TokenFlow 是本地开发工具，不是多租户服务。默认信任边界是当前用户账户和回环网络接口。

## 安全敏感操作

以下接口和能力可能产生本地副作用：

1. `POST /api/execute` 可以调用本机已安装的 Harness；
2. `POST /api/freetoken/install` 可以打开本地安装器；
3. `POST /api/freetoken/start` 可以启动本地模型服务；
4. Harness 可能根据自身配置读取文件、调用工具或执行命令。
5. `POST /api/parse` 可以读取请求中明确提供的本地路径；如果调用方不应授予路径访问权，优先使用 multipart 上传。
6. `POST /api/pc/execute` 只有在显式环境开关开启后才能控制本地鼠标键盘。
7. `POST /api/jev/decision` 会把调用方的决策 payload 发送到配置的 Jev 端点。

## 必须遵守的部署规则

- 保持 TokenFlow 绑定 `127.0.0.1`；
- 每个 POST 都必须携带 `GET /api/session` 返回的进程级令牌；浏览器 POST 还必须来自匹配的本机 Origin。这是 CSRF 缓解措施，不是身份认证；
- 不要在不可信网络中使用 `--host 0.0.0.0`；
- 未增加认证、授权、请求限制和审计日志前，不要通过端口转发、公网反向代理或共享局域网暴露服务；
- 不要在任务内容中发送密钥；提示词、文档、源码和 Harness 输出都应视为敏感数据；
- 除非人类明确批准变更，否则使用 Codex 只读模式和 Claude 计划模式；
- 接受任何命令、文件修改或外部副作用前先人工复核；
- 不要将 `/api/parse`、`/api/pc/execute` 或 `/api/jev/decision` 暴露给不可信调用方；
- 将云端、Laya 和 Jev 配置视为数据外发配置，启用前审查端点和 payload；
- `/api/chat` 的 `auto` 只尝试回环地址；显式指定任何提供商都可能把任务发送到其配置的远程地址。页面确认不能代替 API 客户端自己的外发审批；
- `/api/local-chat` 仅接受回环 FreeToken 地址；远程 FreeToken 必须通过 `/api/chat` 显式选择；
- 核验安装器来源和签名；不要为绕过安全警告而关闭杀毒软件或执行策略。

## 浏览器写操作令牌

页面会获得当前服务进程随机生成的令牌，并在 POST 请求中通过 `X-TokenFlow-Token` 发送。命令行客户端应先请求 `GET /api/session`，再在每个 POST 中使用返回的令牌。它不是登录凭据，也不能防御同一用户账户下运行的其他程序；服务仍应只绑定回环接口，不能将该令牌当作局域网访问控制。

## 密钥处理

绝不提交 API Key、OAuth Token、Cookie、SSH 私钥、证书、本地环境文件、私有模型仓库凭据或个人绝对路径。

如果密钥已经泄露：

1. 立即吊销或轮换；
2. 从工作区移除并阻止再次提交；
3. 只保留调查所需的最少证据；
4. 私下报告事件。

## 漏洞报告

对于尚未修复的漏洞，请不要直接创建公开 Issue，也不要先公开 PoC 或利用细节。请使用仓库 **Security → Advisories → Report a vulnerability** 流程；如果该流程不可用，只创建一条不含利用细节的最小 Issue，请维护者提供私下报告渠道。

报告应包括：

- 受影响的 commit、tag 或版本；
- 操作系统与 Python 版本；
- 已移除密钥和个人数据的最小复现步骤；
- 影响范围与真实攻击前提；
- 如已知，给出缓解建议。

维护者目标是在 7 天内确认有效报告，在 14 天内给出严重性评估或缓解计划，并在修复可用后协调披露。这些是目标时限，不构成保证。

## 供应链边界

TokenFlow 不再分发 FreeToken 运行时 wheel、模型权重、Codex、Claude、DSH 或安装器二进制。用户必须从官方来源获取这些内容，并遵守其许可证和使用条款。

Windows 一键辅助脚本只负责定位用户提供的安装器并在用户确认下打开；它不是签名验证器，也不会静默安装软件。

PC Agent 层采用白名单并默认 dry-run。`TOKENFLOW_PC_AGENT_EXECUTE=1` 只是本地显式开关，不等同于安全审批。

English version: [SECURITY.md](SECURITY.md)。
