"""Safe structured operational logging helpers.

Only explicitly approved scalar fields are emitted. Logging is best-effort and
must never change application or transaction behavior.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
from datetime import datetime, timezone

_CONTEXT = contextvars.ContextVar('factory_log_context', default={})
_FIELDS = frozenset({
    'request_id', 'tenant_id', 'project_id', 'task_id', 'run_id', 'agent',
    'provider', 'model', 'attempt', 'retryable', 'outcome', 'status',
    'error_type', 'duration_ms', 'http_method', 'http_route', 'http_status',
    'component', 'check',
})
_SECRET = re.compile(r'(?i)(password|passwd|secret|api[_-]?key|token|authorization)(\s*[=:]\s*)([^\s,;]+)')
_BEARER = re.compile(r'(?i)bearer\s+[A-Za-z0-9._~+/=-]+')
_DB_URL = re.compile(r'(?i)(postgres(?:ql)?://[^:/\s]+:)[^@/\s]+(@)')
_TOKEN_VALUE = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,})\b')


def _redact(value):
    value = _BEARER.sub('Bearer [REDACTED]', value)
    value = _SECRET.sub(r'\1\2[REDACTED]', value)
    value = _DB_URL.sub(r'\1[REDACTED]\2', value)
    return _TOKEN_VALUE.sub('[REDACTED]', value)


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record, with secret-like text redacted."""
    def format(self, record):
        try:
            message = _redact(record.getMessage())
            fields = dict(_CONTEXT.get())
            fields.update(getattr(record, 'obs_fields', {}) or {})
            payload = {
                'timestamp': datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                'severity': record.levelname,
                'component': str(fields.pop('component', record.name))[:100],
                'event': str(getattr(record, 'obs_event', record.msg))[:120],
                'message': message[:500],
            }
            for key, value in fields.items():
                if key not in _FIELDS or value is None or type(value) not in (str, int, float, bool):
                    continue
                if isinstance(value, str):
                    value = _redact(value)[:200]
                payload[key] = value
            return json.dumps(payload, ensure_ascii=True, separators=(',', ':'))
        except Exception:
            return '{"severity":"ERROR","component":"observability","event":"format_failed"}'


def configure_logging(level=logging.INFO):
    """Install JSON formatting on root handlers, without exposing configuration."""
    try:
        root = logging.getLogger()
        if not root.handlers:
            handler = logging.StreamHandler()
            root.addHandler(handler)
        for handler in root.handlers:
            handler.setFormatter(JsonFormatter())
        root.setLevel(level)
    except Exception:
        pass


def bind_log_context(**fields):
    """Bind allowlisted scalar correlation IDs for the current context."""
    safe = {k: v for k, v in fields.items() if k in _FIELDS and type(v) in (str, int, float, bool)}
    current = dict(_CONTEXT.get())
    current.update(safe)
    return _CONTEXT.set(current)


def reset_log_context(token):
    try:
        _CONTEXT.reset(token)
    except Exception:
        pass


def log_event(logger, level, event, **fields):
    """Best-effort event logger. Unknown fields and non-scalars are discarded."""
    try:
        safe = {k: v for k, v in fields.items() if k in _FIELDS and type(v) in (str, int, float, bool)}
        logger.log(level, str(event)[:120], extra={'obs_event': str(event)[:120], 'obs_fields': safe})
    except Exception:
        pass
