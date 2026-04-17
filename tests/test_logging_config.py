import logging

from minx_mcp.logging_config import configure_logging


def test_configure_logging_sets_json_handler():
    configure_logging(level="WARNING")
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.WARNING
