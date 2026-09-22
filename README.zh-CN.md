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
- 支持可配置的 Ollama、Laya 兼容端点和云端 OpenAI 兼容端点；
- 支持 TXT、Markdown、源码、DOCX、XLSX，以及可选 PDF 文本提取；
- 使用 SQLite 保存文档分块和确定性的 64 维向量缓存；
- 通过显式接口调用 Jev 决策/路由能力，不把 Jev 当作普通聊天模型；
- 提供白名单 PC Agent 动作，默认 dry-run，执行需要显式授权；
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

### Windows 可执行文件

首次执行 `build_tokenflow_exe.cmd` 后，会生成 `dist/TokenFlow.exe`。该 EXE 无控制台窗口，会启动本地服务并自动打开默认浏览器中的 TokenFlow 控制台。`dist/` 被 Git 忽略，因为二进制文件应通过经过审查的 Release 附件分发，而不是提交到源码分支。

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

## 提供商路由

`POST /api/chat` 支持 `provider: "auto" | "freetoken" | "ollama" | "laya" | "cloud"`。自动路由遵循 `TOKENFLOW_PROVIDER_ORDER`，默认顺序为 `freetoken,ollama,laya,cloud`。

通过环境变量配置：

```text
TOKENFLOW_FREETOKEN_URL       本地 FreeToken 地址，默认 http://127.0.0.1:1919
TOKENFLOW_OLLAMA_URL          Ollama OpenAI 兼容地址，默认 http://127.0.0.1:11434/v1
TOKENFLOW_LAYA_URL            用户自行提供的 Laya 兼容端点
TOKENFLOW_LAYA_API_KEY        可选 Laya 密钥，只从环境变量读取
TOKENFLOW_CLOUD_URL           用户自行提供的云端 OpenAI 兼容端点
TOKENFLOW_CLOUD_API_KEY       云端密钥，只从环境变量读取
TOKENFLOW_PROVIDER_ORDER      逗号分隔的提供商顺序
```

TokenFlow 不打印或保存提供商密钥。本仓库不猜测 Laya 的固定公网地址，请按实际部署配置。Jev 通过 `POST /api/jev/decision` 显式调用，使用 `JEV_API_KEY` 和可选的 `TOKENFLOW_JEV_URL`，不会被静默当作普通聊天模型调用。

## 文档解析与缓存

`POST /api/parse` 支持带本地 `path` 的 JSON 请求，也支持 `multipart/form-data` 文件上传。支持 TXT、Markdown、常见源码/文本文件、DOCX、XLSX；安装 `pypdf` 后支持 PDF 文本提取。

`POST /api/index` 将提取文本保存到 SQLite，并生成确定性的哈希向量缓存；`POST /api/search` 返回相似度最高的文本分块。这是本地词法/向量检索缓存，不宣称等同于语义 Embedding 模型。

可选 PDF 解析依赖：

```powershell
python -m pip install pypdf
```

## PC Agent 安全边界

`POST /api/pc/plan` 只接受 `move`、`click`、`type`、`key`、`wait` 五类动作，并始终返回需要确认的 dry-run。`POST /api/pc/execute` 只有在显式设置 `TOKENFLOW_PC_AGENT_EXECUTE=1` 且安装可选依赖 `pyautogui` 后才会执行。

系统不支持 Shell 动作、任意可执行文件动作或自动屏幕决策循环。启用执行前必须人工审查每个动作。

## 真实 Token 计数

TokenFlow 支持可选的本地 tokenizer 后端。未安装可选后端时，响应会明确标记为 `backend: "heuristic"` 和 `exact: false`。

使用本地 Hugging Face `tokenizer.json`：

```powershell
python -m pip install tokenizers
$env:TOKENFLOW_TOKENIZER_PATH = "<tokenizer目录或文件>"
```

使用 OpenAI 兼容编码：

```powershell
python -m pip install tiktoken
$env:TOKENFLOW_TOKENIZER_BACKEND = "tiktoken"
$env:TOKENFLOW_TIKTOKEN_ENCODING = "cl100k_base"
```

通过 `GET /api/tokenizer` 查看当前后端。Tokenizer 只从本地加载，TokenFlow 不会自动下载模型文件。

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
GET  /api/providers
GET  /api/tokenizer
GET  /api/store
GET  /api/local-models
POST /api/run
POST /api/local-chat
POST /api/chat
POST /api/execute
POST /api/parse
POST /api/index
POST /api/search
POST /api/pc/plan
POST /api/pc/execute
POST /api/jev/decision
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
- 提供商是否可用取决于用户配置和网络条件；
- PDF 提取需要可选 `pypdf`，本地模型接入不代表仓库包含模型权重；
- 哈希向量缓存是确定性检索基础设施，不是训练得到的 Embedding 模型；
- PC Agent 执行需要显式环境开关，并且仍要求人工审查。

## 许可证

采用 MIT 许可证发布，详见 [LICENSE](LICENSE)。
