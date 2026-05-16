from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error, request


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass
class AIResult:
    ok: bool
    backend: str
    model: str | None
    output: dict[str, Any]
    rendered_prompt: str
    raw_response: dict[str, Any] | None
    error: str | None = None


def generate_structured_output(
    *,
    task_name: str,
    prompt_template: str,
    context: dict[str, str],
    schema: dict[str, Any],
    fallback_output: dict[str, Any],
    config: dict[str, Any],
) -> AIResult:
    rendered_prompt = render_template(prompt_template, context)
    backend = str(config.get("ai_backend", "auto"))

    if backend not in {"auto", "openai", "stub"}:
        return AIResult(
            ok=False,
            backend="invalid",
            model=None,
            output=fallback_output,
            rendered_prompt=rendered_prompt,
            raw_response=None,
            error=f"Unsupported ai_backend: {backend}",
        )

    if backend == "stub":
        return AIResult(
            ok=True,
            backend="stub",
            model=None,
            output=fallback_output,
            rendered_prompt=rendered_prompt,
            raw_response=None,
        )

    api_key = resolve_openai_api_key(config)
    if not api_key:
        return AIResult(
            ok=True,
            backend="stub",
            model=None,
            output=fallback_output,
            rendered_prompt=rendered_prompt,
            raw_response=None,
            error="OPENAI_API_KEY or openai_api_key is not set; used local fallback generator.",
        )

    model = str(config.get("openai_model", "gpt-5.4-mini"))
    timeout_seconds = int(config.get("openai_timeout_seconds", 60))
    raw_response, response_error = call_openai_structured_output(
        api_key=api_key,
        model=model,
        prompt=rendered_prompt,
        task_name=task_name,
        schema=schema,
        timeout_seconds=timeout_seconds,
        base_url=resolve_openai_responses_url(config),
    )
    if response_error is not None or raw_response is None:
        return AIResult(
            ok=True,
            backend="stub",
            model=model,
            output=fallback_output,
            rendered_prompt=rendered_prompt,
            raw_response=raw_response,
            error=response_error or "Unknown OpenAI error; used local fallback generator.",
        )

    text_payload = extract_output_text(raw_response)
    if text_payload is None:
        return AIResult(
            ok=True,
            backend="stub",
            model=model,
            output=fallback_output,
            rendered_prompt=rendered_prompt,
            raw_response=raw_response,
            error="OpenAI response did not contain parsable text; used local fallback generator.",
        )

    try:
        parsed = json.loads(text_payload)
    except json.JSONDecodeError as exc:
        return AIResult(
            ok=True,
            backend="stub",
            model=model,
            output=fallback_output,
            rendered_prompt=rendered_prompt,
            raw_response=raw_response,
            error=f"OpenAI response JSON parsing failed: {exc}; used local fallback generator.",
        )

    return AIResult(
        ok=True,
        backend="openai",
        model=model,
        output=parsed,
        rendered_prompt=rendered_prompt,
        raw_response=raw_response,
    )


def resolve_openai_responses_url(config: dict[str, Any]) -> str:
    base_url = os.getenv("OPENAI_BASE_URL") or str(config.get("openai_base_url", OPENAI_RESPONSES_URL))
    base_url = base_url.strip()
    if not base_url:
        return OPENAI_RESPONSES_URL
    if base_url.rstrip("/").endswith("/responses"):
        return base_url.rstrip("/")
    return f"{base_url.rstrip('/')}/responses"


def resolve_openai_api_key(config: dict[str, Any]) -> str:
    api_key = os.getenv("OPENAI_API_KEY") or str(config.get("openai_api_key", ""))
    return api_key.strip()


def render_template(template: str, context: dict[str, str]) -> str:
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", value)
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def call_openai_structured_output(
    *,
    api_key: str,
    model: str,
    prompt: str,
    task_name: str,
    schema: dict[str, Any],
    timeout_seconds: int,
    base_url: str,
) -> tuple[dict[str, Any] | None, str | None]:
    body = {
        "model": model,
        "input": [
            {
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You are ExpLine's semantic reporting engine. "
                            "Return JSON only and follow the provided schema exactly."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": task_name,
                "schema": schema,
                "strict": True,
            }
        },
    }
    payload = json.dumps(body).encode("utf-8")
    http_request = request.Request(
        base_url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        try:
            details = exc.read().decode("utf-8")
        except Exception:
            details = str(exc)
        return None, f"OpenAI API HTTP error {exc.code}: {details}"
    except error.URLError as exc:
        return None, f"OpenAI API connection error: {exc.reason}"
    except TimeoutError:
        return None, "OpenAI API request timed out."

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"OpenAI API returned invalid JSON: {exc}"
    return parsed, None


def extract_output_text(raw_response: dict[str, Any]) -> str | None:
    for item in raw_response.get("output", []):
        content = item.get("content", []) if isinstance(item, dict) else []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                return part["text"]
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                return part["text"]
    return None
