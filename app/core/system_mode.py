"""Regras da máquina de estados do modo do sistema (Fases 10/11).

`SystemMode` (`app/core/enums.py`) existe desde a Fase 1, mas nenhuma
fase anterior validava ou persistia transições de fato. A Fase 10
("paper trading") foi o primeiro ponto em que o modo passou a ter efeito
real: um comando de paper trading só roda se o sistema estiver no modo
`PAPER`. A Fase 11 ("executor em conta demo") estende o avanço até
`DEMO` — a maquinaria que o torna seguro (motor de risco com poder de
veto, `app.risk`; máquina de estados de ordem, `app.execution.
order_state`; bloqueio incondicional de conta não-demo em `app.mt5.
orders.send_market_order`) já existe a partir desta fase.

Este módulo é deliberadamente livre de acesso a banco de dados (não
importa `app.database`) para poder ser testado em isolamento e evitar
import circular — a orquestração que de fato lê/grava o modo persistido
e escreve auditoria vive em
`app.database.repositories.system_setting_repository`.

Regras impostas (não apenas convenção):
- Avanço permitido apenas um passo por vez, na ordem
  `DISABLED -> DATA_ONLY -> BACKTEST -> REPLAY -> PAPER -> DEMO` — nunca
  pulando estado intermediário.
- Retroceder para qualquer estado anterior é sempre permitido (parar/
  resetar o sistema não deveria exigir "desfazer" passo a passo).
- `EMERGENCY_STOP` é alcançável a partir de qualquer estado ativo
  (nunca a partir de `DISABLED`, que já é o estado mais seguro).
- A partir de `EMERGENCY_STOP`, só é permitido voltar para `DISABLED`
  (reset manual) — nunca retomar diretamente de onde parou.
- `REAL_LOCKED` e `REAL_ENABLED` permanecem bloqueados incondicionalmente
  até que toda a confirmação manual multi-etapa exigida pelo prompt
  mestre (seção 2: chave de liberação, prazo de expiração, valor máximo
  diário, lista de símbolos autorizados etc.) esteja implementada —
  ainda fora do escopo desta fase.
"""

from __future__ import annotations

from app.core.enums import SystemMode

FORWARD_ORDER: tuple[SystemMode, ...] = (
    SystemMode.DISABLED,
    SystemMode.DATA_ONLY,
    SystemMode.BACKTEST,
    SystemMode.REPLAY,
    SystemMode.PAPER,
    SystemMode.DEMO,
)

NOT_YET_IMPLEMENTED_MODES: frozenset[SystemMode] = frozenset(
    {SystemMode.REAL_LOCKED, SystemMode.REAL_ENABLED}
)


class SystemModeError(Exception):
    """Transição de modo inválida ou proibida nesta fase."""


def validate_transition(current: SystemMode, target: SystemMode) -> None:
    """Levanta `SystemModeError` se `current -> target` não for permitido.
    Não tem efeito colateral — apenas valida."""
    if target == current:
        raise SystemModeError(f"sistema já está em {target.value}.")

    if target in NOT_YET_IMPLEMENTED_MODES:
        raise SystemModeError(
            f"{target.value} ainda não implementado nesta fase (Fase 11+) — transição bloqueada."
        )

    if target == SystemMode.EMERGENCY_STOP:
        if current == SystemMode.DISABLED:
            raise SystemModeError(
                "EMERGENCY_STOP só pode ser acionado a partir de um estado ativo."
            )
        return

    if current == SystemMode.EMERGENCY_STOP:
        if target != SystemMode.DISABLED:
            raise SystemModeError("a partir de EMERGENCY_STOP só é permitido voltar para DISABLED.")
        return

    if current not in FORWARD_ORDER or target not in FORWARD_ORDER:
        raise SystemModeError(f"transição inválida: {current.value} -> {target.value}.")

    current_index = FORWARD_ORDER.index(current)
    target_index = FORWARD_ORDER.index(target)

    if target_index == current_index + 1:
        return
    if target_index < current_index:
        return

    raise SystemModeError(
        f"transição inválida: {current.value} -> {target.value} "
        "(pula estado(s) intermediário(s))."
    )
