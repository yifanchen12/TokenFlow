# 合成样本与资料完整性验证

日期：2026-10-13。对应方案：[下一轮实施方案](../plans/next-round-synthetic-quality.md)。本轮只运行规则生成、离线检查和模拟响应回归，没有执行真实模型对照评测。

## 样本来源

生成器使用固定种子 `20261013`、模板版本 1，生成 120 道 `source_kind=synthetic` 样本。六类各 20 题：长文、冲突信息、表格、代码依赖、资料内干扰指令、文件解析。开发集与保留集各 60 题，每类各 10 题，使用不同模板族。

标准答案从明确的生成规则和整数运算得到，不采用模型回答。合成保留集仍由开发者设计，不能作为真实业务代表性或独立泛化证据。原有 60 道仓库问答另行检查，不与合成样本合称真实任务。

## 离线结果

| 语料 | 路径 | 样本数 | 压缩样本 | 标注事实丢失 |
|---|---|---:|---:|---:|
| 合成样本 | 单次提示词选取 | 120 | 70 | 0 |
| 合成样本 | 实际任务上下文选取 | 120 | 70 | 0 |
| 仓库问答 | 单次提示词选取 | 60 | 14 | 0 |
| 仓库问答 | 实际任务上下文选取 | 60 | 14 | 0 |

开发集中 40 道、保留集中 30 道合成题采用压缩，其他题保留原文。文件类 20 个样本均实际解析：TXT、DOCX、XLSX 各 4 个，PDF 文本提取 4 个，PNG 本地 OCR 4 个；没有缺依赖跳过项。OCR 的标签匹配只覆盖这些固定版式，不证明任意图片识别质量。

离线报告的模型准确率和提供商用量均为未知，质量门槛为 `not_evaluated`，不能视为模型质量通过。对资料内干扰指令的离线事实检查也不证明模型遵守指令边界。更保守的依赖处理可能增加全文回退；本轮不宣称模型 Token 节省或耗时改善。

## 四项问题的验证

- 多处规则和后置现行事实：当前任务资料中的全部明确词法命中参与选取，不再被前三个搜索预览排除。原复现的现行规则被保留。
- Python 全局常量及辅助函数：静态依赖链被保留；循环引用会终止遍历，动态查找、嵌套定义、复杂写入或超限回退原文。
- 截断答案：`finish_reason=length` 的模拟响应不进入完整对比，已返回 usage 保留。空答案、无效文本及协议待续工具调用同样拒绝。
- 失败类别：整体正确率 90%、某类别 0% 的复现现在失败。来源不同但类别同名的组分别判断；每类完整对比不足 10 个时标为未评测。

主程序 self-check、评测 self-check、Node 页面状态回归、语法和差异检查均通过。没有修改模型密钥保护、用户策略、真实任务人工确认要求或 Agent 工具权限。

Windows 新程序已构建并通过本地验证：Python 全局常量保留、真实 OCR 和重复图片缓存、零调用统计、密钥不回显、预算和请求回执持久化、草稿导出、存储管理、重启及关闭。程序为 `dist/quality/TokenFlow.exe`，`dist/quality/SHA256SUMS.txt` 已与文件核对。自动检查流程已加入合成语料两种选取路径及仓库任务上下文检查；本报告记录的是本地运行结果。

## 复现和本地产物

```powershell
python scripts/generate_eval_cases.py --seed 20261013 --output-dir tmp/synthetic-eval
python eval_workflow.py --cases tmp/synthetic-eval/all.json --report tmp/synthetic-direct.json
python eval_workflow.py --cases tmp/synthetic-eval/all.json --offline-task-context --report tmp/synthetic-context.json
python eval_workflow.py --cases eval_cases.repository.json --offline-task-context --report tmp/repository-context.json
```

本次实际生成目录为 `tmp/synthetic-quality-v1/`，包含 `development.json`、`holdout.json`、`all.json`、生成清单和 20 个源文件。报告为 `tmp/synthetic-direct.json`、`tmp/synthetic-context.json`、`tmp/repository-direct-next.json`、`tmp/repository-context.json` 及对应 Markdown。生成目录须不存在；复现时选用新目录，不能覆盖已有样本。

这些输出都位于被 Git 忽略的 `tmp/`。仓库保留生成器、回归检查和本报告；真实模型评测需要明确选择当前可用的模型与连接后再运行。
