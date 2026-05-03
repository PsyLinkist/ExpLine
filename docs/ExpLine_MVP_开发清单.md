# ExpLine MVP 开发清单

本文档对应当前仓库的第一阶段最小可执行内容，覆盖一条带 AI 语义生成与人工修订能力的最短价值闭环：

`expline init` -> AI 生成项目语义说明 -> `expline run -- <command>` -> AI 生成实验语义记录 -> 用户可编辑修订 -> `expline show <experiment_id>` 查看记录

## 1. 范围收敛

本阶段必须实现：

1. 项目初始化：生成 `.expline` 目录和默认文件。
2. 实验运行包装：执行用户命令并记录一次实验。
3. 实验节点存储：为每次运行生成唯一实验 ID 和独立目录。
4. Git 状态采集：尽可能记录 commit、branch、diff、变更文件。
5. 默认父实验推断：支持 `--parent` 覆盖，默认按当前 commit 或祖先 commit 推断。
6. AI 项目理解：在初始化或重扫时生成项目语义说明。
7. AI 语义记录：每次实验基于父实验记录和当前 diff 生成结构化报告。
8. 人工可编辑报告：用户可以直接修订实验记录，不被 AI 原稿覆盖。
9. 记录查看：支持按实验 ID 查看当前可编辑版本。

本阶段明确不做：

1. 静态站点可视化。
2. `rebuild` / `site` / `list` 等扩展命令。
3. 复杂的多提供商 AI 接入。
4. VS Code 插件、Web 服务、复杂前端。
5. 大型实验输出和完整日志管理。

## 2. 交付物

本阶段应新增以下交付物：

1. Python CLI 项目骨架。
2. `expline` 命令入口。
3. `.expline` 目录结构初始化逻辑。
4. 实验记录 JSON / Markdown 写入逻辑。
5. OpenAI Responses API 结构化输出接入。
6. 本地回退语义摘要器。
7. 可编辑 Markdown 报告机制。
8. 基本使用说明和本地验证结果。

## 3. 实现拆分

### 3.1 CLI 与项目骨架

验收点：

1. 可以通过 `python -m expline` 运行。
2. 命令行包含 `init`、`run`、`show` 三个子命令。
3. 参数解析支持 `expline run -- <command>` 和 `expline run --parent EXP-0001 -- <command>`。

### 3.2 初始化流程

验收点：

1. 执行 `expline init` 后生成 `.expline/`。
2. 生成默认 `config.json`。
3. 生成默认 Prompt 模板。
4. 生成 `project_summary.ai.md` 和 `project_summary.md`。
5. 生成 `project_summary.json`。
6. 生成实验目录和索引文件。

### 3.3 AI 项目语义说明

验收点：

1. `expline init` 默认尝试使用 AI 生成项目语义说明。
2. 无 API Key 或调用失败时自动回退到本地摘要器。
3. 生成的项目说明既有结构化 JSON，也有可读 Markdown。
4. `expline rescan` 可以重新生成项目说明。

### 3.4 单次实验记录流程

验收点：

1. `expline run -- <command>` 会真正执行用户命令。
2. 每次运行生成新实验目录，例如 `EXP-0001`。
3. 保存 `command.txt`、`diff.patch`、`changed_files.txt`、`record.json`、`record.ai.md`、`record.md`。
4. 实验记录包含父实验 ID、Git 信息、时间戳。
5. 保存 AI 提示词和原始响应工件。

### 3.5 父实验推断

验收点：

1. 指定 `--parent` 时直接采用指定值。
2. 当前 commit 已有历史实验时，使用该 commit 最近一次实验。
3. 当前 commit 没有实验时，向祖先 commit 查找最近实验。
4. 非 Git 仓库时默认生成根实验。

### 3.6 AI 语义实验报告

验收点：

1. 默认尝试使用 AI 生成结构化实验报告。
2. 输入至少包含项目语义说明、父实验报告、当前命令、当前 diff、变更文件列表。
3. 输出至少包含标题、摘要、变更说明、变更类型、受影响文件、相对父实验语义差异。
4. 即使没有真实 AI，也能稳定回退到本地摘要器。
5. 支持通过 `expline config set report-language <language>` 持久设置默认报告语言。
6. 支持通过 `--report-language <language>` 对单次命令临时覆盖默认语言。

### 3.7 人工可编辑报告

验收点：

1. AI 原稿保存为 `record.ai.md`。
2. 用户可编辑版本保存为 `record.md`。
3. `show` 默认展示 `record.md`。
4. 后续重建或展示流程不覆盖用户手工修订。

### 3.8 记录查看

验收点：

1. `expline show EXP-0001` 可以展示记录。
2. 至少能输出 `record.md` 内容和对应目录位置。
3. `expline edit EXP-0001` 能打印可编辑文件路径。

## 4. 执行顺序

1. 写本文档。
2. 搭建 Python CLI 项目骨架。
3. 实现 `init` 和 `rescan`。
4. 实现 OpenAI 结构化输出接入与本地回退。
5. 实现 `run` 的 AI 实验报告生成。
6. 实现 `show` 和 `edit`。
7. 本地运行一轮初始化、实验记录和人工修订验证。

## 5. 完成定义

以下条件全部满足即视为本阶段完成：

1. 新仓库下可执行 `python -m expline init`。
2. 可执行 `python -m expline rescan`。
3. 可执行 `python -m expline run -- python -c "print('hello')"`。
4. `.expline/` 下生成项目语义说明的 JSON、AI 原稿和当前可读版本。
5. `.expline/experiments/` 中生成实验目录与记录文件。
6. `python -m expline show <experiment_id>` 可读出当前可编辑版本。
7. 用户可直接修改 `record.md`，且 `show` 会展示修订结果。
8. 文档与代码都保存在仓库中，后续可继续扩展到 `list`、`rebuild`、`site`。
