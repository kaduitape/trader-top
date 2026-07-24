"""Logging estruturado em JSON.

Todo log de producao deve ser uma linha JSON com, no minimo, timestamp UTC,
nivel, modulo e evento — preparando o terreno para os campos adicionais
(correlation_id, signal_id, order_id, strategy_id, model_version, symbol)
que serao anexados a partir das fases que os introduzem (ver prompt mestre,
secao 25).

Campos cujo nome sugere segredo (password, secret, token, key, authorization)
sao mascarados antes de serem serializados, mesmo que alguem os inclua por
engano em um `extra={...}` de log.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SENSITIVE_SUBSTRINGS = ("password", "secret", "token", "key", "authorization")

_STANDARD_LOGRECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


def _mask_value(key: str, value: Any) -> Any:
    if any(marker in key.lower() for marker in _SENSITIVE_SUBSTRINGS):
        return "***MASKED***"
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "event": record.getMessage(),
        }

        extra_keys = set(record.__dict__) - _STANDARD_LOGRECORD_ATTRS
        for key in extra_keys:
            payload[key] = _mask_value(key, record.__dict__[key])

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    *, level: str = "INFO", log_dir: str = "logs", json_format: bool = True
) -> None:
    """Configura o logging raiz da aplicacao. Deve ser chamado uma unica vez,
    na inicializacao (`main.py` / criacao do app FastAPI)."""

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    formatter: logging.Formatter
    if json_format:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path / "app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
