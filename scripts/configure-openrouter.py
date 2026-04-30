#!/usr/bin/env python3
"""Configure Minx Core to use OpenRouter for chat + embeddings.

Writes the `core/llm_config` preference (read by minx_mcp.core.llm.create_llm)
and prints the env-var settings the user needs for embeddings. The chat model
is deployment configuration; current runbooks pass `--model
google/gemini-2.5-flash` explicitly.

Usage:
    OPENROUTER_API_KEY=sk-or-v1-... uv run scripts/configure-openrouter.py --model google/gemini-2.5-flash
    OPENROUTER_API_KEY=... uv run scripts/configure-openrouter.py --model <openrouter-model-id>
    uv run scripts/configure-openrouter.py --print  # show what would be written, don't write

Re-run any time you want to change the model or provider preferences. The
preference write is idempotent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from minx_mcp.config import get_settings  # noqa: E402
from minx_mcp.db import get_connection  # noqa: E402
from minx_mcp.preferences import set_preference  # noqa: E402

DEFAULT_MODEL = "google/gemini-2.5-flash"
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"


def build_llm_config(
    *,
    model: str,
    api_key_env: str,
    timeout_seconds: float,
    data_collection: str,
    zdr: bool,
    require_parameters: bool,
    quantizations: list[str] | None,
    reasoning_effort: str | None,
) -> dict[str, object]:
    provider_preferences: dict[str, object] = {
        "data_collection": data_collection,
        "zdr": zdr,
        "require_parameters": require_parameters,
        "allow_fallbacks": True,
    }
    if quantizations:
        provider_preferences["quantizations"] = quantizations

    config: dict[str, object] = {
        "provider": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "model": model,
        "api_key_env": api_key_env,
        "timeout_seconds": timeout_seconds,
        "provider_preferences": provider_preferences,
    }
    if reasoning_effort:
        config["reasoning"] = {"effort": reasoning_effort}
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument(
        "--data-collection",
        choices=("deny", "allow"),
        default="deny",
        help="OpenRouter provider routing: deny = only no-logging providers.",
    )
    parser.add_argument(
        "--no-zdr",
        action="store_true",
        help="Disable OpenRouter Zero Data Retention endpoint enforcement.",
    )
    parser.add_argument("--no-require-parameters", action="store_true")
    parser.add_argument(
        "--quantizations",
        default="",
        help="Comma-separated OpenRouter quantization hints; empty disables the hint.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "off"),
        default="medium",
    )
    parser.add_argument("--print", dest="print_only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    quantizations = (
        [q.strip() for q in args.quantizations.split(",") if q.strip()]
        if args.quantizations
        else None
    )
    reasoning_effort = None if args.reasoning_effort == "off" else args.reasoning_effort

    config = build_llm_config(
        model=args.model,
        api_key_env=args.api_key_env,
        timeout_seconds=args.timeout_seconds,
        data_collection=args.data_collection,
        zdr=not args.no_zdr,
        require_parameters=not args.no_require_parameters,
        quantizations=quantizations,
        reasoning_effort=reasoning_effort,
    )

    print("LLM config to write into preferences[core/llm_config]:")
    print(json.dumps(config, indent=2))
    print()
    print("Embedding settings (set these as env vars wherever Core / sweepers run):")
    settings = get_settings()
    print("  MINX_OPENROUTER_API_KEY  = (from env at runtime)")
    print(f"  MINX_EMBEDDING_MODEL     = {args.embedding_model}")
    print("  MINX_EMBEDDING_DIMENSIONS= 512   # recommended for memory")
    print(f"  MINX_EMBEDDING_REQUEST_TIMEOUT_S = {settings.embedding_request_timeout_s}")
    print(f"  MINX_EMBEDDING_MAX_COST_MICROUSD = {settings.embedding_max_cost_microusd}")
    print()
    print(f"Chat API key env var: {args.api_key_env}")
    if not os.environ.get(args.api_key_env):
        print(f"  WARNING: {args.api_key_env} is not set in this shell.")
    print()

    if args.print_only:
        print("(--print mode: not writing to DB)")
        return 0

    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        set_preference(conn, "core", "llm_config", config)
    print(f"Wrote preference 'core/llm_config' to {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
