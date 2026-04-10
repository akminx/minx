from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def log_interpretation_failure(
    *,
    task: str,
    prompt_summary: str,
    error: Exception,
) -> None:
    logger.warning(
        "interpretation_failed task=%s summary=%s error=%s",
        task,
        prompt_summary,
        error,
    )
