from __future__ import annotations

from minx_mcp.config import Settings
from minx_mcp.entrypoint import run_domain_server
from minx_mcp.training.server import create_training_server
from minx_mcp.training.service import TrainingService


def _create(settings: Settings) -> object:
    service = TrainingService(settings.db_path)
    return create_training_server(service)


if __name__ == "__main__":
    run_domain_server(_create)
