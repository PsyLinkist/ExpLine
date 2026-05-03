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

## 安装

从 GitHub 安装：

```bash
python -m pip install git+https://github.com/PsyLinkist/ExpLine.git
```

安装后可以检查命令是否可用：

```bash
expline --help
```

如果想参与开发或修改源码，可以使用可编辑安装：

```bash
git clone https://github.com/PsyLinkist/ExpLine.git
cd ExpLine
python -m pip install -e .
```

## 快速开始

进入你的实验项目目录：

```bash
cd path/to/my-experiment-project
```

初始化 ExpLine：

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

ExpLine 会把实验记录保存在当前实验项目的 `.expline/` 目录中。

## 常用命令

### 初始化项目

```bash
expline init
```

不调用 AI：

```bash
expline init --no-ai
```

### 重新生成项目语义说明

```bash
expline rescan
```

不调用 AI：

```bash
expline rescan --no-ai
```

### 设置默认报告语言

```bash
expline config set report-language Chinese
```

查看当前默认报告语言：

```bash
expline config get report-language
```

### 运行并记录实验

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

## 运行数据目录

运行 `expline init` 后，当前实验项目下会生成 `.expline/`：

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

其中：

1. `.expline/project_summary.md` 是当前项目语义说明。
2. `.expline/experiments/EXP-xxxx/record.ai.md` 是 AI 生成的实验报告原稿。
3. `.expline/experiments/EXP-xxxx/record.md` 是用户可编辑的实验报告，也是 `show` 默认展示的版本。

## AI 配置

当环境变量 `OPENAI_API_KEY` 可用，并且 `.expline/config.json` 中的 `ai_backend` 为 `auto` 或 `openai` 时，ExpLine 会尝试使用 OpenAI Responses API 生成结构化报告。

PowerShell 示例：

```powershell
$env:OPENAI_API_KEY = "your-api-key"
expline run -- python main.py
```

如果没有 API Key 或 AI 调用失败，ExpLine 会自动回退到本地摘要器，保证记录流程仍然可用。
