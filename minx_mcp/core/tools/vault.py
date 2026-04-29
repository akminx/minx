"""Vault read/write MCP tools and wiki-template resources.

Also registers ``health://status`` since it's a tiny cross-cutting resource
that doesn't merit its own module. If additional cross-cutting resources
emerge, split them into a dedicated ``tools/resources.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from minx_mcp.audit import log_sensitive_access
from minx_mcp.contracts import (
    ConflictError,
    InvalidInputError,
    ToolResponse,
    wrap_tool_call,
)
from minx_mcp.core.tools._shared import CoreServiceConfig
from minx_mcp.core.vault_reconciler import VaultReconciler
from minx_mcp.core.vault_scanner import VaultScanner
from minx_mcp.db import scoped_connection
from minx_mcp.transport import health_payload
from minx_mcp.vault_reader import VaultReader
from minx_mcp.vault_writer import VaultWriter

__all__ = ["register_vault_tools"]


_WIKI_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "wiki"
_WIKI_TEMPLATE_NAMES = ["entity", "pattern", "review", "goal", "memory"]


def register_vault_tools(mcp: FastMCP, config: CoreServiceConfig) -> None:
    @mcp.resource("health://status")
    def health_status() -> str:
        return health_payload("minx-core")

    @mcp.tool(name="persist_note")
    def persist_note(
        relative_path: str,
        content: str,
        overwrite: bool = False,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _persist_note(config, relative_path, content, overwrite),
            tool_name="persist_note",
        )

    @mcp.tool(name="vault_replace_section")
    def vault_replace_section(
        relative_path: str,
        heading: str,
        body: str,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _vault_replace_section(config, relative_path, heading, body),
            tool_name="vault_replace_section",
        )

    @mcp.tool(name="vault_replace_frontmatter")
    def vault_replace_frontmatter(
        relative_path: str,
        frontmatter: dict[str, object],
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _vault_replace_frontmatter(config, relative_path, frontmatter),
            tool_name="vault_replace_frontmatter",
        )

    @mcp.tool(name="vault_scan")
    def vault_scan(dry_run: bool = False) -> ToolResponse:
        return wrap_tool_call(
            lambda: _vault_scan(config, dry_run),
            tool_name="vault_scan",
        )

    @mcp.tool(name="vault_reconcile_memories")
    def vault_reconcile_memories(dry_run: bool = False) -> ToolResponse:
        return wrap_tool_call(
            lambda: _vault_reconcile_memories(config, dry_run),
            tool_name="vault_reconcile_memories",
        )

    @mcp.resource("wiki-templates://list")
    def wiki_templates_list() -> str:
        return json.dumps(_WIKI_TEMPLATE_NAMES)

    @mcp.resource("wiki-templates://{name}")
    def wiki_template(name: str) -> str:
        if name not in _WIKI_TEMPLATE_NAMES:
            raise InvalidInputError(f"unknown wiki template: {name!r}")
        return (_WIKI_TEMPLATE_DIR / f"{name}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _persist_note(
    config: CoreServiceConfig,
    relative_path: str,
    content: str,
    overwrite: bool,
) -> dict[str, object]:
    writer = VaultWriter(config.vault_path, ("Minx",))
    resolved = writer.resolve_path(relative_path)
    existed = resolved.exists()
    if existed and not overwrite:
        raise ConflictError("note already exists", data={"path": str(resolved)})
    writer.write_markdown(relative_path, content)
    return {"path": str(resolved), "overwritten" if existed else "created": True}


def _vault_replace_section(
    config: CoreServiceConfig,
    relative_path: str,
    heading: str,
    body: str,
) -> dict[str, object]:
    writer = VaultWriter(config.vault_path, ("Minx",))
    resolved = writer.replace_section(relative_path, heading, body)
    return {"path": str(resolved)}


def _vault_replace_frontmatter(
    config: CoreServiceConfig,
    relative_path: str,
    frontmatter: dict[str, object],
) -> dict[str, object]:
    if not isinstance(frontmatter, dict):
        raise InvalidInputError("frontmatter must be an object")
    writer = VaultWriter(config.vault_path, ("Minx",))
    resolved = writer.replace_frontmatter(relative_path, frontmatter)
    return {"path": str(resolved)}


def _vault_scan(config: CoreServiceConfig, dry_run: bool = False) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        log_sensitive_access(
            conn,
            "vault_scan",
            None,
            f"vault_scan dry_run={dry_run}",
        )
        scanner = VaultScanner(
            conn,
            VaultReader(config.vault_path, ("Minx",)),
            scope_prefix="Minx",
        )
        return {"report": scanner.scan(dry_run=dry_run).as_dict()}


def _vault_reconcile_memories(
    config: CoreServiceConfig,
    dry_run: bool = False,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        log_sensitive_access(
            conn,
            "vault_reconcile_memories",
            None,
            f"vault_reconcile_memories dry_run={dry_run}",
        )
        reconciler = VaultReconciler(
            conn,
            VaultReader(config.vault_path, ("Minx",)),
            VaultWriter(config.vault_path, ("Minx",)),
            scope_prefix="Minx",
        )
        return {"report": reconciler.reconcile(dry_run=dry_run).as_dict()}
