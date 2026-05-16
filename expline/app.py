from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import shutil
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
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1/responses",
    "openai_timeout_seconds": 60,
    "save_prompt_artifacts": True,
    "project_context_max_files": 40,
    "project_context_total_chars": 24000,
    "project_file_snippet_chars": 1600,
    "diff_max_chars": 18000,
    "focused_diff_max_chars": 12000,
    "changed_file_snippet_chars": 2200,
    "result_context_max_files": 20,
    "result_file_snippet_chars": 1200,
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
- Prioritize experiment-critical code and configuration over documentation or organizational edits.
- Be concrete about the mechanism: name changed functions/classes, parameters, ranking logic, data flow, retrieval/evaluation stages, and outputs when evidence is available.
- Do not summarize broad repository cleanup as the main change unless it directly changes experiment behavior.
- If many files changed, identify the 1-3 files most likely to affect the experiment outcome and explain their specific role first.
- Base change_description on concrete diff hunks, not just filenames. Explain what behavior the edited code now performs differently from the parent experiment.
- Distinguish "file role" from "actual change": do not merely say a file is the retrieval backbone; say what logic in that file changed and how that changes the experiment design.
- When git_diff_mode is parent_commit, interpret the diff direction carefully: removed '-' lines are parent experiment behavior, added '+' lines are current experiment behavior.
- Explicitly describe the experiment design transition: parent pipeline/design -> current pipeline/design -> why this changes the experimental question or control condition.

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

Git diff comparison:
{{ git_diff_comparison }}

Changed files:
{{ changed_files }}

Focused code/config diff:
{{ focused_diff_text }}

Current diff:
{{ diff_text }}

Changed file snippets:
{{ changed_file_snippets }}

Recorded result artifacts (evidence only, not result analysis):
{{ result_artifacts }}

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
    diff_mode: str = "workspace"
    diff_base: str | None = None
    diff_target: str | None = None


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
    run_parser.add_argument("--result-path", action="append", default=[], help="Result file or directory to summarize after the command finishes; can be used multiple times")
    run_parser.add_argument("command_parts", nargs=argparse.REMAINDER, help="Command to execute after --")
    run_parser.set_defaults(func=cmd_run)

    list_parser = subparsers.add_parser("list", help="List recorded experiments")
    list_parser.add_argument("--limit", type=int, help="Show at most N experiments")
    list_parser.add_argument("--branch", help="Only show experiments recorded on this Git branch")
    list_parser.add_argument("--parent", dest="parent_id", help="Only show direct children of this parent experiment")
    list_parser.set_defaults(func=cmd_list)

    undo_parser = subparsers.add_parser("undo", help="Delete the most recent experiment record")
    undo_parser.add_argument("-y", "--yes", action="store_true", help="Actually delete the latest experiment; without this, only preview")
    undo_parser.set_defaults(func=cmd_undo)

    site_parser = subparsers.add_parser("site", help="Generate the static experiment lineage HTML page")
    site_parser.set_defaults(func=cmd_site)

    show_parser = subparsers.add_parser("show", help="Show a recorded experiment")
    show_parser.add_argument("experiment_id", help="Experiment ID like EXP-0001")
    show_parser.set_defaults(func=cmd_show)

    edit_parser = subparsers.add_parser("edit", help="Print the editable report path for an experiment")
    edit_parser.add_argument("experiment_id", help="Experiment ID like EXP-0001")
    edit_parser.set_defaults(func=cmd_edit)

    rebuild_parser = subparsers.add_parser("rebuild", help="Rebuild ExpLine index from experiment record.json files")
    rebuild_parser.set_defaults(func=cmd_rebuild)

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
    progress = ProgressBar("ExpLine init", total=5)
    progress.step("Preparing .expline workspace")
    ensure_layout(root)
    config = ensure_config(root)
    ensure_default_text(project_prompt_path(root), DEFAULT_PROJECT_PROMPT_TEMPLATE)
    ensure_default_text(record_prompt_path(root), DEFAULT_RECORD_PROMPT_TEMPLATE)
    initialize_index(root)
    report_language = resolve_report_language(args, config)
    result = regenerate_project_summary(root, config, use_ai=not args.no_ai, report_language=report_language, progress=progress)
    refresh_site(root)
    progress.done("Initialization complete")
    print(f"Initialized ExpLine in {app_path(root)}")
    print_project_summary_status(result)
    return 0


def cmd_rescan(args: argparse.Namespace) -> int:
    root = Path.cwd()
    assert_initialized(root)
    progress = ProgressBar("ExpLine rescan", total=4)
    config = ensure_config(root)
    report_language = resolve_report_language(args, config)
    result = regenerate_project_summary(root, config, use_ai=not args.no_ai, report_language=report_language, progress=progress)
    progress.done("Project summary refreshed")
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
    parent_record = load_parent_record(root, parent_id)
    validate_parent_record(parent_id, parent_record, explicit=bool(args.parent_id))
    experiment_id = next_experiment_id(index)
    experiment_dir = experiments_path(root) / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=False)

    started_at = now_iso()
    command_text = format_command(command_parts)
    command_result = run_user_command(command_parts, root)
    git_snapshot = collect_parent_aware_git_snapshot(root, config, git_snapshot, parent_record)
    result_artifacts = collect_result_artifacts(root, args.result_path, config)
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
        result_artifacts=result_artifacts,
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
        "git_diff_mode": git_snapshot.diff_mode,
        "git_diff_base": git_snapshot.diff_base,
        "git_diff_target": git_snapshot.diff_target,
        "change_types": semantic_result.output["change_types"],
        "affected_files": semantic_result.output["affected_files"],
        "affected_stages": semantic_result.output["affected_stages"],
        "result_artifacts": result_artifacts,
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
    refresh_site(root, index)

    print(f"Recorded experiment {experiment_id} in {experiment_dir}")
    print(f"Editable report: {experiment_dir / 'record.md'}")
    if result_artifacts:
        print(f"Result artifacts: {experiment_dir / 'result_artifacts.md'}")
    if semantic_result.error:
        print(f"AI note: {semantic_result.error}")
    if command_result.returncode != 0:
        print(f"Wrapped command exited with code {command_result.returncode}", file=sys.stderr)
    return command_result.returncode


def cmd_list(args: argparse.Namespace) -> int:
    root = Path.cwd()
    assert_initialized(root)
    index = load_index(root)
    experiments = build_list_experiment_rows(root, index)

    if args.branch:
        experiments = [item for item in experiments if item.get("git_branch") == args.branch]
    if args.parent_id:
        experiments = [item for item in experiments if item.get("parent_id") == args.parent_id]

    experiments.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("experiment_id") or "")), reverse=True)

    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit must be greater than 0")
        experiments = experiments[: args.limit]

    if not experiments:
        print("No experiments found.")
        print("If records exist under .expline/experiments, run: expline rebuild")
        return 0

    print_experiment_table(experiments)
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    root = Path.cwd()
    assert_initialized(root)
    latest, warnings = find_latest_experiment_record(root)
    if latest is None:
        print("No experiment records found.")
        return 0

    experiment_id, experiment_dir, record = latest
    children = find_child_experiments(root, experiment_id)
    if children:
        child_list = ", ".join(children)
        raise SystemExit(f"Cannot undo {experiment_id}; it has child experiment(s): {child_list}.")

    command = str(record.get("command") or "").strip()
    exit_code = record.get("command_exit_code")
    print(f"Latest experiment: {experiment_id}")
    print(f"Directory: {experiment_dir}")
    if command:
        print(f"Command: {command}")
    if exit_code is not None:
        print(f"Command exit code: {exit_code}")

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")

    if not args.yes:
        print("Dry run only. Re-run with: expline undo --yes")
        return 0

    experiments_root = experiments_path(root).resolve()
    resolved_dir = experiment_dir.resolve()
    try:
        resolved_dir.relative_to(experiments_root)
    except ValueError:
        raise SystemExit(f"Refusing to delete path outside experiments directory: {experiment_dir}")
    if not resolved_dir.name.startswith(EXPERIMENT_PREFIX):
        raise SystemExit(f"Refusing to delete non-experiment directory: {experiment_dir}")

    remove_experiment_dir(resolved_dir)
    rebuilt_index, rebuild_warnings = rebuild_index_from_records(root)
    save_index(root, rebuilt_index)
    refresh_site(root, rebuilt_index)
    print(f"Deleted experiment {experiment_id}.")
    print(f"Next experiment ID: {EXPERIMENT_PREFIX}{rebuilt_index['next_id']:04d}")
    if rebuild_warnings:
        print("Warnings:")
        for warning in rebuild_warnings:
            print(f"- {warning}")
    return 0


def cmd_site(args: argparse.Namespace) -> int:
    root = Path.cwd()
    assert_initialized(root)
    index = load_index(root)
    html_path = write_site(root, index)
    print(f"Generated ExpLine site: {html_path}")
    print("Remote server preview:")
    print("  cd .expline/site")
    print("  python -m http.server 8765 --bind 127.0.0.1")
    print("Then forward port 8765 in VS Code Remote or SSH and open http://localhost:8765")
    return 0


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


def cmd_rebuild(args: argparse.Namespace) -> int:
    root = Path.cwd()
    assert_initialized(root)
    rebuilt_index, warnings = rebuild_index_from_records(root)
    save_index(root, rebuilt_index)
    refresh_site(root, rebuilt_index)
    experiment_count = len(rebuilt_index.get("experiments", []))
    print(f"Rebuilt ExpLine index from {experiment_count} experiment record(s).")
    print(f"Next experiment ID: {EXPERIMENT_PREFIX}{rebuilt_index['next_id']:04d}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
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
    config[key] = coerce_config_value(key, args.value)
    write_json(config_path(root), config)
    print(f"{args.key} = {config[key]}")
    return 0


def app_path(root: Path) -> Path:
    return root / APP_DIRNAME


def experiments_path(root: Path) -> Path:
    return app_path(root) / "experiments"


def site_dir(root: Path) -> Path:
    return app_path(root) / "site"


def site_index_path(root: Path) -> Path:
    return site_dir(root) / "index.html"


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
    site_dir(root).mkdir(parents=True, exist_ok=True)


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
        "openai-api-key": "openai_api_key",
        "api-key": "openai_api_key",
        "openai-base-url": "openai_base_url",
        "base-url": "openai_base_url",
        "openai-model": "openai_model",
        "model": "openai_model",
        "diff-max-chars": "diff_max_chars",
        "focused-diff-max-chars": "focused_diff_max_chars",
    }
    if normalized not in aliases:
        allowed = ", ".join(sorted(aliases))
        raise SystemExit(f"Unsupported config key: {key}. Supported keys: {allowed}")
    return aliases[normalized]


def coerce_config_value(key: str, value: str) -> Any:
    text = value.strip() if isinstance(value, str) else value
    if key in {"diff_max_chars", "focused_diff_max_chars"}:
        try:
            parsed = int(text)
        except (TypeError, ValueError):
            raise SystemExit(f"{key} must be an integer")
        if parsed <= 0:
            raise SystemExit(f"{key} must be greater than 0")
        return parsed
    return text


class ProgressBar:
    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = total
        self.current = 0
        self.interactive = sys.stdout.isatty()

    def step(self, message: str) -> None:
        self.current = min(self.current + 1, self.total)
        self.render(message)

    def done(self, message: str) -> None:
        self.current = self.total
        self.render(message)
        if self.interactive:
            print()

    def render(self, message: str) -> None:
        width = 24
        filled = round(width * self.current / self.total) if self.total else width
        bar = "#" * filled + "-" * (width - filled)
        line = f"{self.label} [{bar}] {self.current}/{self.total} {message}"
        if self.interactive:
            print(f"\r{line}", end="", flush=True)
        else:
            print(line)


def regenerate_project_summary(
    root: Path,
    config: dict[str, Any],
    use_ai: bool,
    report_language: str,
    progress: ProgressBar | None = None,
) -> AIResult:
    if progress:
        progress.step("Scanning project files")
    project_context = build_project_context(root, config)
    if progress:
        progress.step("Preparing project summary prompt")
    fallback_output = fallback_project_summary(project_context, report_language)
    prompt_template = project_prompt_path(root).read_text(encoding="utf-8")
    if progress:
        progress.step("Generating semantic project summary")
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
    if progress:
        progress.step("Writing project summary files")
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


def truncate_text(text: str, max_chars: int, marker: str) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[{marker}]\n"


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
    diff = truncate_text(diff, diff_max_chars, "[diff truncated by ExpLine]")
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
    return GitSnapshot(
        True,
        commit or None,
        branch or None,
        bool(changed_files),
        diff,
        changed_files,
        "workspace",
        commit or None,
        "workspace",
    )


def collect_parent_aware_git_snapshot(
    root: Path,
    config: dict[str, Any],
    snapshot: GitSnapshot,
    parent_record: dict[str, Any] | None,
) -> GitSnapshot:
    if not snapshot.is_repo or not snapshot.commit or not parent_record:
        return snapshot

    parent_commit = parent_record.get("git_commit")
    if not isinstance(parent_commit, str) or not parent_commit.strip() or parent_commit == snapshot.commit:
        return snapshot
    if run_git(root, ["cat-file", "-e", f"{parent_commit}^{{commit}}"]).returncode != 0:
        return snapshot

    diff_max_chars = int(config.get("diff_max_chars", 18000))
    committed_diff = git_output(root, ["diff", "--no-ext-diff", f"{parent_commit}..HEAD", "--", ".", ":(exclude).expline"])
    workspace_diff = snapshot.diff
    diff_parts = [
        f"[ExpLine comparison: parent experiment commit {parent_commit}..current commit {snapshot.commit}]",
        committed_diff or "(no committed diff between parent experiment commit and current commit)",
    ]
    if workspace_diff:
        diff_parts.extend(
            [
                "",
                "[ExpLine additional workspace diff against current HEAD]",
                workspace_diff,
            ]
        )
    diff = truncate_text("\n".join(diff_parts), diff_max_chars, "[parent-aware diff truncated by ExpLine]")
    committed_files = git_output(root, ["diff", "--name-only", f"{parent_commit}..HEAD", "--", ".", ":(exclude).expline"]).splitlines()
    changed_files = sorted({normalize_status_path(path) for path in [*committed_files, *snapshot.changed_files] if path and not is_internal_path(path)})
    return GitSnapshot(
        True,
        snapshot.commit,
        snapshot.branch,
        snapshot.dirty,
        diff,
        changed_files,
        "parent_commit",
        parent_commit,
        snapshot.commit,
    )


def run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)


def git_output(root: Path, args: list[str]) -> str:
    result = run_git(root, args)
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


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


def validate_parent_record(parent_id: str | None, parent_record: dict[str, Any] | None, explicit: bool) -> None:
    if not parent_id or parent_record is not None:
        return
    source = "specified" if explicit else "inferred"
    raise SystemExit(
        f"Parent experiment {source} but not found: {parent_id}. "
        "Check the experiment ID or rebuild the ExpLine index after merging branches."
    )


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
    result_artifacts: list[dict[str, Any]],
) -> AIResult:
    project_summary = project_summary_md_path(root).read_text(encoding="utf-8") if project_summary_md_path(root).exists() else "(project summary missing)"
    git_diff_comparison = build_git_diff_comparison(git_snapshot, parent_id, parent_record)
    focused_diff_text = build_focused_diff_text(git_snapshot.diff, config, project_summary)
    changed_file_snippets = build_changed_file_snippets(root, git_snapshot.changed_files, config)
    parent_report_text = parent_record["editable_markdown"] if parent_record and parent_record.get("editable_markdown") else "(no parent experiment)"
    result_artifact_summary = render_result_artifacts_for_prompt(result_artifacts)
    fallback_output = fallback_experiment_report(
        command_text=command_text,
        git_snapshot=git_snapshot,
        parent_id=parent_id,
        parent_record=parent_record,
        report_language=report_language,
        result_artifacts=result_artifacts,
    )
    prompt_template = enrich_record_prompt_template(record_prompt_path(root).read_text(encoding="utf-8"))
    if "{{ git_diff_comparison }}" not in prompt_template:
        prompt_template = f"{prompt_template.rstrip()}\n\nGit diff comparison:\n{{{{ git_diff_comparison }}}}\n"
    if "{{ focused_diff_text }}" not in prompt_template:
        prompt_template = f"{prompt_template.rstrip()}\n\nFocused code/config diff:\n{{{{ focused_diff_text }}}}\n"
    if "{{ result_artifacts }}" not in prompt_template:
        prompt_template = f"{prompt_template.rstrip()}\n\nRecorded result artifacts (evidence only, not result analysis):\n{{{{ result_artifacts }}}}\n"
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
            "git_diff_comparison": git_diff_comparison,
            "changed_files": "\n".join(git_snapshot.changed_files) if git_snapshot.changed_files else "(none)",
            "focused_diff_text": focused_diff_text,
            "diff_text": git_snapshot.diff or "(no diff available)",
            "changed_file_snippets": changed_file_snippets,
            "result_artifacts": result_artifact_summary,
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
    prioritized_files = sorted(changed_files, key=changed_file_priority)
    for file_name in prioritized_files[:12]:
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


def build_git_diff_comparison(
    git_snapshot: GitSnapshot,
    parent_id: str | None,
    parent_record: dict[str, Any] | None,
) -> str:
    lines = [
        f"- mode: {git_snapshot.diff_mode}",
        f"- parent_experiment_id: {parent_id or 'None'}",
        f"- parent_experiment_commit: {parent_record.get('git_commit') if parent_record else 'N/A'}",
        f"- diff_base: {git_snapshot.diff_base or 'N/A'}",
        f"- diff_target: {git_snapshot.diff_target or 'N/A'}",
        f"- current_commit: {git_snapshot.commit or 'N/A'}",
        f"- current_branch: {git_snapshot.branch or 'N/A'}",
    ]
    if git_snapshot.diff_mode == "parent_commit":
        lines.extend(
            [
                "- interpretation: compare parent experiment commit to current experiment commit.",
                "- removed_lines: behavior present in the parent experiment side.",
                "- added_lines: behavior present in the current experiment side.",
                "- required_focus: explain the experiment design transition from parent to current, not just a list of edited files.",
            ]
        )
    else:
        lines.extend(
            [
                "- interpretation: compare current workspace changes against the current HEAD.",
                "- required_focus: explain how uncommitted code/config changes alter this experiment run.",
            ]
        )
    return "\n".join(lines)


def build_focused_diff_text(diff_text: str, config: dict[str, Any], project_summary: str = "") -> str:
    if not diff_text.strip():
        return "(no focused code/config diff available)"
    max_chars = int(config.get("focused_diff_max_chars", 12000))
    sensitive_paths = extract_experiment_sensitive_paths(project_summary)
    file_diffs = split_unified_diff_by_file(diff_text)
    priority_diffs = [
        (focused_diff_priority(path, sensitive_paths), path, block)
        for path, block in file_diffs
        if include_in_focused_diff(path, sensitive_paths)
    ]
    if not priority_diffs:
        return "(no code/config diff hunks found; see Current diff for other changes)"
    return allocate_focused_diff_budget(sorted(priority_diffs, key=lambda item: item[0]), max_chars)


def allocate_focused_diff_budget(priority_diffs: list[tuple[tuple[int, str], str, str]], max_chars: int) -> str:
    block_count = len(priority_diffs)
    min_per_file = min(3000, max(1200, max_chars // max(block_count, 1)))
    remaining = max_chars
    selected: list[str] = []
    omitted: list[str] = []
    for _priority, path, block in priority_diffs:
        if remaining <= 0:
            omitted.append(path)
            continue
        budget = min(len(block), max(min_per_file, remaining // max(block_count - len(selected), 1)))
        if budget > remaining:
            budget = remaining
        rendered = block if len(block) <= budget else truncate_to_budget(block, budget, f"focused diff for {path} truncated by ExpLine")
        selected.append(rendered)
        remaining -= len(rendered) + 1
    if omitted:
        selected.append("[Focused diff omitted files due to budget: " + ", ".join(omitted) + "]")
    return "\n".join(selected)


def truncate_to_budget(text: str, max_chars: int, marker: str) -> str:
    suffix = f"\n\n[{marker}]\n"
    if max_chars <= len(suffix):
        return suffix[:max_chars]
    return text[: max_chars - len(suffix)] + suffix


def include_in_focused_diff(path: str, sensitive_paths: set[str]) -> bool:
    normalized = path.replace("\\", "/").lower()
    if normalized in sensitive_paths:
        return True
    priority = changed_file_priority(path)[0]
    return priority <= 3


def focused_diff_priority(path: str, sensitive_paths: set[str]) -> tuple[int, str]:
    normalized = path.replace("\\", "/").lower()
    if normalized in sensitive_paths:
        return (-1, normalized)
    return changed_file_priority(path)


def extract_experiment_sensitive_paths(project_summary: str) -> set[str]:
    sensitive_paths: set[str] = set()
    in_sensitive_section = False
    for raw_line in project_summary.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            heading = line.lstrip("#").strip().lower()
            in_sensitive_section = heading in {"experiment-sensitive modules", "experiment sensitive modules"}
            continue
        if not in_sensitive_section or not line.startswith("- "):
            continue
        candidate = line[2:].strip().strip("`")
        if candidate and not any(token in candidate for token in ["*", "<", ">"]):
            sensitive_paths.add(candidate.replace("\\", "/").lower())
    return sensitive_paths


def split_unified_diff_by_file(diff_text: str) -> list[tuple[str, str]]:
    file_diffs: list[tuple[str, str]] = []
    current_lines: list[str] = []
    current_path = ""
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current_lines:
                file_diffs.append((current_path, "\n".join(current_lines)))
            current_lines = [line]
            current_path = parse_diff_path(line)
        elif current_lines:
            current_lines.append(line)
    if current_lines:
        file_diffs.append((current_path, "\n".join(current_lines)))
    return file_diffs


def parse_diff_path(diff_header: str) -> str:
    parts = diff_header.split()
    if len(parts) >= 4:
        path = parts[3]
        if path.startswith("b/"):
            return path[2:]
        return path
    return ""


def changed_file_priority(file_name: str) -> tuple[int, str]:
    normalized = file_name.replace("\\", "/").lower()
    suffix = Path(normalized).suffix
    if normalized.startswith(("src/", "expline/", "lib/", "app/", "scripts/")) and suffix in {".py", ".sh"}:
        return (0, normalized)
    if normalized in {"main.py", "train.py", "eval.py", "evaluate.py"} or normalized.endswith(("/main.py", "/train.py", "/eval.py", "/evaluate.py")):
        return (1, normalized)
    if suffix in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg"}:
        return (2, normalized)
    if suffix in {".py", ".sh", ".ps1", ".bat"}:
        return (3, normalized)
    if normalized.startswith(("docs/", "doc/")) or suffix == ".md":
        return (8, normalized)
    return (5, normalized)


def enrich_record_prompt_template(prompt_template: str) -> str:
    marker = "Experiment-critical analysis rules:"
    if marker in prompt_template:
        return prompt_template
    return (
        prompt_template.rstrip()
        + """

Experiment-critical analysis rules:
- Prioritize code/config changes that can alter the experiment behavior, outputs, metrics, retrieval/ranking/training/evaluation pipeline, or data selection.
- Treat documentation, notes, refactors, and project organization as secondary unless they directly change how the experiment runs.
- In change_description, start with the concrete experimental mechanism before mentioning docs or cleanup.
- Use Focused code/config diff as primary evidence. Name the actual edited functions/classes/parameters/control flow and explain how they alter the experiment design compared with the parent experiment.
- Avoid file-role summaries. Do not write only that a file "is the retrieval backbone"; describe the concrete logic that changed inside it.
- If git_diff_mode is parent_commit, use Git diff comparison to interpret direction: '-' is parent experiment behavior and '+' is current experiment behavior.
- The report must answer this explicitly: compared with the parent experiment, what experimental pipeline/design was removed, added, or replaced?
- If result artifacts are provided, use them only as saved-output evidence. Do not explain metric changes, judge result quality, infer causality, or claim the method is better/worse because of those values.
"""
    )


def collect_result_artifacts(root: Path, requested_paths: list[str], config: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    max_files = int(config.get("result_context_max_files", 20))
    snippet_chars = int(config.get("result_file_snippet_chars", 1200))
    root_resolved = root.resolve()
    for requested_path in requested_paths:
        path_text = requested_path.strip() if isinstance(requested_path, str) else ""
        if not path_text:
            continue
        path = (root / path_text).resolve()
        artifact: dict[str, Any] = {
            "requested_path": path_text,
            "exists": path.exists(),
            "type": "missing",
            "files": [],
        }
        try:
            path.relative_to(root_resolved)
        except ValueError:
            artifact["error"] = "Path is outside the project root; skipped for safety."
            artifacts.append(artifact)
            continue
        if path.is_file():
            artifact["type"] = "file"
            artifact["files"] = [summarize_result_file(root, path, snippet_chars)]
        elif path.is_dir():
            artifact["type"] = "directory"
            files = [item for item in sorted(path.rglob("*")) if item.is_file() and not should_ignore_path(item.relative_to(root_resolved))]
            artifact["file_count"] = len(files)
            artifact["files"] = [summarize_result_file(root, item, snippet_chars) for item in files[:max_files]]
            if len(files) > max_files:
                artifact["truncated"] = True
        artifacts.append(artifact)
    return artifacts


def summarize_result_file(root: Path, path: Path, snippet_chars: int) -> dict[str, Any]:
    rel_path = path.relative_to(root.resolve()).as_posix()
    summary: dict[str, Any] = {"path": rel_path}
    try:
        summary["size_bytes"] = path.stat().st_size
    except OSError:
        summary["size_bytes"] = None
    if path.suffix.lower() in TEXT_FILE_SUFFIXES or path.name.lower() in {"makefile"}:
        content = safe_read_text(path)
        if content is not None:
            summary["snippet"] = truncate_text(content.strip(), snippet_chars, "result snippet truncated by ExpLine")
    return summary


def render_result_artifacts_for_prompt(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts:
        return "(no result artifacts requested)"
    lines: list[str] = []
    for artifact in artifacts:
        lines.append(f"## {artifact.get('requested_path')}")
        lines.append(f"- exists: {artifact.get('exists')}")
        lines.append(f"- type: {artifact.get('type')}")
        if artifact.get("error"):
            lines.append(f"- error: {artifact['error']}")
        if artifact.get("file_count") is not None:
            lines.append(f"- file_count: {artifact['file_count']}")
        for file_info in artifact.get("files", []):
            lines.append(f"### {file_info.get('path')}")
            lines.append(f"- size_bytes: {file_info.get('size_bytes')}")
            if file_info.get("snippet"):
                lines.append("```text")
                lines.append(str(file_info["snippet"]))
                lines.append("```")
        lines.append("")
    return "\n".join(lines).strip()


def fallback_experiment_report(
    *,
    command_text: str,
    git_snapshot: GitSnapshot,
    parent_id: str | None,
    parent_record: dict[str, Any] | None,
    report_language: str,
    result_artifacts: list[dict[str, Any]],
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
    if result_artifacts:
        change_types.append("result")

    parent_title = parent_record.get("title") if parent_record else "root"
    if is_chinese(report_language):
        change_description = (
            f"本次实验继承自父实验 `{parent_id}`。" if parent_id else "本次实验作为新的根实验开始记录。"
        )
        change_description += f" 它在 commit `{git_snapshot.commit or 'N/A'}` 上执行了 `{command_text}`"
        change_description += f"，变更文件为：{'、'.join(changed_files) if changed_files else '无'}。"
        evidence_index = [
            "command.txt：本次包装执行的实验命令",
            "diff.patch：父实验 commit 到当前 commit 的 diff，或相对最新 Git commit 的工作区 diff",
            "changed_files.txt：变更文件列表",
        ]
        if result_artifacts:
            evidence_index.append("result_artifacts.json：用户指定的实验结果文件或目录摘要")
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
            "diff.patch: parent commit to current commit diff, or workspace diff against latest Git commit",
            "changed_files.txt: changed file list",
        ]
        if result_artifacts:
            evidence_index.append("result_artifacts.json: summary of user-specified result files or directories")
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
    (experiment_dir / "result_artifacts.json").write_text(json.dumps(record.get("result_artifacts", []), ensure_ascii=False, indent=2), encoding="utf-8")
    (experiment_dir / "result_artifacts.md").write_text(render_result_artifacts_for_prompt(record.get("result_artifacts", [])) + "\n", encoding="utf-8")
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
        f"- Git Diff Mode: {record.get('git_diff_mode', 'workspace')}",
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
            "## Result Artifacts",
            "",
            render_result_artifacts_for_prompt(record.get("result_artifacts", [])),
        ]
    )
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


def refresh_site(root: Path, index: dict[str, Any] | None = None) -> None:
    try:
        write_site(root, index or load_index(root))
    except Exception as exc:
        print(f"Warning: could not refresh ExpLine site: {exc}", file=sys.stderr)


def write_site(root: Path, index: dict[str, Any]) -> Path:
    site_dir(root).mkdir(parents=True, exist_ok=True)
    data = build_site_data(root, index)
    html = render_site_html(data)
    path = site_index_path(root)
    path.write_text(html, encoding="utf-8")
    return path


def build_site_data(root: Path, index: dict[str, Any]) -> dict[str, Any]:
    experiments = []
    for entry in build_list_experiment_rows(root, index):
        experiment_id = str(entry.get("experiment_id") or "")
        if not experiment_id:
            continue
        experiment_dir = experiments_path(root) / experiment_id
        record = load_experiment_record(root, experiment_id) or entry
        experiments.append(build_site_experiment(root, experiment_dir, record))
    experiments.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("experiment_id") or "")))
    return {
        "generated_at": now_iso(),
        "project_root": str(root.resolve()),
        "site_path": str(site_index_path(root).resolve()),
        "experiments": experiments,
        "edges": [
            {"source": item["parent_id"], "target": item["experiment_id"]}
            for item in experiments
            if item.get("parent_id")
        ],
    }


def build_site_experiment(root: Path, experiment_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    experiment_id = str(record.get("experiment_id") or experiment_dir.name)
    record_md = read_optional_text(editable_record_path(experiment_dir))
    command_text = read_optional_text(experiment_dir / "command.txt") or str(record.get("command") or "")
    changed_files_text = read_optional_text(experiment_dir / "changed_files.txt")
    diff_text = read_optional_text(experiment_dir / "diff.patch")
    if diff_text:
        diff_text = truncate_text(diff_text, 30000, "[site diff preview truncated by ExpLine]")
    semantic_diff = record.get("semantic_diff_from_parent") or {}
    evidence = record.get("evidence_index")
    if evidence is None:
        evidence = record.get("evidence_files", [])
    return {
        "experiment_id": experiment_id,
        "parent_id": record.get("parent_id"),
        "title": record.get("title") or experiment_id,
        "summary": record.get("summary") or "",
        "change_description": record.get("change_description") or "",
        "command": command_text,
        "created_at": record.get("created_at") or "",
        "git_commit": record.get("git_commit"),
        "git_branch": record.get("git_branch"),
        "git_dirty": record.get("git_dirty"),
        "change_types": record.get("change_types") or [],
        "affected_files": record.get("affected_files") or [],
        "affected_stages": record.get("affected_stages") or [],
        "semantic_before": semantic_diff.get("before", "") if isinstance(semantic_diff, dict) else "",
        "semantic_after": semantic_diff.get("after", "") if isinstance(semantic_diff, dict) else "",
        "evidence": evidence,
        "record_md": record_md,
        "changed_files_text": changed_files_text,
        "diff_preview": diff_text,
        "paths": {
            "experiment_dir": str(experiment_dir.resolve()),
            "record_md": str(editable_record_path(experiment_dir).resolve()),
            "diff_patch": str((experiment_dir / "diff.patch").resolve()),
            "command_txt": str((experiment_dir / "command.txt").resolve()),
            "changed_files_txt": str((experiment_dir / "changed_files.txt").resolve()),
        },
    }


def read_optional_text(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def render_site_html(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    generated_at = html_lib.escape(str(data.get("generated_at") or ""))
    project_root = html_lib.escape(str(data.get("project_root") or ""))
    return SITE_HTML_TEMPLATE.replace("__EXPLINE_DATA__", payload).replace("__GENERATED_AT__", generated_at).replace("__PROJECT_ROOT__", project_root)


SITE_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ExpLine Experiment Lineage</title>
  <style>
    :root {
      --ink: #18201b;
      --muted: #66736a;
      --paper: #f6f0e4;
      --panel: #fffaf0;
      --line: #d8ccb7;
      --accent: #1f7a68;
      --accent-2: #e07836;
      --shadow: 0 18px 55px rgba(57, 42, 22, 0.14);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 12% 5%, rgba(224, 120, 54, 0.18), transparent 32rem),
        radial-gradient(circle at 90% 8%, rgba(31, 122, 104, 0.16), transparent 26rem),
        linear-gradient(135deg, #fbf5e9 0%, #efe5d1 100%);
      font-family: Georgia, "Times New Roman", serif;
    }
    header {
      padding: 28px 34px 20px;
      border-bottom: 1px solid rgba(99, 82, 52, 0.18);
    }
    h1 { margin: 0; font-size: clamp(30px, 5vw, 58px); letter-spacing: -0.05em; }
    .subtitle { margin-top: 8px; color: var(--muted); font-family: "Trebuchet MS", sans-serif; }
    .toolbar {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      padding: 18px 34px;
    }
    input {
      min-width: min(520px, 100%);
      flex: 1;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 12px 18px;
      background: rgba(255, 250, 240, 0.8);
      color: var(--ink);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.8);
      font-size: 15px;
      font-family: "Trebuchet MS", sans-serif;
    }
    button {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 11px 15px;
      background: var(--panel);
      color: var(--ink);
      cursor: pointer;
      font-family: "Trebuchet MS", sans-serif;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(340px, 0.8fr);
      gap: 18px;
      padding: 0 22px 22px;
      min-height: calc(100vh - 150px);
    }
    .graph-wrap, .detail {
      position: relative;
      min-height: 620px;
      border: 1px solid rgba(99, 82, 52, 0.18);
      border-radius: 28px;
      background: rgba(255, 250, 240, 0.72);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .graph-wrap {
      overflow: auto;
      cursor: grab;
      user-select: none;
    }
    .graph-wrap.dragging {
      cursor: grabbing;
    }
    .graph {
      position: relative;
      min-height: 620px;
      min-width: 100%;
      overflow: visible;
    }
    #edges {
      position: absolute;
      left: 0;
      top: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
      overflow: visible;
    }
    .node {
      position: absolute;
      width: 250px;
      min-height: 118px;
      padding: 15px 16px;
      border: 1px solid rgba(31, 122, 104, 0.28);
      border-radius: 22px;
      background: linear-gradient(160deg, rgba(255,255,255,0.82), rgba(255,250,240,0.96));
      box-shadow: 0 14px 34px rgba(55, 44, 22, 0.14);
      cursor: pointer;
      transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease, opacity 160ms ease;
      font-family: "Trebuchet MS", sans-serif;
    }
    .node:hover { transform: translateY(-2px); box-shadow: 0 18px 42px rgba(55, 44, 22, 0.18); }
    .node .id { font-weight: 700; color: var(--accent); font-size: 13px; letter-spacing: 0.04em; }
    .node .title { margin-top: 8px; font-size: 16px; font-weight: 700; line-height: 1.25; }
    .node .meta { margin-top: 8px; color: var(--muted); font-size: 12px; }
    .node.active { border-color: var(--accent-2); box-shadow: 0 0 0 3px rgba(224, 120, 54, 0.18), var(--shadow); }
    .node.ancestor, .node.descendant { border-color: var(--accent); }
    .node.dim { opacity: 0.32; }
    .node.search-hit { box-shadow: 0 0 0 3px rgba(31, 122, 104, 0.18), var(--shadow); }
    .edge { stroke: rgba(31, 122, 104, 0.28); stroke-width: 2.2; fill: none; }
    .edge.hot { stroke: var(--accent-2); stroke-width: 3.5; }
    .detail {
      padding: 24px;
      overflow: auto;
    }
    .detail h2 { margin: 0 0 6px; font-size: 31px; letter-spacing: -0.04em; }
    .detail .meta-line { color: var(--muted); font-family: "Trebuchet MS", sans-serif; font-size: 13px; }
    .section { margin-top: 22px; }
    .section h3 { margin: 0 0 8px; font-size: 15px; font-family: "Trebuchet MS", sans-serif; color: var(--accent); text-transform: uppercase; letter-spacing: 0.08em; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .chip { border-radius: 999px; padding: 5px 9px; background: rgba(31, 122, 104, 0.10); color: #25584f; font-family: "Trebuchet MS", sans-serif; font-size: 12px; }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 260px;
      overflow: auto;
      padding: 14px;
      border-radius: 16px;
      background: rgba(35, 29, 18, 0.06);
      border: 1px solid rgba(99, 82, 52, 0.12);
      font-family: "Cascadia Code", Consolas, monospace;
      font-size: 12px;
      line-height: 1.55;
    }
    pre.diff-preview {
      max-height: 420px;
      white-space: pre;
      overflow: auto;
    }
    pre.record-preview {
      max-height: 360px;
    }
    .context-menu {
      position: fixed;
      display: none;
      z-index: 20;
      width: 280px;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: #fffaf0;
      box-shadow: var(--shadow);
      font-family: "Trebuchet MS", sans-serif;
    }
    .context-menu button {
      display: block;
      width: 100%;
      margin: 2px 0;
      text-align: left;
      border-radius: 12px;
      border: 0;
      background: transparent;
    }
    .toast {
      position: fixed;
      left: 50%;
      bottom: 24px;
      transform: translateX(-50%);
      display: none;
      padding: 10px 14px;
      border-radius: 999px;
      background: #18201b;
      color: white;
      font-family: "Trebuchet MS", sans-serif;
      box-shadow: var(--shadow);
      z-index: 30;
    }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      .detail { min-height: 360px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>ExpLine Experiment Lineage</h1>
    <div class="subtitle">Project: __PROJECT_ROOT__ · Generated: __GENERATED_AT__</div>
  </header>
  <div class="toolbar">
    <input id="search" placeholder="Search ID, title, summary, change types, files, commit...">
    <button id="reset">Reset highlight</button>
    <button id="fit">Fit graph</button>
  </div>
  <main>
    <section class="graph-wrap">
      <div id="graph" class="graph">
        <svg id="edges"></svg>
      </div>
    </section>
    <aside id="detail" class="detail"></aside>
  </main>
  <div id="menu" class="context-menu"></div>
  <div id="toast" class="toast"></div>
  <script id="expline-data" type="application/json">__EXPLINE_DATA__</script>
  <script>
    const data = JSON.parse(document.getElementById('expline-data').textContent);
    const experiments = data.experiments || [];
    const byId = new Map(experiments.map(item => [item.experiment_id, item]));
    const children = new Map();
    for (const item of experiments) {
      if (!children.has(item.parent_id || '__root__')) children.set(item.parent_id || '__root__', []);
      children.get(item.parent_id || '__root__').push(item.experiment_id);
    }
    const graph = document.getElementById('graph');
    const graphWrap = document.querySelector('.graph-wrap');
    let svg = document.getElementById('edges');
    const detail = document.getElementById('detail');
    const menu = document.getElementById('menu');
    const toast = document.getElementById('toast');
    let activeId = experiments[0]?.experiment_id || null;
    let dragState = null;

    function depthOf(id, seen = new Set()) {
      const item = byId.get(id);
      if (!item || !item.parent_id || seen.has(id) || !byId.has(item.parent_id)) return 0;
      seen.add(id);
      return depthOf(item.parent_id, seen) + 1;
    }

    function layout() {
      graph.innerHTML = '<svg id="edges"></svg>';
      svg = document.getElementById('edges');
      const levels = new Map();
      for (const item of experiments) {
        const depth = depthOf(item.experiment_id);
        if (!levels.has(depth)) levels.set(depth, []);
        levels.get(depth).push(item);
      }
      const columnWidth = 310;
      const rowHeight = 168;
      const maxDepth = Math.max(0, ...Array.from(levels.keys()));
      let maxRows = 1;
      let maxRight = 760;
      let maxBottom = 560;
      for (const [depth, items] of levels) {
        items.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)) || a.experiment_id.localeCompare(b.experiment_id));
        maxRows = Math.max(maxRows, items.length);
        items.forEach((item, row) => {
          const node = document.createElement('div');
          node.className = 'node';
          node.dataset.id = item.experiment_id;
          node.style.left = `${38 + depth * columnWidth}px`;
          node.style.top = `${38 + row * rowHeight}px`;
          maxRight = Math.max(maxRight, 38 + depth * columnWidth + 250);
          maxBottom = Math.max(maxBottom, 38 + row * rowHeight + 140);
          node.innerHTML = `<div class="id">${escapeHtml(item.experiment_id)}</div><div class="title">${escapeHtml(item.title || item.summary || '')}</div><div class="meta">${escapeHtml(item.git_branch || '-')} · ${escapeHtml(shortCommit(item.git_commit))}</div>`;
          node.addEventListener('click', () => selectNode(item.experiment_id));
          node.addEventListener('contextmenu', event => openMenu(event, item));
          graph.appendChild(node);
        });
      }
      graph.style.width = `${Math.max(960, maxRight + 220, 110 + (maxDepth + 1) * columnWidth)}px`;
      graph.style.height = `${Math.max(620, maxBottom + 180, 110 + maxRows * rowHeight)}px`;
      requestAnimationFrame(drawEdges);
    }

    function drawEdges() {
      const graphWidth = graph.offsetWidth;
      const graphHeight = graph.offsetHeight;
      svg.setAttribute('viewBox', `0 0 ${graphWidth} ${graphHeight}`);
      svg.style.width = `${graphWidth}px`;
      svg.style.height = `${graphHeight}px`;
      svg.innerHTML = '';
      for (const edge of data.edges || []) {
        const source = document.querySelector(`.node[data-id="${cssEscape(edge.source)}"]`);
        const target = document.querySelector(`.node[data-id="${cssEscape(edge.target)}"]`);
        if (!source || !target) continue;
        const x1 = source.offsetLeft + source.offsetWidth;
        const y1 = source.offsetTop + source.offsetHeight / 2;
        const x2 = target.offsetLeft;
        const y2 = target.offsetTop + target.offsetHeight / 2;
        const mid = Math.max(40, (x2 - x1) / 2);
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M ${x1} ${y1} C ${x1 + mid} ${y1}, ${x2 - mid} ${y2}, ${x2} ${y2}`);
        path.setAttribute('class', `edge edge-${edge.source}-${edge.target}`);
        path.dataset.source = edge.source;
        path.dataset.target = edge.target;
        svg.appendChild(path);
      }
      highlight(activeId);
    }

    function selectNode(id) {
      activeId = id;
      renderDetail(byId.get(id));
      highlight(id);
    }

    function collectAncestors(id) {
      const out = new Set();
      let cursor = byId.get(id)?.parent_id;
      while (cursor && byId.has(cursor) && !out.has(cursor)) {
        out.add(cursor);
        cursor = byId.get(cursor).parent_id;
      }
      return out;
    }

    function collectDescendants(id) {
      const out = new Set();
      const stack = [...(children.get(id) || [])];
      while (stack.length) {
        const current = stack.pop();
        if (!current || out.has(current)) continue;
        out.add(current);
        stack.push(...(children.get(current) || []));
      }
      return out;
    }

    function highlight(id) {
      const ancestors = collectAncestors(id);
      const descendants = collectDescendants(id);
      document.querySelectorAll('.node').forEach(node => {
        const nodeId = node.dataset.id;
        node.classList.toggle('active', nodeId === id);
        node.classList.toggle('ancestor', ancestors.has(nodeId));
        node.classList.toggle('descendant', descendants.has(nodeId));
        node.classList.toggle('dim', id && nodeId !== id && !ancestors.has(nodeId) && !descendants.has(nodeId));
      });
      document.querySelectorAll('.edge').forEach(edge => {
        const hot = (edge.dataset.target === id && ancestors.has(edge.dataset.source)) ||
          (descendants.has(edge.dataset.target) && (edge.dataset.source === id || descendants.has(edge.dataset.source) || edge.dataset.source === id));
        edge.classList.toggle('hot', hot);
      });
    }

    function renderDetail(item) {
      if (!item) {
        detail.innerHTML = '<h2>No experiments yet</h2><p class="meta-line">Run <code>expline run -- &lt;command&gt;</code> to create the first node.</p>';
        return;
      }
      detail.innerHTML = `
        <h2>${escapeHtml(item.experiment_id)}</h2>
        <div class="meta-line">${escapeHtml(item.title || '')}</div>
        <div class="section"><h3>Summary</h3><p>${escapeHtml(item.summary || '')}</p></div>
        <div class="section"><h3>Metadata</h3><pre>${escapeHtml(metadataText(item))}</pre></div>
        <div class="section"><h3>Command</h3><pre>${escapeHtml(item.command || '')}</pre></div>
        <div class="section"><h3>Change Types</h3><div class="chips">${chips(item.change_types)}</div></div>
        <div class="section"><h3>Affected Files</h3><pre>${escapeHtml((item.affected_files || []).join('\n'))}</pre></div>
        <div class="section"><h3>Semantic Diff From Parent</h3><pre>${escapeHtml(`Before: ${item.semantic_before || ''}\n\nAfter: ${item.semantic_after || ''}`)}</pre></div>
        <div class="section"><h3>Record.md</h3><pre class="record-preview">${escapeHtml(item.record_md || '')}</pre></div>
        <div class="section"><h3>Changed Files Evidence</h3><pre>${escapeHtml(item.changed_files_text || '')}</pre></div>
        <div class="section"><h3>Diff Preview</h3><pre class="diff-preview">${escapeHtml(item.diff_preview || '')}</pre></div>
        <div class="section"><h3>Paths</h3><pre>${escapeHtml(Object.entries(item.paths || {}).map(([key, value]) => `${key}: ${value}`).join('\n'))}</pre></div>
      `;
    }

    function metadataText(item) {
      return [
        `Parent: ${item.parent_id || 'None'}`,
        `Created: ${item.created_at || '-'}`,
        `Git branch: ${item.git_branch || '-'}`,
        `Git commit: ${item.git_commit || '-'}`,
        `Git dirty: ${item.git_dirty}`,
      ].join('\n');
    }

    function chips(values) {
      return (values || []).map(value => `<span class="chip">${escapeHtml(String(value))}</span>`).join('') || '<span class="chip">None</span>';
    }

    function openMenu(event, item) {
      event.preventDefault();
      const paths = item.paths || {};
      const actions = [
        ['Copy experiment ID', item.experiment_id],
        ['Copy experiment directory', paths.experiment_dir],
        ['Copy record.md path', paths.record_md],
        ['Copy diff.patch path', paths.diff_patch],
        ['Copy command.txt path', paths.command_txt],
        ['Copy VS Code open directory command', `code "${paths.experiment_dir || ''}"`],
        ['Copy VS Code open record command', `code "${paths.record_md || ''}"`],
      ];
      menu.innerHTML = actions.map(([label, value]) => `<button data-copy="${escapeHtml(value || '')}">${escapeHtml(label)}</button>`).join('');
      menu.style.display = 'block';
      menu.style.left = `${event.clientX}px`;
      menu.style.top = `${event.clientY}px`;
      menu.querySelectorAll('button').forEach(button => button.addEventListener('click', () => copyText(button.dataset.copy || '')));
    }

    async function copyText(text) {
      try {
        await navigator.clipboard.writeText(text);
        showToast('Copied');
      } catch {
        showToast('Copy failed; select the text from details instead');
      }
      menu.style.display = 'none';
    }

    function showToast(message) {
      toast.textContent = message;
      toast.style.display = 'block';
      setTimeout(() => { toast.style.display = 'none'; }, 1300);
    }

    function search(query) {
      const q = query.trim().toLowerCase();
      document.querySelectorAll('.node').forEach(node => node.classList.remove('search-hit', 'dim'));
      if (!q) {
        highlight(activeId);
        return;
      }
      const hits = new Set();
      for (const item of experiments) {
        const haystack = [
          item.experiment_id, item.title, item.summary, item.git_commit,
          ...(item.change_types || []), ...(item.affected_files || [])
        ].join(' ').toLowerCase();
        if (haystack.includes(q)) hits.add(item.experiment_id);
      }
      document.querySelectorAll('.node').forEach(node => {
        const hit = hits.has(node.dataset.id);
        node.classList.toggle('search-hit', hit);
        node.classList.toggle('dim', !hit);
      });
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    }
    function cssEscape(value) {
      if (window.CSS && CSS.escape) return CSS.escape(value);
      return String(value).replace(/["\\]/g, '\\$&');
    }
    function shortCommit(value) {
      return value ? String(value).slice(0, 7) : '-';
    }

    document.getElementById('search').addEventListener('input', event => search(event.target.value));
    document.getElementById('reset').addEventListener('click', () => { document.getElementById('search').value = ''; selectNode(activeId); });
    document.getElementById('fit').addEventListener('click', () => { graphWrap.scrollTo({left: 0, top: 0, behavior: 'smooth'}); });
    document.addEventListener('click', () => { menu.style.display = 'none'; });
    window.addEventListener('resize', drawEdges);
    graphWrap.addEventListener('scroll', drawEdges);
    graphWrap.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.target.closest('.node')) return;
      dragState = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        left: graphWrap.scrollLeft,
        top: graphWrap.scrollTop,
      };
      graphWrap.classList.add('dragging');
      graphWrap.setPointerCapture(event.pointerId);
    });
    graphWrap.addEventListener('pointermove', event => {
      if (!dragState) return;
      graphWrap.scrollLeft = dragState.left - (event.clientX - dragState.x);
      graphWrap.scrollTop = dragState.top - (event.clientY - dragState.y);
    });
    graphWrap.addEventListener('pointerup', endDrag);
    graphWrap.addEventListener('pointercancel', endDrag);

    function endDrag() {
      if (!dragState) return;
      try { graphWrap.releasePointerCapture(dragState.pointerId); } catch {}
      dragState = null;
      graphWrap.classList.remove('dragging');
    }

    layout();
    renderDetail(byId.get(activeId));
    if (activeId) highlight(activeId);
  </script>
</body>
</html>
"""


def build_list_experiment_rows(root: Path, index: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in index.get("experiments", []):
        if not isinstance(entry, dict):
            continue
        experiment_id = str(entry.get("experiment_id") or "")
        if not experiment_id:
            continue
        record = load_experiment_record(root, experiment_id)
        merged = dict(entry)
        if record:
            for key in ("parent_id", "title", "summary", "git_commit", "git_branch", "created_at"):
                if merged.get(key) in (None, "") and record.get(key) not in (None, ""):
                    merged[key] = record.get(key)
        rows.append(merged)
    return rows


def find_latest_experiment_record(root: Path) -> tuple[tuple[str, Path, dict[str, Any]] | None, list[str]]:
    records: list[tuple[tuple[str, int, str], str, Path, dict[str, Any]]] = []
    warnings: list[str] = []
    for record_path in sorted(experiments_path(root).glob(f"{EXPERIMENT_PREFIX}*/record.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Could not read {record_path}: {exc}")
            continue
        experiment_id = str(record.get("experiment_id") or record_path.parent.name)
        key = (
            str(record.get("created_at") or ""),
            parse_experiment_number(experiment_id),
            experiment_id,
        )
        records.append((key, experiment_id, record_path.parent, record))
    if not records:
        return None, warnings
    _, experiment_id, experiment_dir, record = max(records, key=lambda item: item[0])
    return (experiment_id, experiment_dir, record), warnings


def find_child_experiments(root: Path, parent_id: str) -> list[str]:
    children: list[str] = []
    for record_path in sorted(experiments_path(root).glob(f"{EXPERIMENT_PREFIX}*/record.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("parent_id") == parent_id:
            children.append(str(record.get("experiment_id") or record_path.parent.name))
    return children


def remove_experiment_dir(path: Path) -> None:
    def retry_after_chmod(function: Any, failed_path: str, exc_info: Any) -> None:
        try:
            os.chmod(failed_path, 0o700)
            function(failed_path)
        except OSError:
            raise exc_info[1]

    shutil.rmtree(path, onerror=retry_after_chmod)


def load_experiment_record(root: Path, experiment_id: str) -> dict[str, Any] | None:
    record_json_path = experiments_path(root) / experiment_id / "record.json"
    if not record_json_path.exists():
        return None
    try:
        data = json.loads(record_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def print_experiment_table(experiments: list[dict[str, Any]]) -> None:
    headers = ["ID", "Created", "Parent", "Branch", "Commit", "Title"]
    rows = [
        [
            str(item.get("experiment_id") or ""),
            format_list_datetime(item.get("created_at")),
            str(item.get("parent_id") or "-"),
            truncate_table_text(str(item.get("git_branch") or "-"), 18),
            short_commit(item.get("git_commit")),
            truncate_table_text(str(item.get("title") or item.get("summary") or ""), 54),
        ]
        for item in experiments
    ]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))


def format_list_datetime(value: Any) -> str:
    if not value:
        return "-"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return truncate_table_text(text, 16)
    return parsed.strftime("%Y-%m-%d %H:%M")


def short_commit(value: Any) -> str:
    if not value:
        return "-"
    text = str(value)
    return text[:7]


def truncate_table_text(text: str, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    if max_chars <= 3:
        return compact[:max_chars]
    return compact[: max_chars - 3] + "..."


def update_index(index: dict[str, Any], record: dict[str, Any]) -> None:
    summary_entry = {
        "experiment_id": record["experiment_id"],
        "parent_id": record["parent_id"],
        "title": record["title"],
        "summary": record["summary"],
        "git_commit": record["git_commit"],
        "git_branch": record["git_branch"],
        "created_at": record["created_at"],
    }
    index.setdefault("experiments", []).append(summary_entry)
    commit = record.get("git_commit")
    if commit:
        commit_index = index.setdefault("commit_index", {})
        commit_index.setdefault(commit, []).append(record["experiment_id"])


def rebuild_index_from_records(root: Path) -> tuple[dict[str, Any], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()

    for record_path in sorted(experiments_path(root).glob(f"{EXPERIMENT_PREFIX}*/record.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Could not read {record_path}: {exc}")
            continue

        experiment_id = str(record.get("experiment_id") or record_path.parent.name)
        if experiment_id in seen_ids:
            duplicate_ids.add(experiment_id)
            continue
        seen_ids.add(experiment_id)
        records.append(record)

    if duplicate_ids:
        warnings.append("Duplicate experiment ID(s): " + ", ".join(sorted(duplicate_ids)))

    records.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("experiment_id") or "")))
    experiments: list[dict[str, Any]] = []
    commit_index: dict[str, list[str]] = {}
    max_number = 0
    known_ids = {str(record.get("experiment_id") or "") for record in records if record.get("experiment_id")}

    for record in records:
        experiment_id = str(record.get("experiment_id") or "")
        if not experiment_id:
            warnings.append("Record without experiment_id skipped.")
            continue
        max_number = max(max_number, parse_experiment_number(experiment_id))
        parent_id = record.get("parent_id")
        if parent_id and str(parent_id) not in known_ids:
            warnings.append(f"{experiment_id} references missing parent {parent_id}.")
        summary_entry = {
            "experiment_id": experiment_id,
            "parent_id": parent_id,
            "title": record.get("title", ""),
            "summary": record.get("summary", ""),
            "git_commit": record.get("git_commit"),
            "git_branch": record.get("git_branch"),
            "created_at": record.get("created_at", ""),
        }
        experiments.append(summary_entry)
        commit = record.get("git_commit")
        if commit:
            commit_index.setdefault(str(commit), []).append(experiment_id)

    return {
        "next_id": max_number + 1,
        "experiments": experiments,
        "commit_index": commit_index,
    }, warnings


def parse_experiment_number(experiment_id: str) -> int:
    if not experiment_id.startswith(EXPERIMENT_PREFIX):
        return 0
    suffix = experiment_id[len(EXPERIMENT_PREFIX) :]
    try:
        return int(suffix)
    except ValueError:
        return 0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_chinese(language: str) -> bool:
    normalized = language.strip().lower()
    return normalized in {"zh", "zh-cn", "zh-hans", "chinese", "simplified chinese", "中文", "简体中文"}
