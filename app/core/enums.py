"""Enumeracoes centrais e compartilhadas do dominio.

Mantidas em um unico modulo para evitar ciclos de import entre camadas
(estrategias, risco, execucao, API todos referenciam estes enums).
"""

from __future__ import annotations

import enum


class SystemMode(enum.StrEnum):
    """Modo operacional global do sistema.

    Transicoes validas (impostas pela maquina de estados, nao apenas por
    convencao): DISABLED -> DATA_ONLY -> BACKTEST -> REPLAY -> PAPER -> DEMO
    -> REAL_LOCKED -> REAL_ENABLED, com EMERGENCY_STOP acessivel a partir de
    qualquer estado ativo. O sistema sempre inicia em DISABLED.
    """

    DISABLED = "DISABLED"
    DATA_ONLY = "DATA_ONLY"
    BACKTEST = "BACKTEST"
    REPLAY = "REPLAY"
    PAPER = "PAPER"
    DEMO = "DEMO"
    REAL_LOCKED = "REAL_LOCKED"
    REAL_ENABLED = "REAL_ENABLED"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class UserRole(enum.StrEnum):
    """Perfis de autorizacao de usuario do dashboard/API."""

    VIEWER = "VIEWER"
    ANALYST = "ANALYST"
    OPERATOR = "OPERATOR"
    RISK_MANAGER = "RISK_MANAGER"
    ADMIN = "ADMIN"
