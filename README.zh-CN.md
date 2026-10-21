# TokenFlow

**面向 AI Agent 工作流的本地低 Token 执行框架。**

TokenFlow 在任务交给本地模型或 AI 编程 Harness 之前，先减少不必要的上下文。核心保持小而清晰：Python 标准库、确定性预处理、本地 HTTP 页面，以及可选的 FreeToken、Codex、Claude 和 DSH 适配器。

English documentation: [README.md](README.md)。

## 核心能力

- 将任务分类为代码、表格、文档或通用任务；
- 按任务匹配摘取文本，保留完整 Python 函数、Markdown 小节或正文段落；抽取范围过大或不确定时保留原文；
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
- 提供可选的 OmniRoute 安装、本机启动和仅存于进程内存的 Endpoint Key 配置入口。

## 架构

页面的主要操作现在是“布置任务并自动完成”：本地读取文件和统计，按需由本地模型分类与摘要，规划器拆分任务，模型或 Harness 顺序执行，最后验收。支持的纯计数和统计任务不调用模型。推理任务必须获得有效的验收结论；未通过可在预算内修正一次，执行失败或未完成验收不会显示“已完成”。

### 保存连接、检测模型与 Harness 绑定

工作空间醒目展示累计输入费用节省估算，任务输入与结果并排。右侧“结果 / 执行过程 / 任务记录”分别显示答案、步骤交接与历史；结果标明所属任务，并区分本地校验与模型复核，可复制纯文本。连接、预算、存储和高级工具统一放在“设置”抽屉，Escape 关闭后焦点返回入口；支持手机布局、方向键切换标签和减少动态效果偏好。[界面验证与截图](docs/reports/premium-workspace-ui-2026-10-21.zh-CN.md)。

在“设置 → 模型提供商与 Harness 配置”中保存连接名称、Base URL、API Key 和协议（Chat Completions、Ollama 本机、Responses 或 Anthropic Messages）。保存后请求 `/models`，当前目录和历史模型记录跨重启保留；不提供目录的端点可以手动登记 ID。检测失败保留原记录并显示错误。自动本地检测仅探测已配置或默认的回环 Ollama / FreeToken 端点，不生成文本或下载模型。

Codex 模型来自本机配置及可见模型缓存，Claude 来自本机配置中的模型 ID。缓存和手工记录不保证当前账户可调用。Codex 绑定需要 Responses，Claude 需要 Messages；DSH 可绑定 Chat Completions 连接（包括北邮 API），使用临时 profile 补丁，不改全局配置。原生订阅登录仍由 Harness 使用。支持导入现有 API Key 连接，Key 不回显；绑定只影响 TokenFlow 启动的子进程，密钥通过子进程环境传递。默认继续使用只读 / 计划模式。

连接、策略和最近任务记录保存在用户缓存旁的 `connections.db`，不写入项目；可用 `TOKENFLOW_CONFIG_DB` 指定位置。Windows 使用当前系统用户的 DPAPI 保护 Key；POSIX 通过仅文件所有者可读的权限保护。Key 留空保留原值，更换地址需要重新填写。任务记录包含结果，应视为私人数据。旧版 OmniRoute 设置区仍是独立的进程内存配置。

策略可选择本地模型、规划器、执行器、验收器和 3–8 次阶段调用预算，默认 6 次。规划最多四步，允许不同已安装 Harness 传递步骤结果；一次 Harness 启动可能产生额外内部调用，TokenFlow 无法完整计量。页面区分可见提示词估算、提供商 usage 和未知用量，与一次原文输入相比的差值可能为负，不承诺实际账单节省或任意任务的语义正确性。规划可使用明确标注的摘要或预览；执行保留选取的资料，验收读取原始输入。

新增接口：`GET /api/config`；`POST /api/config/connection`、`/api/config/discover`、`/api/config/discover-local`、`/api/config/import-native`、`/api/config/settings`、`/api/tasks/start`；`GET /api/tasks` 和 `/api/tasks/<id>`。这些配置与任务接口要求回环访问和会话请求头，GET 也需要。`python test_orchestration.py` 执行离线编排检查，也包含在主程序 `--self-check` 中。本机 Codex TOML 导入需要 Python 3.11+，Windows 构建已包含。

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
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py omniroute_manager.py
python tokenflow.py --self-check
python tokenflow.py
```

然后打开 <http://127.0.0.1:8765>。Windows 用户也可以双击 `start_tokenflow.cmd`。

### Windows 可执行文件

可从 [GitHub 最新 Release 下载 Windows 可执行文件](https://github.com/yifanchen12/TokenFlow/releases/latest/download/TokenFlow.exe)，也可以执行 `build_tokenflow_exe.cmd` 自行生成 `dist/TokenFlow.exe`。程序会启动本地服务并在默认浏览器打开 TokenFlow 控制台；通过“设置 → 运行环境与本机服务 → 关闭服务”即可停止。二进制文件不提交到源码分支，而通过 Release 附件分发。

SQLite 缓存默认保存在当前用户的应用数据目录（Windows 示例：`%LOCALAPPDATA%\TokenFlow\tokenflow.db`）。设置 `TOKENFLOW_DB_PATH` 可指定其他位置。打开状态页不会创建数据库；首次索引文档时才会初始化。

如果在旧版工作目录或程序目录发现 `tokenflow.db`，缓存卡片会提供手动导入入口。文档会按内容去重并合并到当前缓存；旧数据库文件保持不变。

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

`POST /api/chat` 支持 `provider: "auto" | "freetoken" | "ollama" | "omniroute" | "laya" | "cloud"`。自动路由遵循 `TOKENFLOW_PROVIDER_ORDER`，默认顺序为 `freetoken,ollama,laya,cloud`，但**只尝试 HTTP(S) 回环地址，且始终排除 `cloud` 和 `omniroute`**。即使 OmniRoute 运行在本机，也可能把内容转发给远程或付费模型，因此必须显式选择。远程 FreeToken、Ollama、Laya 端点同样不会进入自动路由。没有符合条件的端点时会报错。明确指定提供商才允许使用其远程地址；页面会在显式选择时再次确认，直接调用 API 的客户端须自行确认外发。

`POST /api/local-chat` 也只允许本机 FreeToken 地址；如果有意使用远程 FreeToken，请通过 `/api/chat` 明确指定 `provider: "freetoken"`。

通过环境变量配置：

```text
TOKENFLOW_FREETOKEN_URL       本地 FreeToken 地址，默认 http://127.0.0.1:1919
TOKENFLOW_OLLAMA_URL          Ollama OpenAI 兼容地址，默认 http://127.0.0.1:11434/v1
TOKENFLOW_OMNIROUTE_URL       OmniRoute /v1 地址，默认 http://127.0.0.1:20128/v1
TOKENFLOW_OMNIROUTE_API_KEY   OmniRoute 的 Endpoint Key；已测试的本机部署及远程网关均需提供
TOKENFLOW_OMNIROUTE_ALLOWED_MODELS  可选的明确模型 ID 白名单，逗号分隔；OmniRoute 始终要求明确模型
TOKENFLOW_LAYA_URL            用户自行提供的 Laya 兼容端点
TOKENFLOW_LAYA_API_KEY        可选 Laya 密钥，只从环境变量读取
TOKENFLOW_CLOUD_URL           用户自行提供的云端 OpenAI 兼容端点
TOKENFLOW_CLOUD_API_KEY       云端密钥，只从环境变量读取
TOKENFLOW_PROVIDER_ORDER      逗号分隔的提供商顺序
```

页面的“OmniRoute 下载与连接设置”会检测已安装的 CLI、启动默认本机网关；Windows 上还可在确认后打开可见窗口，执行上游包的 `npm install -g omniroute`。安装前须自行准备 Node.js/npm，并核对[上游项目](https://github.com/diegosouzapw/OmniRoute)和软件包来源；TokenFlow 不捆绑 OmniRoute，也不安装 Node.js。其他系统请按上游说明安装；已经安装的 CLI 不会重复安装。

在页面输入 `/v1` 地址和 Endpoint Key 后保存。页面输入的 Key 只留在当前 TokenFlow 进程的内存中（也可通过 `TOKENFLOW_OMNIROUTE_API_KEY` 提供），不会在接口响应中回显、写入磁盘或放进命令参数；关闭 TokenFlow 后该页面配置失效。Key 输入框留空表示保留现有 Key。地址不得包含凭据、查询或片段；远程网关必须使用 HTTPS 并提供 Key。“启动本地网关”只适用于默认回环地址，不管理自定义或远程网关。聊天接口调用 `/v1/models` 和 `/v1/chat/completions`。本仓库不猜测 Laya 的固定公网地址，请按实际部署配置。Jev 通过 `POST /api/jev/decision` 显式调用，使用 `JEV_API_KEY` 和可选的 `TOKENFLOW_JEV_URL`，不会被静默当作普通聊天模型调用。

OmniRoute 必须填写明确模型 ID（例如 `ollama-local/qwen3.5:9b`）；`auto` 会在发送内容前被拒绝。可在页面填写模型白名单并保存连接配置，或设置 `TOKENFLOW_OMNIROUTE_ALLOWED_MODELS`；白名单按完整 ID 匹配。留空允许明确指定的模型，不允许自动选择。同一规则适用于聊天、评测和 TokenFlow 启动的 Codex。`POST /api/omniroute/preview` 接受 `{"model":"<模型ID>"}`，只展示网关与所选模型，不发送任务或启动 Harness；页面会在执行前自动预览。TokenFlow 无法确认最终上游费用，也无法约束网关组合内部的回退，请在 OmniRoute 中核对这些设置。

## 文档解析与缓存

`POST /api/parse` 支持带本地 `path` 的 JSON 请求，也支持 `multipart/form-data` 文件上传。支持 TXT、Markdown、常见源码/文本文件、DOCX、XLSX；安装 `pypdf` 后支持 PDF 文本提取。

JSON 请求上限为 2 MB；上传文件上限为每个文件 20 MB。

`POST /api/index` 将提取文本保存到 SQLite，并生成确定性的 64 维哈希向量。中文使用连续二字/三字片段，英文使用单词；`POST /api/search` 在线性扫描时只保留得分最高的少量结果。旧向量会在新版首次索引或检索时事务性重建，原有文档保留；状态读取不会迁移或创建数据库。这是词法检索，不宣称等同于语义 Embedding 模型。

可选 PDF 解析依赖：

```powershell
python -m pip install pypdf
```

## PC Agent 安全边界

`POST /api/pc/plan` 只接受 `move`、`click`、`type`、`key`、`wait` 五类动作，并始终返回需要确认的 dry-run。`POST /api/pc/execute` 只有在显式设置 `TOKENFLOW_PC_AGENT_EXECUTE=1` 且安装可选依赖 `pyautogui` 后才会执行。

页面允许编辑 PC 动作 JSON 并预览校验后的计划；预览不会执行动作。`POST /api/shutdown` 用于停止本地服务，页面的“关闭服务”按钮会调用该接口。

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

### 收益统计与质量评测

`/api/run` 估算正文 Token；模型和 Harness 接口统计实际使用的可见提示词文本。长内容按任务匹配，保留完整 Python 函数、Markdown 小节或正文段落。总结、翻译等广覆盖任务、无匹配片段、抽取范围超过限制或无估算收益时发送原文，并返回 `compression_applied: false`。这种启发式方法不保证所有相关事实都被找到。`token_counting.scope` 标明统计范围；数字不包含隐藏开销，不等同于账单用量。

运行内置的三类示例基准，不会连接模型或上传内容：

```powershell
python eval_workflow.py
```

需要比较同一模型对原文和候选压缩文本的答案时，明确指定已配置的提供商；这会向该端点发送两份提示词：

```powershell
python eval_workflow.py --provider freetoken --model <已安装模型名>
```

仓库还提供了 60 个基于实际项目代码和公开文档的可复现问答任务。它们属于项目问答基线，不是实际用户任务日志：

```powershell
python eval_workflow.py --cases eval_cases.repository.json --report tmp/offline.json
python eval_workflow.py --cases eval_cases.repository.json --provider ollama --model <已安装模型名> --report tmp/model-comparison.json
```

本机服务使用自定义端口时设置 `TOKENFLOW_OLLAMA_URL`。评测比较原文与 TokenFlow 实际选中的提示词，记录答案、响应中的 usage 和耗时；交替执行顺序，分别统计文本估算与提供商报告的输入/输出总量。每个任务完成后生成 JSON 和 Markdown；报告可能含任务答案，分享前须检查。

Ollama 评测使用原生聊天 API，设置 `--ollama-context`（默认 32768）、温度 0 和关闭思考，并记录设置；原文和处理后提示词使用相同参数。请为原文和输出预留足够上下文容量；普通应用聊天仍使用原有 OpenAI 兼容接口。这遵循 [Ollama 的上下文说明](https://docs.ollama.com/api/openai-compatibility#setting-the-local-context-size)。

自有样本通过 `--cases` 提供，每项需要 `task`、`content`（或样本文件所在目录内的相对 `source_file`）及 `must_keep` 关键原文数组。模型评测还需要 `expected_answer` 字符串或可接受答案数组。默认 `answer_match: "exact"` 精确匹配；显式使用 `contains_all` 时要求全部片段存在，不会把 `17` 中的 `7` 判为正确。请准确标注 `source_kind`。

默认质量门槛要求至少 50 个完整对比、标注事实零丢失、处理后准确率至少 90%、答案准确率下降不超过 2 个百分点，且提供商报告的输入 Token 至少节省 5%。缺少 usage 或样本未完成时标记 `not_evaluated`，不会算通过。`--enforce-gate` 会在门槛未通过时以退出码 2 结束，阈值可调整。通过只针对所提供样本，不证明普遍质量或账单收益；已测结果与限制见[项目问答对比报告](docs/reports/repository-quality.md)。

## Harness 执行

页面可以把处理后的任务交给 Codex、Claude 或 DSH；无估算收益时保留原文。代码任务优先 Codex，其它任务优先 Claude；长文本和代码任务优先 quality 模型。

```powershell
$body = @{task="分析这段代码"; content="print('hello')"; harness="auto"; model="auto"} | ConvertTo-Json
$session = Invoke-RestMethod http://127.0.0.1:8765/api/session
$headers = @{"X-TokenFlow-Token"=$session.token}
Invoke-RestMethod http://127.0.0.1:8765/api/execute `
  -Method Post -ContentType "application/json" -Headers $headers -Body $body
```

默认 Codex 使用只读模式，Claude 使用计划模式。DSH 需要配置 `DSH_HOME` 和 profile。

若想让 TokenFlow 启动的 Codex CLI 走 OmniRoute，在 `/api/execute` 指定 `harness: "codex"`、`harness_backend: "omniroute"` 和白名单允许的明确 `model`，或使用页面选项；`auto` 会被拒绝。默认后端仍为 `direct`。OmniRoute 模式使用本次调用的 `/v1/responses` 参数，不修改全局 Codex 文件。上游账号、费用和回退由网关决定，网关须已启动并支持 Responses API；这不是 Codex 桌面应用的全局设置。API 客户端应自行取得外发与可能计费的授权。

## HTTP 接口

```text
GET  /health
GET  /api/harnesses
GET  /api/freetoken
GET  /api/omniroute
GET  /api/providers
GET  /api/tokenizer
GET  /api/store
GET  /api/session
GET  /api/local-models
POST /api/shutdown
POST /api/run
POST /api/local-chat
POST /api/chat
POST /api/execute
POST /api/parse
POST /api/index
POST /api/store/migrate
POST /api/search
POST /api/pc/plan
POST /api/pc/execute
POST /api/jev/decision
POST /api/freetoken/install
POST /api/freetoken/start
POST /api/omniroute/config
POST /api/omniroute/install
POST /api/omniroute/start
POST /api/omniroute/preview
```

所有 POST 请求都必须携带 `GET /api/session` 返回的进程级令牌；浏览器请求还必须来自匹配的本机 Origin。OmniRoute 设置接口额外要求请求来自回环地址。该令牌用于降低跨站请求风险，不是用户身份认证。安装和启动接口可以控制本地进程，不要将它们转发到公网反向代理。

## 安全与隐私

部署前请阅读 [SECURITY.zh-CN.md](SECURITY.zh-CN.md)。保持服务绑定 `127.0.0.1`，绝不提交密钥或个人路径，将 Harness 执行视为代码执行，并从官方来源获取第三方运行时和模型。

## 开发

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py omniroute_manager.py
python tokenflow.py --self-check
python eval_workflow.py --self-check
```

GitHub Actions 会在 Windows 自动自检、构建 EXE，通过 `scripts/smoke_exe.ps1` 验证启动、状态、预览和关闭，并提供 EXE 与 SHA256 构建附件。正式 Release 单独上传已验证的程序和校验文件，不把二进制提交到源码分支。

提交 Pull Request 前，请检查 diff 中是否包含密钥、绝对路径、日志、模型文件、凭据或未经审查的子进程行为。详见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)。

## 当前边界

- Token 数可能是启发式估算；即使使用可选 tokenizer，可见提示词估算也不是账单用量；
- 预处理是确定性、保留结构的抽取，不是语义摘要；真实业务代表性的质量仍需外部任务验证；
- 本地 HTTP 接口没有用户认证、配额和多用户隔离；进程级写令牌仅用于降低浏览器跨站请求风险；
- TokenFlow 不分发 FreeToken、OmniRoute 运行时、模型权重或第三方 Harness；
- 提供商是否可用取决于用户配置和网络条件；
- PDF 提取需要可选 `pypdf`，本地模型接入不代表仓库包含模型权重；
- 哈希向量缓存是确定性检索基础设施，不是训练得到的 Embedding 模型；
- PC Agent 执行需要显式环境开关，并且仍要求人工审查。

## 许可证

采用 MIT 许可证发布，详见 [LICENSE](LICENSE)。


## 本地文字识别

PNG/JPEG/WebP/BMP 使用本地 RapidOCR 中英文识别，默认 CPU 离线运行。PDF 先提取文字；无文字页，以及存在较大图片区域但该区域文字很少的混合页，由 PyMuPDF 渲染后补充 OCR，每份文件最多识别 30 页。已有文字与新增识别行合并去重，结果记录 `ocr_pages`、置信度与警告；小 logo 不触发识别。这是版面启发式检查，识别内容仍需核对、编辑。缺少 PyMuPDF 时，文字页会提示未检查图片正文，扫描页会报错。OCR 不调用云端、不占用独立显卡显存。可选依赖：`rapidocr_onnxruntime==1.2.3`、`Pillow`、`PyMuPDF`。安装后，构建脚本会把 OCR 配置和模型打包进 EXE。

分类摘要角色可显式选择云端 API，调用统计会按实际连接归类。可从本机 `BUPT_API_BASE_URL` / `BUPT_API_KEY` 导入北邮连接。DSH headless 通过临时补丁绑定模型；Codex 原生登录使用 HTTPS，模型目录可由 CLI `model/list` 检测，避免使用不可调用的应用缓存模型。

## 输入、任务恢复与完整流程评测

- XLSX 保留内联/共享文本、空单元格、空行和工作表顺序；单表输出 TSV，多表带工作表名称。公式读取文件中的缓存值，缺失缓存时显示公式及提示，不重新计算公式。DOCX 保留段落和段内连续文本。
- Codex、Claude 的完整提示词通过 UTF-8 标准输入传递；DSH headless 使用临时 `headless-runner` 任务补丁，调用后清理。长资料不占用命令行参数长度。
- 明确的短资料单步问答、摘要、翻译可直接执行并验收，通常两次调用；复杂任务仍规划。额外分类摘要只用于适合缩短规划输入的长资料。未通过验收仍可在预算内修正一次。
- 任务使用 SQLite 内容哈希复用文档索引，仅检索本次资料并记录出处。检索使用词法匹配和哈希评分；保留函数/章节结构，范围过大时回退原文。全文总结、翻译、统计等读取完整资料，验收始终读取原文。
- 页面可以取消运行任务、查看历史后返回当前任务，并在刷新后恢复跟踪。`POST /api/tasks/cancel` 接受 `{"id":"<任务ID>"}`，使用与启动接口相同的回环和会话头校验。`cancelling` 表示停止后续调用并等待正在进行的 API 请求返回；Harness 进程树会终止。`cancelled` 保留此前已完成的结果。服务重启会把未结束的任务标为 `interrupted`。

完整流程评测比较“一次原文调用”和真实的摘要、规划、执行、验收、修正流程。所有阶段使用一个明确的已保存 API 连接及同一模型，原文和最终执行输出上限 2400 Token，辅助阶段使用报告记录的分角色上限，最多 3–8 次阶段调用；不启动 Harness。连接 ID 来自 `/api/config` 的 `connections[].id`。评测使用临时数据库，不改已保存策略或任务记录。

```powershell
# 会调用所选端点；6 个独立于仓库源码的合成场景只用于流程冒烟，不是生产质量证明。
python eval_workflow.py --pipeline --connection <连接ID> --model <模型ID> --cases eval_cases.workflow.json --min-cases 6 --report tmp/pipeline.json

# 完全离线回归（主自检也包含 test_optimizations.py）
python tokenflow.py --self-check
python eval_workflow.py --self-check
node scripts/test_ui.js
```

报告逐阶段记录输入/输出 usage、调用状态、修正次数和完整耗时，并汇总全流程 Token。失败调用缺少用量时保留“未知”，不把估算当作实际用量；未通过验收、样本不足或用量不全不能通过门槛。完整流程可能比单次调用更贵。正式质量验证请使用独立标注的留出任务；这六个合成场景不作为泛化或节省承诺。

## 预算、提交恢复与存储管理

- 默认整任务预算 64000 Token，上下文容量 32768 Token；分类摘要、规划、执行、验收的输出上限分别为 1024、1536、2400、1024。发送前检查预估输入加输出预留，已返回的实际 usage 优先记账；未知部分保守估算。Responses/Messages 截断失败时保留已经返回的用量。这是流程约束，不能保证账单或 Harness 内部开销；上下文容量需要按实际模型调整。
- `/api/tasks/start` 可携带 `request_id`。同一 ID、相同任务资料重复提交会返回已有任务，完成或服务重启后也不会重复执行；同一 ID 携带不同内容会拒绝。页面只在 localStorage 保存待确认请求 ID 和时间，不保存资料。`GET /api/tasks/request/<ID>` 可恢复响应丢失的提交；删除历史后仍保留最小回执，旧请求不会再次执行。
- 文件导入按文件内容、扩展名、解析器和依赖版本复用成功的解析/OCR 结果。失败结果不缓存。“缓存、历史与保留策略”显示内容容量、数据库文件大小、已保存资料/解析结果和任务数量，并提供删除和可选保留期限。默认 0 不自动清理。启用后在导入、任务结束及保存/整理策略时清理符合条件的记录，运行任务受保护。SQLite 删除后的空间可被复用，文件大小可能暂不减少。

## 真实任务样本准备

历史任务可“导出评测样本草稿”，包含缓存中的原资料和候选回答。草稿的正确答案与关键事实留空，不把模型回答当作标准答案。人工填写 `expected_answer`、`must_keep`、`category`，并核对后将 `label_status` 改为 `verified`，才能准备和评测。原缓存被清理后，需要重新提供资料。

```powershell
# 创建空白标注模板，不生成或伪造真实任务。
python scripts/prepare_eval_cases.py --template tmp/real-tasks/manifest.json
# 填写真实任务及人工答案。source_file 相对清单所在目录；支持 DOCX/XLSX/PDF/图片等。
python scripts/prepare_eval_cases.py --manifest tmp/real-tasks/manifest.json --output tmp/real-tasks/prepared.json
# 离线检查，不调用模型。
python eval_workflow.py --cases tmp/real-tasks/prepared.json --report tmp/real-offline.json
# 仅明确选择连接与模型后运行；此命令会调用该端点。
python eval_workflow.py --pipeline --connection <连接ID> --model <模型ID> --cases tmp/real-tasks/prepared.json --min-cases 20 --report tmp/real-pipeline.json
```

准备后的数据集是可移植的文本快照，去除候选答案。报告按类别比较正确率、完整输入加输出 Token 和中位耗时；完整流程门槛同时检查输入与总 Token 节省。真实样本、原文件和报告建议放入已忽略的 `tmp/`，不随源码提交。模板及回归夹具不算真实评测成绩。

## 合成样本与资料完整性检查

可以在本机按固定规则生成 120 个合成样本：长文、冲突信息、表格、Python 依赖、资料内干扰指令、文件解析六类各 20 题。开发集与保留集各 60 题，模板族分开；标准答案直接由规则计算，不取自模型回答。文件样本包含 TXT、DOCX、XLSX、PDF 和 PNG。生成依赖沿用已有可选组件；缺少依赖会列入 `generation.json` 并以退出码 2 结束，不能算完成。输出目录必须不存在，避免覆盖已有资料。

```powershell
python scripts/generate_eval_cases.py --seed 20261013 --output-dir tmp/synthetic-eval
# 检查单次提示词选取；不会调用模型。
python eval_workflow.py --cases tmp/synthetic-eval/all.json --report tmp/synthetic-direct.json
# 实际任务的解析、索引、检索和上下文选取；数据库完全隔离，不调用执行器或模型。
python eval_workflow.py --cases tmp/synthetic-eval/all.json --offline-task-context --report tmp/synthetic-context.json
```

`development.json` 和 `holdout.json` 可分别输入评测。保留集仍来自开发者设计的合成模板，不等同于独立真实任务。离线模式只检查资料与标注保留，模型正确率和实际用量保持未知，门槛不会显示通过。干扰指令样本的离线事实保留不证明模型遵守指令边界。

检索前三条现在只作为搜索预览；它们不能排除其他明确命中的原文。Python 抽取补充可解析的模块级常量、导入和辅助函数依赖；动态查找、嵌套定义、复杂写入或抽取超限时回退原文。`sources_scope=search_preview` 区分搜索预览与实际选取范围。

模型评测和编排共用完整文本检查；截断、工具调用待续、空答案或无效文本结构不能成为完整答案对比，已返回的 usage 仍记录。报告按来源及类别分别统计，合成、仓库问答和真实任务不混作同一类质量证据。每个来源/类别沿用整体正确率及下降门槛，并至少需要 10 对完整结果（`--min-category-cases` 可调整）；某类失败阻止整体通过，样本不足标为未评测。调整阈值只改变所提供样本的门槛，不代表生产质量已被证明。

## 统计可靠性、验收预检与历史查询

- 表格求和和中位数按输入位数、指数跨度及行数设置 Decimal 精度；数值或精度超出 4096 的安全范围时拒绝计算。均值的 `mean_exact=false` 表示有限精度下有舍入。缺缓存公式保留占位和 `missing_formula_count`；包含缺失公式或混合非数值的部分数值统计标为 `needs_review`，不能通过验收。只统计行列数不因此失败。解析警告随上传资料和路径任务进入结果，公式仍须在表格软件中重新计算并保存。
- 首次模型调用前检查原文验收最低上下文与预算，规划后及后续阶段前继续预留验收开销；已知不可行时提前停止。动态输出、估算误差和 Harness 内部调用仍受原有计量边界限制。可见原文基线使用执行器的相同消息封装（`baseline_scope=executor_visible_prompt`），`metrics.by_role` 列出各用途调用的输入/输出估算和已报告用量。
- 历史区域支持任务文字、状态筛选和翻页；筛选不会改变当前运行任务。受保护的 `GET /api/tasks` 增加 `limit`（1–100，默认 20）、`offset`（0–1000000）、`query`（最多 200 字符）、`status` 参数。文字按字面子串匹配，状态包括 `running`、`cancelling`、`completed`、`failed`、`cancelled`、`needs_review`、`interrupted`。返回 `total`、`offset`、`limit`、`has_more`；按 ID 读取保持兼容。

本机 Ollama 完整流程评测可使用原生 API，固定上下文、温度 0、关闭思考；原文与全部阶段使用同一连接和模型。显式 Ollama 本机协议在日常调用中也使用这些设置；旧版回环 Chat Completions 连接可通过下列参数仅在评测中启用原生设置，不修改保存协议：

```powershell
python eval_workflow.py --pipeline --ollama-native --connection <本机Ollama连接ID> --model qwen3.5:9b --ollama-context 32768 --cases tmp/synthetic-eval/holdout.json --report tmp/pipeline-ollama.json
```

完整对比正确率只覆盖成功配对，须同时检查 `accepted_correct_cases / cases`。`reported_all_raw_*` 与 `reported_pipeline_*` 保留全部任务的原文和流程用量，`reported_all_*_token_saving_ratio` 比较包含失败调用的总开销；缺用量时仍为未知。上一轮修复和实测结果见 [可靠性验证报告](docs/reports/reliability-2026-10-15.zh-CN.md)。

## 结构化验收与本地单值查询

显式选择“Ollama（本机）”协议时，地址必须是以 `/v1` 结尾的回环地址。日常任务和评测共用 Ollama 原生调用、关闭思考、温度 0 和策略中的上下文容量；规划与验收使用小型 JSON Schema，客户端仍检查结论与字段一致性。单次高级调用保留自己的输出上限。新检测出的 Ollama 使用此协议，旧连接不会自动换协议；原有 Harness 绑定规则保留。

验收只核对候选回答，保留原文依据；只有错误证据确实出现在原文和候选中，才允许预算内的一次修正。格式无效、证据不足或再次验收失败时保留回答并标记需复核。发生修正后，结果区可展开查看原回答、修正结果和各自的验收状态。结构约束和证据存在都不保证模型的事实判断正确。

窄范围的明确 CSV/TSV 查询可直接本地完成，例如 `CSV表格中 "K 17" 的 free_stock 值是多少？只回答数字。`。第一列须是唯一行键，列名须明确唯一。重复键、缺列、目标缺值或公式无缓存标记需复核；多表、模糊问题或多步任务保留原有流程。成功查询为确定性验收，模型调用为 0。

全流程评测另存 `candidates`、首个候选与最终候选正确性、修正改善/退化数和阶段错误类别。可生成独立于旧模板族的 60 题合成验证集（六类各 10 题），输出目录仍须不存在：

```powershell
python scripts/generate_eval_cases.py --validation-only --seed 20261020 --output-dir tmp/acceptance-validation
python eval_workflow.py --cases tmp/acceptance-validation/validation.json --offline-task-context --report tmp/acceptance-context.json
```

离线检查只验证标注保留，模型门槛仍需实际对照评测。实现及两组实测结果见 [2026-10-20 验收验证报告](docs/reports/acceptance-2026-10-20.zh-CN.md)。

## 累计输入费用节省与任务进度

首页突出显示已记录的输入 Token 净减少与费用折算。参考价格固定为 [GPT-6.1 Sol Standard](https://developers.openai.com/api/docs/models/gpt-6.1-sol)（2026-10-20 核对）：未缓存输入 $2/百万 Token，单次输入超过 272K 时整个请求按 $4/百万 Token。基线与每次阶段分别计价；规划、执行、验收、修正和路由回退的已知尝试都会扣除。失败或未通过验收的操作没有正收益，净额为负时显示额外输入开销。

金额为**可见输入的参考费用估算**，并非实际账单。官方缓存输入 $0.10、输出 $10/百万 Token 在详情中作参考；这里不假设原文模型的输出、不计算未知输出节省，也不把本地推理的全部 Token 当成减少的 Token。缺失历史和 Harness 内部未知调用无法补算，口径不完整者单独显示未计入。

统计只保存数值与去重标识，位于现有 `connections.db` 的独立表。重复保存、轮询与请求回执复用不重复计入；重启、删除任务历史和清理解析缓存不清空累计。可恢复的旧任务按已有字段补录，预处理预览及本仓库隔离评测不计入个人累计。`GET /api/config/savings` 沿用本机与会话保护。

任务运行显示当前阶段、已耗时、运转符号与等待进度条，不伪造模型完成百分比。取消中和断线恢复有专门提示，终态停止动画；刷新可恢复正在运行任务的耗时。手机主操作按钮整行显示，减少动态效果设置会关闭动画。

## 端云协同与复杂任务

工作台可选择“端云协同”“仅本地”或“按高级角色配置”。连接设置提供端侧整理连接及复杂任务连接的简化选择；复杂连接可一次应用到规划、执行和验收，也可展开高级分配分别设置。已有角色设置继续保留；旧设置未指定协同策略时按原分工执行。

点击“预览本次分工”只核对配置，不调用模型。启动时自动再预检。端侧推理依据保存连接的 `local` 标记识别，云端网关不能标成本机直接推理；原生 Harness 单独显示，内部执行位置和用量可能未知。“仅本地”禁止调用云端及原生 Harness，纯统计/查表仍可零模型完成。

复杂计划最多四个顺序步骤，可声明 `id`、`depends_on`、`input_scope`（`source` / `dependencies` / `both`）及 `expected_output`。依赖只指向前序步骤，最后一步须汇总必要产出。执行时只交接指定资料和依赖结果；旧格式计划仍按原依赖语义兼容。辅助摘要不替代原文，最终验收继续依据原始资料。

端侧模型步骤实际调用失败后，只有协同策略允许且剩余调用/Token 预算足以预留验收时，才升级一次到已配置的复杂执行器；失败与升级均计入用量。云端失败或预算不足保留已完成步骤，明确显示部分结果，不把中间结果标成最终完成。结果区展示实际分工、依赖、耗时、升级原因和各步产出。

`POST /api/tasks/preview` 与任务接口使用相同的本机/会话保护；启动请求可携带 `collaboration_mode`（`hybrid` / `local_only` / `configured`），参与请求去重。评测保留步骤与实际位置；同模型对照继续使用原角色模式，不混称端云验证。运行 `python test_collaboration.py` 检查分工、交接、禁止外发、预算、部分结果和预检保护。实际模型效果与离线夹具分开报告，不承诺任意复杂任务都正确或必然节省费用。
