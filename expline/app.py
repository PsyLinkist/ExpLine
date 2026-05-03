from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from expline.ai import AIResult, generate_structured_output


APP_DIRNAME = ".expline"
EXPERIMENT_PREFIX = "EXP-"
DEFAULT_CONFIG = {
    "version": 2,
    "ai_backend": "auto",
    "default_report_language": "English",
    "openai_model": "gpt-5.4-mini",
    "openai_base_url": "https://api.openai.com/v1/responses",
    "openai_timeout_seconds": 60,
    "save_prompt_artifacts": True,
    "project_context_max_files": 40,
    "project_context_total_chars": 24000,
    "project_file_snippet_chars": 1600,
    "diff_max_chars": 18000,
    "changed_file_snippet_chars": 2200,
}
DEFAULT_PROJECT_PROMPT_TEMPLATE = """Generate a project summary for ExpLine.

The goal is to help future experiment reports understand the codebase well enough to describe experiment changes.

Write the summary in this language:
{{ report_language }}

Project tree:
{{ project_tree }}

Selected file snippets:
{{ project_snippets }}

Return a compact, technically precise summary of:
- project_goal
- main_entrypoints
- main_run_commands
- core_modules
- config_files
- experiment_scripts
- output_locations
- workflow_overview
- experiment_sensitive_modules
- experiment_sensitive_configs
- notes
"""
DEFAULT_RECORD_PROMPT_TEMPLATE = """Generate a semantic experiment report for ExpLine.

Important constraints:
- Focus on what changed, not whether the experiment result is good or bad.
- Compare against the parent experiment when available.
- Use the current diff versus the latest Git commit to understand code and config changes.
- Produce readable, concise language for a human researcher.
- Write the report in this language: {{ report_language }}

Project summary:
{{ project_summary }}

Parent experiment report:
{{ parent_report }}

Current command:
{{ command }}

Current Git commit:
{{ git_commit }}

Current Git branch:
{{ git_branch }}

Changed files:
{{ changed_files }}

Current diff:
{{ diff_text }}

Changed file snippets:
{{ changed_file_snippets }}

Return:
- title
- summary
- change_description
- change_types
- affected_files
- affected_stages
- semantic_diff_from_parent.before
- semantic_diff_from_parent.after
- evidence_index
- review_hints
"""
PROJECT_SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "project_goal": {"type": "string"},
        "main_entrypoints": {"type": "array", "items": {"type": "string"}},
        "main_run_commands": {"type": "array", "items": {"type": "string"}},
        "core_modules": {"type": "array", "items": {"type": "string"}},
        "config_files": {"type": "array", "items": {"type": "string"}},
        "experiment_scripts": {"type": "array", "items": {"type": "string"}},
        "output_locations": {"type": "array", "items": {"type": "string"}},
        "workflow_overview": {"type": "array", "items": {"type": "string"}},
        "experiment_sensitive_modules": {"type": "array", "items": {"type": "string"}},
        "experiment_sensitive_configs": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "project_goal",
        "main_entrypoints",
        "main_run_commands",
        "core_modules",
        "config_files",
        "experiment_scripts",
        "output_locations",
        "workflow_overview",
        "experiment_sensitive_modules",
        "experiment_sensitive_configs",
        "notes",
    ],
}
EXPERIMENT_REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "change_description": {"type": "string"},
        "change_types": {"type": "array", "items": {"type": "string"}},
        "affected_files": {"type": "array", "items": {"type": "string"}},
        "affected_stages": {"type": "array", "items": {"type": "string"}},
        "semantic_diff_from_parent": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "before": {"type": "string"},
                "after": {"type": "string"},
            },
            "required": ["before", "after"],
        },
        "evidence_index": {"type": "array", "items": {"type": "string"}},
        "review_hints": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title",
        "summary",
        "change_description",
        "change_types",
        "affected_files",
        "affected_stages",
        "semantic_diff_from_parent",
        "evidence_index",
        "review_hints",
    ],
}
TEXT_FILE_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".ps1",
    ".bat",
    ".csv",
}
IGNORED_DIRS = {".git", ".expline", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv", "node_modules"}


@dataclass
class GitSnapshot:
    is_repo: bool
    commit: str | None
    branch: str | None
    dirty: bool
    diff: str
    changed_files: list[str]


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def configure_stdout() -> None:
    stream = getattr(sys.stdout, "reconfigure", None)
    if callable(stream):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="expline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize ExpLine in the current project")
    init_parser.add_argument("--no-ai", action="store_true", help="Skip live AI calls and use the local fallback summarizer")
    init_parser.add_argument("--report-language", help="Language to use for the generated project summary, for example English or Chinese")
    init_parser.set_defaults(func=cmd_init)

    rescan_parser = subparsers.add_parser("rescan", help="Regenerate the project semantic summary")
    rescan_parser.add_argument("--no-ai", action="store_true", help="Skip live AI calls and use the local fallback summarizer")
    rescan_parser.add_argument("--report-language", help="Language to use for the generated project summary, for example English or Chinese")
    rescan_parser.set_defaults(func=cmd_rescan)

    run_parser = subparsers.add_parser("run", help="Run and record an experiment")
    run_parser.add_argument("--parent", dest="parent_id", help="Explicit parent experiment ID")
    run_parser.add_argument("--no-ai", action="store_true", help="Skip live AI calls and use the local fallback summarizer")
    run_parser.add_argument("--report-language", help="Language to use for this experiment report, for example English or Chinese")
    run_parser.add_argument("command_parts", nargs=argparse.REMAINDER, help="Command to execute after --")
    run_parser.set_defaults(func=cmd_run)

    show_parser = subparsers.add_parser("show", help="Show a recorded experiment")
    show_parser.add_argument("experiment_id", help="Experiment ID like EXP-0001")
    show_parser.set_defaults(func=cmd_show)

    edit_parser = subparsers.add_parser("edit", help="Print the editable report path for an experiment")
    edit_parser.add_argument("experiment_id", help="Experiment ID like EXP-0001")
    edit_parser.set_defaults(func=cmd_edit)

    config_parser = subparsers.add_parser("config", help="Read or update ExpLine project settings")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    config_get_parser = config_subparsers.add_parser("get", help="Read one config value")
    config_get_parser.add_argument("key", help="Config key, for example report-language")
    config_get_parser.set_defaults(func=cmd_config_get)

    config_set_parser = config_subparsers.add_parser("set", help="Update one config value")
    config_set_parser.add_argument("key", help="Config key, for example report-language")
    config_set_parser.add_argument("value", help="Value to store")
    config_set_parser.set_defaults(func=cmd_config_set)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    root = Path.cwd()
    ensure_layout(root)
    config = ensure_config(root)
    ensure_default_text(project_prompt_path(root), DEFAULT_PROJECT_PROMPT_TEMPLATE)
    ensure_default_text(record_prompt_path(root), DEFAULT_RECORD_PROMPT_TEMPLATE)
    initialize_index(root)
    report_language = resolve_report_language(args, config)
    result = regenerate_project_summary(root, config, use_ai=not args.no_ai, report_language=report_language)
    print(f"Initialized ExpLine in {app_path(root)}")
    print_project_summary_status(result)
    return 0


def cmd_rescan(args: argparse.Namespace) -> int:
    root = Path.cwd()
    assert_initialized(root)
    config = ensure_config(root)
    report_language = resolve_report_language(args, config)
    result = regenerate_project_summary(root, config, use_ai=not args.no_ai, report_language=report_language)
    print_project_summary_status(result)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    root = Path.cwd()
    assert_initialized(root)
    config = ensure_config(root)
    report_language = resolve_report_language(args, config)
    command_parts = normalize_command_parts(args.command_parts)
    if not command_parts:
        raise SystemExit("No command provided. Use: expline run -- <command>")

    git_snapshot = collect_git_snapshot(root, config)
    index = load_index(root)
    parent_id = args.parent_id or infer_parent_experiment(root, index, git_snapshot)
    experiment_id = next_experiment_id(index)
    experiment_dir = experiments_path(root) / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=False)

    started_at = now_iso()
    command_text = format_command(command_parts)
    command_result = run_user_command(command_parts, root)
    parent_record = load_parent_record(root, parent_id)
    semantic_result = generate_experiment_report(
        root=root,
        config=config,
        git_snapshot=git_snapshot,
        parent_id=parent_id,
        parent_record=parent_record,
        command_text=command_text,
        experiment_dir=experiment_dir,
        use_ai=not args.no_ai,
        report_language=report_language,
    )

    record = {
        "record_version": 2,
        "experiment_id": experiment_id,
        "parent_id": parent_id,
        "title": semantic_result.output["title"],
        "summary": semantic_result.output["summary"],
        "change_description": semantic_result.output["change_description"],
        "command": command_text,
        "command_exit_code": command_result.returncode,
        "git_commit": git_snapshot.commit,
        "git_branch": git_snapshot.branch,
        "git_dirty": git_snapshot.dirty,
        "change_types": semantic_result.output["change_types"],
        "affected_files": semantic_result.output["affected_files"],
        "affected_stages": semantic_result.output["affected_stages"],
        "semantic_diff_from_parent": semantic_result.output["semantic_diff_from_parent"],
        "evidence_index": semantic_result.output["evidence_index"],
        "review_hints": semantic_result.output["review_hints"],
        "created_at": started_at,
        "report_language": report_language,
        "report_backend": semantic_result.backend,
        "report_model": semantic_result.model,
        "report_error": semantic_result.error,
        "editable_report_path": "record.md",
        "ai_report_path": "record.ai.md",
    }

    write_experiment_files(
        experiment_dir=experiment_dir,
        command_text=command_text,
        git_snapshot=git_snapshot,
        record=record,
        semantic_result=semantic_result,
        config=config,
    )
    update_index(index, record)
    save_index(root, index)

    print(f"Recorded experiment {experiment_id} in {experiment_dir}")
    print(f"Editable report: {experiment_dir / 'record.md'}")
    if semantic_result.error:
        print(f"AI note: {semantic_result.error}")
    if command_result.returncode != 0:
        print(f"Wrapped command exited with code {command_result.returncode}", file=sys.stderr)
    return command_result.returncode


def cmd_show(args: argparse.Namespace) -> int:
    root = Path.cwd()
    assert_initialized(root)
    experiment_dir = experiments_path(root) / args.experiment_id
    record_path = editable_record_path(experiment_dir)
    if not record_path.exists():
        raise SystemExit(f"Experiment not found: {args.experiment_id}")
    print(f"# {args.experiment_id}")
    print(f"directory: {experiment_dir}")
    print(f"editable_report: {record_path}")
    print()
    print(record_path.read_text(encoding="utf-8"))
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    root = Path.cwd()
    assert_initialized(root)
    experiment_dir = experiments_path(root) / args.experiment_id
    record_path = editable_record_path(experiment_dir)
    if not record_path.exists():
        raise SystemExit(f"Experiment not found: {args.experiment_id}")
    print(record_path)
    return 0


def cmd_config_get(args: argparse.Namespace) -> int:
    root = Path.cwd()
    ensure_layout(root)
    config = ensure_config(root)
    key = normalize_config_key(args.key)
    print(config[key])
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    root = Path.cwd()
    ensure_layout(root)
    config = ensure_config(root)
    key = normalize_config_key(args.key)
    config[key] = args.value.strip() if isinstance(args.value, str) else args.value
    write_json(config_path(root), config)
    print(f"{args.key} = {config[key]}")
    return 0


def app_path(root: Path) -> Path:
    return root / APP_DIRNAME


def experiments_path(root: Path) -> Path:
    return app_path(root) / "experiments"


def prompts_dir(root: Path) -> Path:
    return app_path(root) / "prompts"


def project_prompt_path(root: Path) -> Path:
    return prompts_dir(root) / "project_summary_prompt.txt"


def record_prompt_path(root: Path) -> Path:
    return prompts_dir(root) / "experiment_record_prompt.txt"


def project_summary_md_path(root: Path) -> Path:
    return app_path(root) / "project_summary.md"


def project_summary_ai_md_path(root: Path) -> Path:
    return app_path(root) / "project_summary.ai.md"


def project_summary_json_path(root: Path) -> Path:
    return app_path(root) / "project_summary.json"


def project_summary_prompt_artifact_path(root: Path) -> Path:
    return app_path(root) / "project_summary.prompt.txt"


def project_summary_raw_path(root: Path) -> Path:
    return app_path(root) / "project_summary.raw.json"


def config_path(root: Path) -> Path:
    return app_path(root) / "config.json"


def index_path(root: Path) -> Path:
    return app_path(root) / "index.json"


def editable_record_path(experiment_dir: Path) -> Path:
    return experiment_dir / "record.md"


def ai_record_path(experiment_dir: Path) -> Path:
    return experiment_dir / "record.ai.md"


def ai_prompt_path(experiment_dir: Path) -> Path:
    return experiment_dir / "ai_prompt.txt"


def ai_raw_path(experiment_dir: Path) -> Path:
    return experiment_dir / "ai_raw.json"


def ensure_layout(root: Path) -> None:
    app_path(root).mkdir(exist_ok=True)
    experiments_path(root).mkdir(parents=True, exist_ok=True)
    prompts_dir(root).mkdir(parents=True, exist_ok=True)


def ensure_config(root: Path) -> dict[str, Any]:
    if not config_path(root).exists():
        write_json(config_path(root), DEFAULT_CONFIG)
    data = json.loads(config_path(root).read_text(encoding="utf-8"))
    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in data:
            data[key] = value
            changed = True
    if changed:
        write_json(config_path(root), data)
    return data


def initialize_index(root: Path) -> None:
    if not index_path(root).exists():
        write_json(index_path(root), {"next_id": 1, "experiments": [], "commit_index": {}})


def assert_initialized(root: Path) -> None:
    if not app_path(root).exists():
        raise SystemExit("ExpLine is not initialized in this directory. Run: expline init")
    initialize_index(root)


def ensure_default_text(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_report_language(args: argparse.Namespace, config: dict[str, Any]) -> str:
    requested = getattr(args, "report_language", None)
    if requested and requested.strip():
        return requested.strip()
    configured = str(config.get("default_report_language", "English")).strip()
    return configured or "English"


def normalize_config_key(key: str) -> str:
    normalized = key.strip().lower().replace("_", "-")
    aliases = {
        "report-language": "default_report_language",
        "default-report-language": "default_report_language",
        "language": "default_report_language",
        "openai-base-url": "openai_base_url",
        "base-url": "openai_base_url",
        "openai-model": "openai_model",
        "model": "openai_model",
    }
    if normalized not in aliases:
        allowed = ", ".join(sorted(aliases))
        raise SystemExit(f"Unsupported config key: {key}. Supported keys: {allowed}")
    return aliases[normalized]


def regenerate_project_summary(root: Path, config: dict[str, Any], use_ai: bool, report_language: str) -> AIResult:
    project_context = build_project_context(root, config)
    fallback_output = fallback_project_summary(project_context, report_language)
    prompt_template = project_prompt_path(root).read_text(encoding="utf-8")
    result = generate_structured_output(
        task_name="project_summary",
        prompt_template=prompt_template,
        context={
            "report_language": report_language,
            "project_tree": project_context["project_tree"],
            "project_snippets": project_context["project_snippets"],
        },
        schema=PROJECT_SUMMARY_SCHEMA,
        fallback_output=fallback_output,
        config={**config, "ai_backend": config.get("ai_backend", "auto") if use_ai else "stub"},
    )
    markdown = render_project_summary_markdown(result.output)
    project_summary_json_path(root).write_text(json.dumps(result.output, ensure_ascii=False, indent=2), encoding="utf-8")
    project_summary_ai_md_path(root).write_text(markdown, encoding="utf-8")
    project_summary_md_path(root).write_text(markdown, encoding="utf-8")
    if config.get("save_prompt_artifacts", True):
        project_summary_prompt_artifact_path(root).write_text(result.rendered_prompt, encoding="utf-8")
        if result.raw_response is not None:
            project_summary_raw_path(root).write_text(json.dumps(result.raw_response, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def build_project_context(root: Path, config: dict[str, Any]) -> dict[str, str]:
    max_files = int(config.get("project_context_max_files", 40))
    total_chars_limit = int(config.get("project_context_total_chars", 24000))
    snippet_chars = int(config.get("project_file_snippet_chars", 1600))
    files = list(iter_project_files(root))
    tree_lines = [path.as_posix() for path in files[: max_files * 4]]
    snippets: list[str] = []
    total_chars = 0
    selected = 0
    for rel_path in files:
        if selected >= max_files:
            break
        content = safe_read_text(root / rel_path)
        if content is None:
            continue
        excerpt = content[:snippet_chars].strip()
        if not excerpt:
            continue
        snippet = f"### {rel_path.as_posix()}\n{excerpt}\n"
        if total_chars + len(snippet) > total_chars_limit:
            break
        snippets.append(snippet)
        total_chars += len(snippet)
        selected += 1
    return {
        "project_tree": "\n".join(tree_lines) if tree_lines else "(empty project)",
        "project_snippets": "\n".join(snippets) if snippets else "(no readable files found)",
    }


def iter_project_files(root: Path) -> list[Path]:
    results: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root)
        if should_ignore_path(rel_path):
            continue
        if path.suffix.lower() in TEXT_FILE_SUFFIXES or path.name.lower() in {"pyproject.toml", "readme.md", "makefile"}:
            results.append(rel_path)
    return results


def should_ignore_path(rel_path: Path) -> bool:
    if rel_path.parts and rel_path.parts[0].startswith("tmp_git_verify"):
        return True
    parts = set(rel_path.parts)
    return any(part in IGNORED_DIRS for part in parts)


def safe_read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def fallback_project_summary(project_context: dict[str, str], report_language: str) -> dict[str, Any]:
    tree_lines = [line.strip() for line in project_context["project_tree"].splitlines() if line.strip()]
    python_files = [line for line in tree_lines if line.endswith(".py")]
    config_files = [line for line in tree_lines if line.endswith((".yaml", ".yml", ".json", ".toml", ".ini"))]
    docs = [line for line in tree_lines if line.endswith(".md")]
    if is_chinese(report_language):
        project_goal = "为当前仓库生成可读性高的 AI 辅助实验记录。"
        workflow_overview = [
            "初始化 ExpLine 元数据和提示词模板。",
            "通过 expline run 包装实验命令。",
            "使用生成的语义摘要持续追踪实验变化。",
        ]
        notes = [
            f"扫描到的可读文件数量：{len(tree_lines)}",
            f"扫描到的文档文件数量：{len(docs)}",
        ]
    else:
        project_goal = "Maintain readable AI-assisted experiment records for this repository."
        workflow_overview = [
            "Initialize ExpLine metadata and prompt templates.",
            "Run experiments through expline run.",
            "Use generated summaries to track semantic changes over time.",
        ]
        notes = [
            f"Top-level readable files discovered: {len(tree_lines)}",
            f"Documentation files discovered: {len(docs)}",
        ]
    return {
        "project_goal": project_goal,
        "main_entrypoints": python_files[:5],
        "main_run_commands": ["python -m expline init", "python -m expline run -- <command>"],
        "core_modules": python_files[:10],
        "config_files": config_files[:10],
        "experiment_scripts": python_files[:10],
        "output_locations": [".expline/experiments/"],
        "workflow_overview": workflow_overview,
        "experiment_sensitive_modules": python_files[:10],
        "experiment_sensitive_configs": config_files[:10],
        "notes": notes,
    }


def render_project_summary_markdown(summary: dict[str, Any]) -> str:
    sections = [
        ("Project Goal", summary["project_goal"]),
        ("Main Entrypoints", summary["main_entrypoints"]),
        ("Main Run Commands", summary["main_run_commands"]),
        ("Core Modules", summary["core_modules"]),
        ("Config Files", summary["config_files"]),
        ("Experiment Scripts", summary["experiment_scripts"]),
        ("Output Locations", summary["output_locations"]),
        ("Workflow Overview", summary["workflow_overview"]),
        ("Experiment-Sensitive Modules", summary["experiment_sensitive_modules"]),
        ("Experiment-Sensitive Configs", summary["experiment_sensitive_configs"]),
        ("Notes", summary["notes"]),
    ]
    lines = ["# Project Summary", ""]
    for title, value in sections:
        lines.append(f"## {title}")
        lines.append("")
        if isinstance(value, list):
            if value:
                lines.extend(f"- {item}" for item in value)
            else:
                lines.append("- (none)")
        else:
            lines.append(str(value))
        lines.append("")
    return "\n".join(lines)


def print_project_summary_status(result: AIResult) -> None:
    backend_label = result.backend
    if result.model:
        backend_label = f"{backend_label} ({result.model})"
    print(f"Project summary updated via {backend_label}.")
    if result.error:
        print(f"AI note: {result.error}")


def normalize_command_parts(parts: list[str]) -> list[str]:
    if parts and parts[0] == "--":
        return parts[1:]
    return parts


def format_command(command_parts: list[str]) -> str:
    if sys.platform.startswith("win"):
        return subprocess.list2cmdline(command_parts)
    return " ".join(command_parts)


def run_user_command(command_parts: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command_parts, cwd=root, text=True, check=False)


def collect_git_snapshot(root: Path, config: dict[str, Any]) -> GitSnapshot:
    if run_git(root, ["rev-parse", "--is-inside-work-tree"]).returncode != 0:
        return GitSnapshot(False, None, None, False, "", [])

    diff_max_chars = int(config.get("diff_max_chars", 18000))
    commit = git_output(root, ["rev-parse", "HEAD"])
    branch = git_output(root, ["branch", "--show-current"])
    diff = git_output(root, ["diff", "--no-ext-diff", "--", ".", ":(exclude).expline"])
    if len(diff) > diff_max_chars:
        diff = diff[:diff_max_chars] + "\n\n[diff truncated by ExpLine]\n"
    status_lines = git_output(root, ["status", "--porcelain"]).splitlines()
    changed_files = sorted(
        {
            path
            for line in status_lines
            if len(line) > 3
            for path in [normalize_status_path(line[3:])]
            if path and not is_internal_path(path)
        }
    )
    return GitSnapshot(True, commit or None, branch or None, bool(changed_files), diff, changed_files)


def run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)


def git_output(root: Path, args: list[str]) -> str:
    result = run_git(root, args)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def normalize_status_path(raw_path: str) -> str:
    if " -> " in raw_path:
        return raw_path.split(" -> ", 1)[1].strip()
    return raw_path.strip()


def is_internal_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized == ".expline" or normalized.startswith(".expline/")


def load_index(root: Path) -> dict[str, Any]:
    return json.loads(index_path(root).read_text(encoding="utf-8"))


def save_index(root: Path, index: dict[str, Any]) -> None:
    write_json(index_path(root), index)


def next_experiment_id(index: dict[str, Any]) -> str:
    next_id = index["next_id"]
    index["next_id"] = next_id + 1
    return f"{EXPERIMENT_PREFIX}{next_id:04d}"


def infer_parent_experiment(root: Path, index: dict[str, Any], git_snapshot: GitSnapshot) -> str | None:
    if not git_snapshot.is_repo or not git_snapshot.commit:
        return None

    commit_index = index.get("commit_index", {})
    if git_snapshot.commit in commit_index and commit_index[git_snapshot.commit]:
        return commit_index[git_snapshot.commit][-1]

    ancestor_result = run_git(root, ["rev-list", "HEAD"])
    if ancestor_result.returncode != 0:
        return None
    for commit in ancestor_result.stdout.splitlines():
        experiment_ids = commit_index.get(commit, [])
        if experiment_ids:
            return experiment_ids[-1]
    return None


def load_parent_record(root: Path, parent_id: str | None) -> dict[str, Any] | None:
    if not parent_id:
        return None
    record_json_path = experiments_path(root) / parent_id / "record.json"
    if not record_json_path.exists():
        return None
    payload = json.loads(record_json_path.read_text(encoding="utf-8"))
    parent_dir = record_json_path.parent
    editable_report = editable_record_path(parent_dir)
    payload["editable_markdown"] = editable_report.read_text(encoding="utf-8") if editable_report.exists() else ""
    return payload


def generate_experiment_report(
    *,
    root: Path,
    config: dict[str, Any],
    git_snapshot: GitSnapshot,
    parent_id: str | None,
    parent_record: dict[str, Any] | None,
    command_text: str,
    experiment_dir: Path,
    use_ai: bool,
    report_language: str,
) -> AIResult:
    project_summary = project_summary_md_path(root).read_text(encoding="utf-8") if project_summary_md_path(root).exists() else "(project summary missing)"
    changed_file_snippets = build_changed_file_snippets(root, git_snapshot.changed_files, config)
    parent_report_text = parent_record["editable_markdown"] if parent_record and parent_record.get("editable_markdown") else "(no parent experiment)"
    fallback_output = fallback_experiment_report(
        command_text=command_text,
        git_snapshot=git_snapshot,
        parent_id=parent_id,
        parent_record=parent_record,
        report_language=report_language,
    )
    prompt_template = record_prompt_path(root).read_text(encoding="utf-8")
    result = generate_structured_output(
        task_name="experiment_report",
        prompt_template=prompt_template,
        context={
            "report_language": report_language,
            "project_summary": project_summary,
            "parent_report": parent_report_text,
            "command": command_text,
            "git_commit": git_snapshot.commit or "N/A",
            "git_branch": git_snapshot.branch or "N/A",
            "changed_files": "\n".join(git_snapshot.changed_files) if git_snapshot.changed_files else "(none)",
            "diff_text": git_snapshot.diff or "(no diff available)",
            "changed_file_snippets": changed_file_snippets,
        },
        schema=EXPERIMENT_REPORT_SCHEMA,
        fallback_output=fallback_output,
        config={**config, "ai_backend": config.get("ai_backend", "auto") if use_ai else "stub"},
    )
    if config.get("save_prompt_artifacts", True):
        ai_prompt_path(experiment_dir).write_text(result.rendered_prompt, encoding="utf-8")
        if result.raw_response is not None:
            ai_raw_path(experiment_dir).write_text(json.dumps(result.raw_response, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def build_changed_file_snippets(root: Path, changed_files: list[str], config: dict[str, Any]) -> str:
    if not changed_files:
        return "(no changed file snippets)"
    snippet_chars = int(config.get("changed_file_snippet_chars", 2200))
    snippets: list[str] = []
    for file_name in changed_files[:12]:
        path = root / file_name
        if not path.exists() or not path.is_file():
            continue
        content = safe_read_text(path)
        if content is None:
            continue
        excerpt = content[:snippet_chars].strip()
        if not excerpt:
            continue
        snippets.append(f"### {file_name}\n{excerpt}\n")
    return "\n".join(snippets) if snippets else "(changed files are not readable text files)"


def fallback_experiment_report(
    *,
    command_text: str,
    git_snapshot: GitSnapshot,
    parent_id: str | None,
    parent_record: dict[str, Any] | None,
    report_language: str,
) -> dict[str, Any]:
    changed_files = git_snapshot.changed_files
    if is_chinese(report_language):
        if changed_files:
            focus = "、".join(changed_files[:3])
            if len(changed_files) > 3:
                focus += f" 等另外 {len(changed_files) - 3} 个文件"
            title = f"更新 {focus}"
            summary = f"执行了 `{command_text}`，并记录了围绕 {focus} 的实验变更。"
        else:
            title = "记录一次仅命令变更的实验"
            summary = f"执行了 `{command_text}`，相对最新 Git commit 没有检测到代码或配置改动。"
    elif changed_files:
        focus = ", ".join(changed_files[:3])
        if len(changed_files) > 3:
            focus += f" and {len(changed_files) - 3} more files"
        title = f"Update {focus}"
        summary = f"Ran `{command_text}` after changing {focus}."
    else:
        title = "Record a command-only experiment"
        summary = f"Ran `{command_text}` without detected code or config edits against the latest Git commit."

    change_types = ["command"]
    if changed_files:
        change_types.append("code")
    if any(name.endswith((".yaml", ".yml", ".json", ".toml", ".ini")) for name in changed_files):
        change_types.append("config")

    parent_title = parent_record.get("title") if parent_record else "root"
    if is_chinese(report_language):
        change_description = (
            f"本次实验继承自父实验 `{parent_id}`。" if parent_id else "本次实验作为新的根实验开始记录。"
        )
        change_description += f" 它在 commit `{git_snapshot.commit or 'N/A'}` 上执行了 `{command_text}`"
        change_description += f"，变更文件为：{'、'.join(changed_files) if changed_files else '无'}。"
        evidence_index = [
            "command.txt：本次包装执行的实验命令",
            "diff.patch：相对最新 Git commit 的工作区 diff",
            "changed_files.txt：变更文件列表",
        ]
        review_hints = [
            "如果报告没有准确表达实验意图，可以直接编辑 record.md。",
            "请确认这些变更更偏向算法、配置，还是数据处理流程。",
        ]
    else:
        change_description = (
            f"This experiment used parent `{parent_id}`." if parent_id else "This experiment starts a new root lineage."
        )
        change_description += f" It ran `{command_text}` on commit `{git_snapshot.commit or 'N/A'}`"
        change_description += f" with changed files: {', '.join(changed_files) if changed_files else 'none'}."
        evidence_index = [
            "command.txt: exact wrapped experiment command",
            "diff.patch: workspace diff against latest Git commit",
            "changed_files.txt: changed file list",
        ]
        review_hints = [
            "Edit record.md if the report misses important scientific intent.",
            "Confirm whether changed files reflect algorithm, config, or data pipeline changes.",
        ]
    return {
        "title": title,
        "summary": summary,
        "change_description": change_description,
        "change_types": change_types,
        "affected_files": changed_files,
        "affected_stages": infer_affected_stages(changed_files),
        "semantic_diff_from_parent": {
            "before": parent_title,
            "after": (
                f"命令 `{command_text}` 以及相对最新 Git commit 的工作区变化。"
                if is_chinese(report_language)
                else f"Command `{command_text}` with workspace delta relative to the latest Git commit."
            ),
        },
        "evidence_index": evidence_index,
        "review_hints": review_hints,
    }


def infer_affected_stages(changed_files: list[str]) -> list[str]:
    stages: list[str] = []
    names = " ".join(changed_files).lower()
    if any(token in names for token in ["config", ".yaml", ".yml", ".json", ".toml", ".ini"]):
        stages.append("configuration")
    if any(token in names for token in ["train", "trainer", "fit"]):
        stages.append("training")
    if any(token in names for token in ["eval", "test", "metric", "infer", "predict"]):
        stages.append("evaluation")
    if any(token in names for token in ["data", "dataset", "loader", "preprocess"]):
        stages.append("data")
    if not stages and changed_files:
        stages.append("implementation")
    return stages


def write_experiment_files(
    *,
    experiment_dir: Path,
    command_text: str,
    git_snapshot: GitSnapshot,
    record: dict[str, Any],
    semantic_result: AIResult,
    config: dict[str, Any],
) -> None:
    (experiment_dir / "command.txt").write_text(command_text + "\n", encoding="utf-8")
    (experiment_dir / "diff.patch").write_text(git_snapshot.diff, encoding="utf-8")
    (experiment_dir / "changed_files.txt").write_text("\n".join(git_snapshot.changed_files) + ("\n" if git_snapshot.changed_files else ""), encoding="utf-8")
    (experiment_dir / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    ai_markdown = render_experiment_markdown(record)
    ai_record_path(experiment_dir).write_text(ai_markdown, encoding="utf-8")
    editable_path = editable_record_path(experiment_dir)
    if not editable_path.exists():
        editable_path.write_text(ai_markdown, encoding="utf-8")

    if config.get("save_prompt_artifacts", True) and semantic_result.raw_response is None and not ai_raw_path(experiment_dir).exists():
        ai_raw_path(experiment_dir).write_text(json.dumps({"backend": semantic_result.backend, "error": semantic_result.error}, ensure_ascii=False, indent=2), encoding="utf-8")


def render_experiment_markdown(record: dict[str, Any]) -> str:
    report_model = record.get("report_model")
    report_backend = record["report_backend"]
    report_backend_label = f"{report_backend} ({report_model})" if report_model else report_backend
    lines = [
        f"# {record['experiment_id']}",
        "",
        f"- Parent: {record['parent_id'] or 'None'}",
        f"- Title: {record['title']}",
        f"- Command: `{record['command']}`",
        f"- Created At: {record['created_at']}",
        f"- Git Commit: `{record['git_commit'] or 'N/A'}`",
        f"- Git Branch: `{record['git_branch'] or 'N/A'}`",
        f"- Report Language: {record.get('report_language', 'N/A')}",
        f"- Report Backend: {report_backend_label}",
        "",
        "## Summary",
        "",
        record["summary"],
        "",
        "## Change Description",
        "",
        record["change_description"],
        "",
        "## Change Types",
        "",
    ]
    lines.extend(f"- {item}" for item in record["change_types"]) if record["change_types"] else lines.append("- (none)")
    lines.extend(
        [
            "",
            "## Affected Files",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in record["affected_files"]) if record["affected_files"] else lines.append("- (none)")
    lines.extend(
        [
            "",
            "## Affected Stages",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in record["affected_stages"]) if record["affected_stages"] else lines.append("- (none)")
    lines.extend(
        [
            "",
            "## Semantic Diff From Parent",
            "",
            f"- Before: {record['semantic_diff_from_parent']['before']}",
            f"- After: {record['semantic_diff_from_parent']['after']}",
            "",
            "## Evidence Index",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in record["evidence_index"]) if record["evidence_index"] else lines.append("- (none)")
    lines.extend(
        [
            "",
            "## Review Hints",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in record["review_hints"]) if record["review_hints"] else lines.append("- (none)")
    if record.get("report_error"):
        lines.extend(
            [
                "",
                "## AI Note",
                "",
                record["report_error"],
            ]
        )
    lines.append("")
    return "\n".join(lines)


def update_index(index: dict[str, Any], record: dict[str, Any]) -> None:
    summary_entry = {
        "experiment_id": record["experiment_id"],
        "parent_id": record["parent_id"],
        "title": record["title"],
        "summary": record["summary"],
        "git_commit": record["git_commit"],
        "created_at": record["created_at"],
    }
    index.setdefault("experiments", []).append(summary_entry)
    commit = record.get("git_commit")
    if commit:
        commit_index = index.setdefault("commit_index", {})
        commit_index.setdefault(commit, []).append(record["experiment_id"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_chinese(language: str) -> bool:
    normalized = language.strip().lower()
    return normalized in {"zh", "zh-cn", "zh-hans", "chinese", "simplified chinese", "中文", "简体中文"}
