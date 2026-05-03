# ExpLine

ExpLine 是一个面向科研代码实验的轻量级实验记录工具。

它的目标是减少科研工作者手写实验记录的负担。用户通过 ExpLine 包装原本的实验命令后，系统会记录当前命令、Git 状态、工作区 diff、变更文件，并结合父实验记录生成一份可读的语义实验报告。

ExpLine 不负责判断实验结果好坏，也不自动给出科研结论。它关注的是：本次实验相对父实验，在代码、配置、命令和方法语义上发生了什么变化。

## 项目目的

ExpLine 适合频繁修改代码并运行实验的科研人员、算法工程师和学生。

当前版本支持：

1. 初始化项目内实验记录目录。
2. 生成项目级语义说明。
3. 使用 `expline run` 包装并执行实验命令。
4. 记录命令、Git commit、Git branch、工作区 diff 和变更文件。
5. 根据 Git commit 推断父实验，也支持手动指定父实验。
6. 使用 AI 生成实验语义报告。
7. 保留可编辑的 `record.md`，方便研究者修正 AI 生成的实验记录。
8. 持久化设置实验报告语言。

## 项目目录结构

```text
ExpLine/
|-- expline/
|   |-- __init__.py
|   |-- __main__.py
|   |-- ai.py
|   |-- app.py
|   `-- cli.py
|-- docs/
|   |-- ExpLine_MVP_开发清单.md
|   |-- ExpLine_开发日志.md
|   `-- ExpLine_需求文档_v0.3.md
|-- .gitignore
|-- pyproject.toml
`-- README.md
```

运行 `expline init` 后，会生成 `.expline/` 运行数据目录：

```text
.expline/
|-- config.json
|-- index.json
|-- project_summary.json
|-- project_summary.ai.md
|-- project_summary.md
|-- prompts/
|   |-- project_summary_prompt.txt
|   `-- experiment_record_prompt.txt
`-- experiments/
    `-- EXP-0001/
        |-- ai_prompt.txt
        |-- ai_raw.json
        |-- changed_files.txt
        |-- command.txt
        |-- diff.patch
        |-- record.ai.md
        |-- record.json
        `-- record.md
```

关键文件说明：

1. `expline/app.py`：CLI 命令和实验记录主流程。
2. `expline/ai.py`：AI 结构化输出接入和本地回退逻辑。
3. `.expline/project_summary.md`：当前项目语义说明。
4. `.expline/experiments/EXP-xxxx/record.ai.md`：AI 生成的实验报告原稿。
5. `.expline/experiments/EXP-xxxx/record.md`：用户可编辑的实验报告，也是 `show` 默认展示的版本。

## 项目使用方法

### 安装 ExpLine

先在 ExpLine 工具仓库中安装一次：

```bash
cd D:\Projects\Python\ExpLine
python -m pip install -e .
```

安装后，系统会获得 `expline` 命令。之后你可以切换到任意实验项目中使用 ExpLine。

如果 Windows 上遇到临时目录权限问题，可以改用：

```powershell
$env:TMP = "$pwd\.tmp"
$env:TEMP = "$pwd\.tmp"
New-Item -ItemType Directory -Force .tmp
python -m pip install -e . --no-build-isolation
```

如果不想安装，也可以在 ExpLine 仓库内部用 `python -m expline ...` 进行开发和调试；但在其他项目中使用时，推荐先安装成 `expline` 命令。

### 从 GitHub 安装

项目发布到 GitHub 后，其他用户不需要下载这个本地目录，可以直接从 GitHub 安装：

```bash
python -m pip install git+https://github.com/<your-name>/ExpLine.git
```

如果用户想参与开发或修改源码，可以使用可编辑安装：

```bash
git clone https://github.com/<your-name>/ExpLine.git
cd ExpLine
python -m pip install -e .
```

安装完成后，在任意实验项目中都可以使用：

```bash
expline --help
```

### 发布到 GitHub 的基本流程

第一次发布时，在 ExpLine 工具仓库中执行：

```bash
git init
git add .
git commit -m "Initial ExpLine MVP"
git branch -M main
git remote add origin https://github.com/<your-name>/ExpLine.git
git push -u origin main
```

推送前建议确认不要提交本地运行数据，例如 `.expline/`、`tmp_git_verify*/`、`__pycache__/`、`*.egg-info/`、`build/`、`dist/`。这些已经写入当前仓库的 `.gitignore`。

之后用户侧的典型使用流程是：

```bash
python -m pip install git+https://github.com/<your-name>/ExpLine.git
cd path/to/my-experiment-project
expline init
expline run -- python main.py --config config.yaml
```

如果后续发布到 PyPI，用户就可以进一步简化为：

```bash
python -m pip install expline
```

### 在其他实验项目中使用

假设你的实验项目目录是：

```bash
cd D:\Projects\Research\MyExperiment
```

第一次使用时初始化：

```bash
expline init
```

设置默认报告语言：

```bash
expline config set report-language Chinese
```

原来这样运行实验：

```bash
python main.py --config config.yaml
```

现在改成：

```bash
expline run -- python main.py --config config.yaml
```

ExpLine 会把实验记录保存在当前实验项目的 `.expline/` 目录中，而不是保存在 ExpLine 工具仓库里。

下面的命令都默认在“正在做实验的项目根目录”执行。

### 初始化项目

```bash
expline init
```

该命令会创建 `.expline/`、默认配置、Prompt 模板、项目语义说明和实验索引。

如果不希望调用 AI：

```bash
expline init --no-ai
```

### 重新生成项目语义说明

```bash
expline rescan
```

如果不希望调用 AI：

```bash
expline rescan --no-ai
```

### 设置默认报告语言

一次性设置默认报告语言：

```bash
expline config set report-language Chinese
```

查看当前默认报告语言：

```bash
expline config get report-language
```

该配置会写入 `.expline/config.json` 的 `default_report_language` 字段。

### 运行并记录实验

将原始实验命令放在 `expline run --` 后面：

```bash
expline run -- python main.py --config config.yaml
```

不调用 AI，使用本地回退摘要器：

```bash
expline run --no-ai -- python main.py --config config.yaml
```

只为本次实验临时指定报告语言：

```bash
expline run --report-language English -- python main.py
```

手动指定父实验：

```bash
expline run --parent EXP-0001 -- python main.py
```

每次运行都会在 `.expline/experiments/` 下创建一个新的实验目录。

### 查看实验报告

```bash
expline show EXP-0001
```

`show` 默认读取可编辑的 `record.md`，而不是只读取 AI 原稿。

### 编辑实验报告

```bash
expline edit EXP-0001
```

该命令会输出对应实验的 `record.md` 路径。研究者可以直接编辑该文件，修正或补充 AI 生成的语义实验记录。

## AI 配置

当环境变量 `OPENAI_API_KEY` 可用，并且 `.expline/config.json` 中的 `ai_backend` 为 `auto` 或 `openai` 时，ExpLine 会尝试使用 OpenAI Responses API 生成结构化报告。

PowerShell 示例：

```powershell
$env:OPENAI_API_KEY = "your-api-key"
expline run -- python main.py
```

如果没有 API Key 或 AI 调用失败，ExpLine 会自动回退到本地摘要器，保证记录流程仍然可用。
