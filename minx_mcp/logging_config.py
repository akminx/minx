import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_MAX_LOG_MESSAGE_CHARS = 4096
_TRUNCATION_MARKER = "…[truncated]"

# Conservative: only scrub obvious header-style secret carriers (false positives in prose are unacceptable).
_SECRET_KV_RE = re.compile(
    r"(?i)(?P<prefix>\b(?:authorization|api[_-]?key|x-api-key|password)\s*[:=]\s*)(?P<val>[^\s,;]+)",
)
_BEARER_TOKEN_RE = re.compile(r"(?i)(?P<prefix>\bbearer\s+)(?P<val>[a-z0-9._\-+/=]{8,})")


def _truncate_log_text(text: str) -> str:
    if len(text) <= _MAX_LOG_MESSAGE_CHARS:
        return text
    keep = _MAX_LOG_MESSAGE_CHARS - len(_TRUNCATION_MARKER)
    if keep < 1:
        return _TRUNCATION_MARKER
    return text[:keep] + _TRUNCATION_MARKER


def _redact_sensitive_substrings(text: str) -> str:
    def _kv_sub(m: re.Match[str]) -> str:
        return f"{m.group('prefix')}[REDACTED]"

    out = _SECRET_KV_RE.sub(_kv_sub, text)
    out = _BEARER_TOKEN_RE.sub(_kv_sub, out)
    return out


def _finalize_payload_strings(payload: dict[str, Any]) -> None:
    if "msg" in payload and isinstance(payload["msg"], str):
        payload["msg"] = _redact_sensitive_substrings(_truncate_log_text(payload["msg"]))
    if "exc" in payload and isinstance(payload["exc"], str):
        payload["exc"] = _redact_sensitive_substrings(_truncate_log_text(payload["exc"]))


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        for key in ("tool", "duration_ms", "success", "error_code", "domain"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        secret_fields = getattr(record, "_secret_fields", None)
        if isinstance(secret_fields, list):
            for key in secret_fields:
                if isinstance(key, str) and getattr(record, key, None) is not None:
                    payload[key] = "[REDACTED]"
        _finalize_payload_strings(payload)
        return json.dumps(payload)


def configure_logging(*, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
