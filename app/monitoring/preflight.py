"""Checagens de prontidão operacional (Fase 15) — `python -m app.cli
preflight check`.

Ponto único que valida, antes de operar por um período estendido (paper
ou demo), tudo que já foi motivo de falha silenciosa em fases
anteriores: segredo padrão esquecido no `.env`, banco sem migrations
aplicadas, diretórios de artefato sem permissão de escrita, credenciais
MT5 ausentes. Nenhuma checagem aqui tenta *corrigir* nada — só relata,
com severidade explícita (`OK`/`WARN`/`FAIL`), para decisão humana.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings

_SECRET_KEY_PLACEHOLDER_PREFIX = "CHANGE_ME"
"""Prefixo usado tanto pelo padrao de `Settings.app_secret_key` quanto
pelo valor mostrado em `.env.example` (`CHANGE_ME_generate_with_openssl_
rand_hex_32`) — checar o prefixo, nao um valor exato, garante que a
checagem pega o segredo de exemplo copiado sem edicao, nao so o default
da classe."""


class CheckStatus(enum.StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    status: CheckStatus
    detail: str


def check_secret_key(settings: Settings) -> PreflightCheck:
    is_placeholder = settings.app_secret_key.startswith(_SECRET_KEY_PLACEHOLDER_PREFIX)
    if is_placeholder and not settings.is_test_env:
        return PreflightCheck(
            name="secret_key",
            status=CheckStatus.FAIL,
            detail=(
                "APP_SECRET_KEY continua no valor padrão do .env.example — "
                "gere um segredo real (ex.: openssl rand -hex 32) antes de operar."
            ),
        )
    return PreflightCheck(name="secret_key", status=CheckStatus.OK, detail="configurado.")


def check_database(session: Session) -> PreflightCheck:
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        return PreflightCheck(
            name="database", status=CheckStatus.FAIL, detail=f"banco inacessível: {exc}"
        )
    return PreflightCheck(name="database", status=CheckStatus.OK, detail="conexão ok.")


def check_migrations_current(session: Session, *, script_location: str) -> PreflightCheck:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    try:
        config = Config()
        config.set_main_option("script_location", script_location)
        script = ScriptDirectory.from_config(config)
        head_revision = script.get_current_head()
    except Exception as exc:
        return PreflightCheck(
            name="migrations",
            status=CheckStatus.WARN,
            detail=f"não foi possível ler as migrations locais: {exc}",
        )

    try:
        current_revision = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    except Exception:
        return PreflightCheck(
            name="migrations",
            status=CheckStatus.WARN,
            detail="tabela alembic_version não encontrada — rode 'alembic upgrade head'.",
        )

    if current_revision != head_revision:
        return PreflightCheck(
            name="migrations",
            status=CheckStatus.FAIL,
            detail=(
                f"banco na revisão {current_revision!r}, mas a mais recente é "
                f"{head_revision!r} — rode 'alembic upgrade head'."
            ),
        )
    return PreflightCheck(
        name="migrations", status=CheckStatus.OK, detail=f"atualizado ({head_revision})."
    )


def check_directory_writable(name: str, path_str: str) -> PreflightCheck:
    path = Path(path_str)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".preflight_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return PreflightCheck(
            name=name, status=CheckStatus.FAIL, detail=f"'{path}' não é gravável: {exc}"
        )
    return PreflightCheck(name=name, status=CheckStatus.OK, detail=f"'{path}' gravável.")


def check_mt5_credentials(settings: Settings) -> PreflightCheck:
    if not settings.mt5_login or not settings.mt5_password or not settings.mt5_server:
        return PreflightCheck(
            name="mt5_credentials",
            status=CheckStatus.WARN,
            detail=(
                "MT5_LOGIN/MT5_PASSWORD/MT5_SERVER não configurados — o conector "
                "tentará reutilizar a sessão já autenticada do terminal Windows."
            ),
        )
    return PreflightCheck(name="mt5_credentials", status=CheckStatus.OK, detail="configurado.")


def run_all_checks(settings: Settings, session: Session) -> list[PreflightCheck]:
    return [
        check_secret_key(settings),
        check_database(session),
        check_migrations_current(session, script_location="alembic"),
        check_directory_writable("log_dir", settings.log_dir),
        check_directory_writable("ml_models_dir", settings.ml_models_dir),
        check_directory_writable("ml_datasets_dir", settings.ml_datasets_dir),
        check_mt5_credentials(settings),
    ]


def worst_status(checks: list[PreflightCheck]) -> CheckStatus:
    if any(c.status == CheckStatus.FAIL for c in checks):
        return CheckStatus.FAIL
    if any(c.status == CheckStatus.WARN for c in checks):
        return CheckStatus.WARN
    return CheckStatus.OK
