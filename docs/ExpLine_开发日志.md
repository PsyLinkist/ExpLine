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
