# ExpLine 开发日志

## 2026-05-01

1. 从需求文档中收敛出第一阶段最小可执行内容，并新增 `docs/ExpLine_MVP_开发清单.md`。
2. 搭建 Python CLI 项目骨架，新增 `pyproject.toml`、`expline/__init__.py`、`expline/__main__.py`、`expline/cli.py`、`expline/app.py`。
3. 实现 `python -m expline init`，可以初始化 `.expline/`、配置文件、Prompt 模板、项目摘要和索引文件。
4. 实现 `python -m expline run -- <command>`，可以执行实验命令并生成实验目录、`command.txt`、`diff.patch`、`changed_files.txt`、`record.json`、`record.md`。
5. 实现 `python -m expline show <experiment_id>`，可以查看实验记录。
6. 支持 Git 与非 Git 项目：Git 项目记录 commit、branch、diff 和变更文件；非 Git 项目退化为根实验记录。
7. 实现父实验推断：优先使用 `--parent`，否则按当前 Git commit 或祖先 commit 上最近实验推断。
8. 排除 `.expline/` 自身文件，避免工具生成的元数据污染实验 diff 和变更文件列表。
9. 新增 `.gitignore`，忽略 `__pycache__/` 和 `*.pyc`。
10. 新增 `expline/ai.py`，接入 AI 结构化输出层，支持 OpenAI Responses API，并在无 API Key 或调用失败时回退到本地摘要器。
11. 新增 `python -m expline rescan`，用于重新生成项目语义说明。
12. `init` 和 `rescan` 生成项目级语义说明：`project_summary.json`、`project_summary.ai.md`、`project_summary.md`。
13. `run` 生成实验语义报告时加入项目说明、父实验记录、当前命令、当前 diff、变更文件列表和文件片段。
14. 实验记录拆分为 AI 原稿和人工可编辑版本：`record.ai.md` 保存 AI 原稿，`record.md` 供用户修改。
15. 实现 `python -m expline edit <experiment_id>`，输出可编辑报告路径。

## 2026-05-03

1. 为项目摘要和实验报告增加报告语言参数，支持通过 `--report-language <language>` 临时指定生成语言。
2. 在 Prompt 模板中注入 `report_language`，让 AI 按指定语言生成项目说明和实验报告。
3. 在实验记录中新增 `report_language` 字段，并在 `record.md` 中显示本次报告语言。
4. 增加持久化配置命令：`python -m expline config set report-language <language>`。
5. 增加配置读取命令：`python -m expline config get report-language`。
6. `init`、`rescan`、`run` 默认读取 `.expline/config.json` 中的 `default_report_language`。
7. 保留 `--report-language <language>` 作为单次临时覆盖方式。
8. 将当前项目默认报告语言设置为 `Chinese`。
9. 验证不带 `--report-language` 的 `run` 会自动使用持久化语言配置。
10. 新增项目根目录 `README.md`，说明项目目的、项目目录结构和当前使用方法。
11. 补充“先安装 ExpLine，再到其他实验项目中使用 `expline init/run`”的跨项目使用流程。
12. 补充 GitHub 发布后的安装方式，包括 `pip install git+https://github.com/<your-name>/ExpLine.git`。
13. 更新 `.gitignore`，排除 `.expline/`、临时验证目录、构建产物和 egg-info 目录。
14. 清理公开版 README，删除本地开发路径、GitHub 发布步骤和维护者说明，只保留用户安装与使用方法。
15. 支持通过 `OPENAI_BASE_URL` 环境变量或 `expline config set openai-base-url <url>` 设置 OpenAI 兼容接口地址，并自动兼容 `/v1` 与 `/v1/responses` 两种写法。
16. 为 `expline init` 和 `expline rescan` 增加无依赖进度条，显示项目扫描、Prompt 准备、AI 摘要生成和文件写入阶段。
17. 为跨分支实验增加父实验 commit 对比：指定父实验时，优先生成父实验 commit 到当前 commit 的 diff，并追加当前工作区未提交 diff。
18. 为 `expline run` 增加 `--result-path`，支持在实验结束后记录结果文件或目录摘要，并提供给 AI 生成报告。
19. 强化实验报告 Prompt，要求 AI 优先分析会影响实验行为的关键代码、配置、参数和流程变化，降低文档或组织性修改对报告重点的干扰。
20. 调整变更文件片段排序，优先把 `src/`、入口脚本和配置文件提供给 AI，再处理文档类文件。
21. 新增 Focused code/config diff：从完整 diff 中优先抽取代码、入口脚本和配置文件的真实 diff hunks，要求 AI 基于具体代码变化解释实验设计结构变化。
22. 在实验报告 Prompt 中加入 Git diff comparison 元信息，明确跨分支父实验比较时 `-` 为父实验行为、`+` 为当前实验行为，并要求报告解释实验流程/设计从父实验到当前实验的结构性变化。

## 2026-05-04

1. 支持通过 `expline config set diff-max-chars <n>` 和 `expline config set focused-diff-max-chars <n>` 调整 diff 上下文预算。
2. Focused diff 改为按文件分配预算，并优先保留 `project_summary.md` 中 `Experiment-Sensitive Modules` 列出的核心文件，避免关键代码 diff 被入口脚本或长文档挤掉。
3. 文档类 diff 默认不进入 Focused diff，只保留在完整 diff 中作为辅助背景。

## 2026-05-05

1. 指定或推断出的父实验不存在时，`expline run` 会在运行用户命令前直接报错，避免生成孤立或错误链路。
2. 调整结果产物 Prompt 说明，明确 `--result-path` 只作为保存产物证据，不用于解释指标变化原因或判断实验好坏。
3. 新增 `expline rebuild`，可以从 `.expline/experiments/*/record.json` 重建 `.expline/index.json`，并报告重复实验 ID、缺失父实验和损坏记录等链路问题。
4. 新增 `expline list`，可以按时间倒序查看实验列表，并支持 `--limit`、`--branch`、`--parent` 过滤。

## 2026-05-06

1. 新增 `expline site`，可以生成 `.expline/site/index.html` 单文件静态实验谱系页面。
2. 静态页面内嵌实验链路、实验摘要和 `record.md` 内容，支持点击节点查看详情、搜索节点、高亮父子链路和右键复制路径。
3. `expline init`、`expline run`、`expline rebuild` 后会自动刷新静态页面。
4. README 增加远程服务器通过 `python -m http.server --bind 127.0.0.1` 和 VS Code Remote 端口转发查看静态页面的说明。
5. 修复 `expline list` 引入的截断函数重名问题，避免 `expline run` 在收集 Git diff 时崩溃。

## 2026-05-07

1. 优化静态页面交互：详情区中的 `record.md` 和 `Diff Preview` 改为内部滚动，避免长内容撑开整个页面。
2. 优化实验谱系图浏览：左侧图区域支持滚轮滚动和空白处拖拽平移，便于查看右侧或较远的实验节点。
3. README 新增可视化实验谱系页面截图，展示实验图和实验详情区域。
