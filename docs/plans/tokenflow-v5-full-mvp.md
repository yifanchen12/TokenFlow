# TokenFlow V5 全功能 MVP 实现记录

## 已覆盖的能力

- FreeToken、Ollama、Laya 兼容端点和云端 OpenAI 兼容端点统一路由；
- Jev 显式决策接口，作为类型化路由/护栏，不替代普通聊天模型；
- 可选真实 tokenizer 与明确的 heuristic 回退；
- TXT、Markdown、源码、DOCX、XLSX、可选 PDF 的文本解析；
- SQLite 文档、分块和确定性哈希向量缓存；
- 白名单 PC Agent 动作计划与显式执行开关；
- 本地 HTTP API 与页面入口。

## 安全边界

- 服务默认只监听回环地址；
- 提供商密钥只从环境变量读取，不写入日志和数据库；
- 云端/Laya/Jev 调用必须由用户配置端点或密钥，不自动外发内容；
- PC Agent 默认 dry-run，只允许有限动作类型；
- 不支持 Shell、任意可执行文件或自动化无限循环；
- 模型权重、第三方运行时和安装器不进入仓库。

## 验证

- 所有 Python 模块通过 `py_compile`；
- `python tokenflow.py --self-check` 通过；
- JSON API 验证覆盖提供商状态、tokenizer、索引、搜索、PC Agent 计划和未配置提供商错误；
- multipart 上传验证通过；
- DOCX/XLSX 内存样本解析通过；
- 远程提供商和 PDF 依赖在用户配置后进行真实端到端验证。

## 已知边界

- Laya 端点没有被假设为固定公网地址，需要用户提供兼容 API 地址；
- PDF 解析依赖 `pypdf`，扫描件 OCR 不在本版；
- SQLite 向量缓存使用确定性哈希向量，不等同于训练得到的 Embedding；
- Jev 只做显式类型化决策，不生成长文本；
- PC Agent 不包含自动屏幕识别和无人值守循环。
