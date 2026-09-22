# TokenFlow

**面向 AI Agent 工作流的本地低 Token 执行框架。**

TokenFlow 在任务交给本地模型或 AI 编程 Harness 之前，先减少不必要的上下文。核心保持小而清晰：Python 标准库、确定性预处理、本地 HTTP 页面，以及可选的 FreeToken、Codex、Claude 和 DSH 适配器。

English documentation: [README.md](README.md)。

## 核心能力

- 将任务分类为代码、表格、文档或通用任务；
- 使用确定性的首尾保留策略压缩长文本；
- 展示压缩前后的 Token 估算值；估算值不是计费级 tokenizer 结果；
- 检测本机 Codex、Claude、DSH 并选择默认 Harness/模型；
- 默认使用 Codex 只读模式和 Claude 计划模式；
- 通过 OpenAI 兼容接口连接本地 FreeToken，不复制其推理运行时；
- 提供需要用户确认的 Windows 安装器入口和 FreeToken 启动入口。

## 架构

```text
浏览器或命令行
       |
       v
TokenFlow HTTP 服务 :8765
       |
       +--> 确定性预处理
       +--> FreeToken :1919（可选本地模型）
       +--> Codex / Claude / DSH（可选 Harness）
```

服务默认绑定 `127.0.0.1`。它不是认证网关，未增加认证和网络隔离前不应暴露到不可信网络。

## 快速开始

要求：Python 3.10 或更高版本。

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py
python tokenflow.py --self-check
python tokenflow.py
```

然后打开 <http://127.0.0.1:8765>。Windows 用户也可以双击 `start_tokenflow.cmd`。

## FreeToken 集成

FreeToken 是可选组件。TokenFlow 使用其本地 OpenAI 兼容接口：

```text
GET  http://127.0.0.1:1919/health
GET  http://127.0.0.1:1919/v1/models
POST http://127.0.0.1:1919/v1/chat/completions
```

Windows 用户双击 `install_freetoken.cmd`。脚本依次查找 `TOKENFLOW_FREETOKEN_INSTALLER`、项目目录旁的安装包和用户 Downloads 目录。安装器在独立窗口打开，由用户明确确认。

设置 `TOKENFLOW_FREETOKEN_MODEL` 指向模型目录，然后在 TokenFlow 页面点击“启动 FreeToken”。脚本不会静默下载或执行安装器，本仓库不捆绑模型权重。

```powershell
$env:TOKENFLOW_FREETOKEN_MODEL = "<模型目录>"
$env:TOKENFLOW_FREETOKEN_URL = "http://127.0.0.1:1919"
python tokenflow.py
```

Linux 用户请按照 FreeToken 官方文档安装并启动服务。

## Harness 执行

页面可以把压缩后的任务交给 Codex、Claude 或 DSH。代码任务优先 Codex，其它任务优先 Claude；长文本和代码任务优先 quality 模型。

```powershell
$body = @{task="分析这段代码"; content="print('hello')"; harness="auto"; model="auto"} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/api/execute `
  -Method Post -ContentType "application/json" -Body $body
```

默认 Codex 使用只读模式，Claude 使用计划模式。DSH 需要配置 `DSH_HOME` 和 profile。

## HTTP 接口

```text
GET  /health
GET  /api/harnesses
GET  /api/freetoken
GET  /api/local-models
POST /api/run
POST /api/local-chat
POST /api/execute
POST /api/freetoken/install
POST /api/freetoken/start
```

安装和启动接口可以控制本地进程，不要将它们转发到公网反向代理。

## 安全与隐私

部署前请阅读 [SECURITY.zh-CN.md](SECURITY.zh-CN.md)。保持服务绑定 `127.0.0.1`，绝不提交密钥或个人路径，将 Harness 执行视为代码执行，并从官方来源获取第三方运行时和模型。

## 开发

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py
python tokenflow.py --self-check
```

提交 Pull Request 前，请检查 diff 中是否包含密钥、绝对路径、日志、模型文件、凭据或未经审查的子进程行为。详见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。

## 当前边界

- Token 数是启发式估算值，与具体模型无关；
- 预处理是确定性清洗和首尾截取，不是语义摘要；
- 本地 HTTP 接口没有认证、配额和多用户隔离；
- TokenFlow 不再分发 FreeToken 运行时、模型权重或第三方 Harness；
- Laya、Jev、Ollama、SQLite 和向量缓存尚未纳入 MVP。

## 许可证

采用 MIT 许可证发布，详见 [LICENSE](LICENSE)。
