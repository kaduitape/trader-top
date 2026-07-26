"""Risk Manager dinamico: proteger primeiro, otimizar depois.

O que este modulo **nunca** faz, por decisao de arquitetura e nao por
configuracao — nao existe parametro que ligue nada disto:

- martingale (aumentar lote apos perda);
- grid (empilhar posicoes contra o movimento);
- dobrar lote em qualquer circunstancia;
- "recuperar" o prejuizo do dia com uma operacao maior.

O dimensionamento por risco fixo continua inteiramente em
`app.risk.position_sizing`/`app.risk.engine`, que ja tem poder de veto
independente. Aqui ficam apenas as decisoes que dependem do
ACOMPANHAMENTO da posicao aberta e do estado do dia.

Todas as funcoes sao puras: recebem numeros, devolvem uma INTENCAO. Quem
executa (envia a modificacao ao MetaTrader) e a camada de execucao — assim
o gerenciamento pode ser testado sem terminal e simulado em backtest.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.apexflow.config import ApexFlowConfig
from app.apexflow.volatility import VolatilityReading
from app.strategies.base import SignalDirection


class StopMoveKind(enum.StrEnum):
    NONE = "NONE"
    BREAK_EVEN = "BREAK_EVEN"
    TRAILING = "TRAILING"


@dataclass(frozen=True, slots=True)
class StopIntent:
    """Para onde o stop DEVERIA ir, e por que. Nao move nada sozinho."""

    kind: StopMoveKind
    new_stop_loss: float | None
    current_r: float
    reason: str

    @property
    def should_move(self) -> bool:
        return self.kind != StopMoveKind.NONE and self.new_stop_loss is not None


class TradingHaltReason(enum.StrEnum):
    NONE = "NONE"
    DAILY_LOSS = "DAILY_LOSS"
    DAILY_PROFIT = "DAILY_PROFIT"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    DRAWDOWN = "DRAWDOWN"


HALT_LABELS: dict[TradingHaltReason, str] = {
    TradingHaltReason.NONE: "Operacao liberada",
    TradingHaltReason.DAILY_LOSS: "Limite diario de perda atingido",
    TradingHaltReason.DAILY_PROFIT: "Meta diaria de lucro atingida",
    TradingHaltReason.CONSECUTIVE_LOSSES: "Perdas consecutivas no limite",
    TradingHaltReason.DRAWDOWN: "Drawdown maximo atingido",
}


@dataclass(frozen=True, slots=True)
class TradingHalt:
    reason: TradingHaltReason
    detail: str

    @property
    def is_halted(self) -> bool:
        return self.reason != TradingHaltReason.NONE

    @property
    def label(self) -> str:
        return HALT_LABELS[self.reason]


def compute_r_multiple(
    *,
    direction: SignalDirection,
    entry_price: float,
    current_price: float,
    stop_loss: float,
) -> float | None:
    """Quanto a operacao andou, medido em multiplos do risco inicial (R).

    R e a unica unidade que compara operacoes de pares e tamanhos
    diferentes. `None` quando o risco inicial e zero (stop no preco de
    entrada), situacao que nunca deveria existir e nunca e mascarada.
    """
    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None
    moved = (
        current_price - entry_price
        if direction == SignalDirection.LONG
        else entry_price - current_price
    )
    return moved / risk


def dynamic_take_profit(
    *,
    direction: SignalDirection,
    entry_price: float,
    stop_loss: float,
    volatility: VolatilityReading,
    point: float,
    config: ApexFlowConfig,
) -> float:
    """Alvo proporcional ao risco, esticado quando a volatilidade permite.

    Alvo fixo em pontos ignora que o mesmo par tem amplitudes diferentes
    em horarios diferentes. Aqui o alvo parte de `risk_reward_min` e so
    estica quando a volatilidade atual esta acima da media — nunca encolhe
    abaixo do minimo configurado.
    """
    risk = abs(entry_price - stop_loss)
    multiple = config.risk_reward_min
    if volatility.atr_ratio is not None and volatility.atr_ratio > 1.0:
        multiple *= min(2.0, volatility.atr_ratio)

    # O alvo nunca fica menor que o minimo em pontos que paga o custo.
    distance = max(risk * multiple, config.min_atr_points * point)
    return (
        entry_price + distance
        if direction == SignalDirection.LONG
        else entry_price - distance
    )


def evaluate_stop_move(
    *,
    direction: SignalDirection,
    entry_price: float,
    current_price: float,
    stop_loss: float,
    config: ApexFlowConfig,
) -> StopIntent:
    """Decide entre nao mexer, ir para o zero a zero, ou seguir o preco.

    Duas regras invioláveis, ambas testadas:

    1. **O stop nunca anda para tras.** Uma modificacao que aumentaria o
       risco e recusada, mesmo que a formula a proponha.
    2. **Break-even antes de trailing.** Tirar o risco da mesa vem primeiro;
       so depois o trailing passa a perseguir o lucro.
    """
    current_r = compute_r_multiple(
        direction=direction,
        entry_price=entry_price,
        current_price=current_price,
        stop_loss=stop_loss,
    )
    if current_r is None:
        return StopIntent(
            kind=StopMoveKind.NONE,
            new_stop_loss=None,
            current_r=0.0,
            reason="Risco inicial nulo — nenhuma movimentacao pode ser calculada.",
        )

    risk = abs(entry_price - stop_loss)
    is_long = direction == SignalDirection.LONG

    def improves(candidate: float) -> bool:
        return candidate > stop_loss if is_long else candidate < stop_loss

    if current_r >= config.trailing_start_r:
        steps = int((current_r - config.trailing_start_r) / config.trailing_step_r)
        locked_r = steps * config.trailing_step_r
        candidate = (
            entry_price + locked_r * risk if is_long else entry_price - locked_r * risk
        )
        if improves(candidate):
            return StopIntent(
                kind=StopMoveKind.TRAILING,
                new_stop_loss=candidate,
                current_r=current_r,
                reason=(
                    f"Operacao a {current_r:.2f}R: trailing trava {locked_r:.2f}R "
                    "de lucro."
                ),
            )

    if current_r >= config.break_even_r and improves(entry_price):
        return StopIntent(
            kind=StopMoveKind.BREAK_EVEN,
            new_stop_loss=entry_price,
            current_r=current_r,
            reason=(
                f"Operacao a {current_r:.2f}R (minimo {config.break_even_r:.2f}R): "
                "stop vai para o zero a zero, tirando o risco da mesa."
            ),
        )

    return StopIntent(
        kind=StopMoveKind.NONE,
        new_stop_loss=None,
        current_r=current_r,
        reason=(
            f"Operacao a {current_r:.2f}R — ainda nao ha lucro suficiente para "
            "mover o stop sem estrangular a operacao."
        ),
    )


def evaluate_trading_halt(
    *,
    day_start_balance: float,
    current_equity: float,
    daily_pnl: float,
    consecutive_losses: int,
    max_consecutive_losses: int,
    peak_equity: float | None = None,
    config: ApexFlowConfig,
    max_daily_loss_pct: float,
) -> TradingHalt:
    """Diz se o dia acabou para o robo — por perda, por lucro ou por
    drawdown.

    O limite de LUCRO existe pelo mesmo motivo do de perda: depois de um
    dia bom, a tendencia humana (e a de um robo mal calibrado) e devolver
    o resultado tentando repeti-lo. Bater a meta e um motivo legitimo para
    parar.
    """
    if day_start_balance <= 0:
        return TradingHalt(
            reason=TradingHaltReason.NONE,
            detail="Saldo inicial do dia indisponivel — nenhum limite pode ser medido.",
        )

    loss_pct = -daily_pnl / day_start_balance * 100
    profit_pct = daily_pnl / day_start_balance * 100

    if loss_pct >= max_daily_loss_pct:
        return TradingHalt(
            reason=TradingHaltReason.DAILY_LOSS,
            detail=(
                f"Perda do dia em {loss_pct:.2f}% do saldo inicial (limite "
                f"{max_daily_loss_pct:.2f}%). Nenhuma entrada nova ate amanha."
            ),
        )

    if consecutive_losses >= max_consecutive_losses:
        return TradingHalt(
            reason=TradingHaltReason.CONSECUTIVE_LOSSES,
            detail=(
                f"{consecutive_losses} perdas consecutivas (limite "
                f"{max_consecutive_losses}) — pausa obrigatoria."
            ),
        )

    reference_peak = peak_equity if peak_equity is not None else day_start_balance
    if reference_peak > 0:
        drawdown_pct = (reference_peak - current_equity) / reference_peak * 100
        if drawdown_pct >= config.max_drawdown_pct:
            return TradingHalt(
                reason=TradingHaltReason.DRAWDOWN,
                detail=(
                    f"Drawdown de {drawdown_pct:.2f}% desde o pico (limite "
                    f"{config.max_drawdown_pct:.2f}%)."
                ),
            )

    if profit_pct >= config.daily_profit_target_pct:
        return TradingHalt(
            reason=TradingHaltReason.DAILY_PROFIT,
            detail=(
                f"Meta diaria de {config.daily_profit_target_pct:.2f}% atingida "
                f"({profit_pct:.2f}%). Parar aqui protege o resultado do dia."
            ),
        )

    return TradingHalt(
        reason=TradingHaltReason.NONE,
        detail=(
            f"Resultado do dia em {profit_pct:+.2f}%, {consecutive_losses} perda(s) "
            "consecutiva(s) — dentro de todos os limites."
        ),
    )
