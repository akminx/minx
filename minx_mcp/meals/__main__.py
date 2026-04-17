from __future__ import annotations

from minx_mcp.config import Settings
from minx_mcp.entrypoint import run_domain_server
from minx_mcp.meals.server import create_meals_server
from minx_mcp.meals.service import MealsService


def _create(settings: Settings) -> object:
    service = MealsService(settings.db_path, vault_root=settings.vault_path)
    return create_meals_server(service)


if __name__ == "__main__":
    run_domain_server(_create)
