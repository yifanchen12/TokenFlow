# Windows EXE 全流程验证 · 2026-11-05

本机打包产物为 `dist/TokenFlow.exe`，共 122,030,705 字节。SHA-256 为 `4bde88fe5da82bc79801f18fbc7531259d846ff88ec72b9a62e99a1718a12a5c`。

## 实际运行

- 新版 EXE 在独立临时数据库中导入北邮连接，经 HTTPS 获取模型目录。使用 `deepseek-v4-flash` 处理文本文件中的库存任务。上午领取 17 份，下午领取 28 份，退回 3 份。标准答案与程序答案均为 42。
- 任务先预检，再由规划、执行和独立复核各调用一次北邮 API。验收通过，API 合计报告 2,170 token，本地模型调用为 0。随后从 EXE 查询已完成任务，并验证相同请求复用原任务。此项结果来自一次合成任务。
- 独立的 EXE 回归覆盖进程单例、文本解析与缓存、查表、缺失公式处理、失败任务恢复、重复请求、重启后的历史与预算持久化、退出。本机跳过 OCR 推理。
- 源码自检、界面交互检查通过。60 项仓库资料和 120 项合成资料分别执行直接处理和资料检索，两种模式下已标注关键事实丢失率均为 0。这些离线检查不调用模型。

## 复验

在已配置北邮环境变量的 Windows 主机上构建 EXE 后运行以下命令。第一条只向北邮提交合成资料，第二条使用独立的离线测试实例。

```powershell
python scripts/check_packaged_bupt.py dist/TokenFlow.exe
pwsh -NoProfile -File scripts/smoke_exe.ps1 -SkipOcrInference
```
