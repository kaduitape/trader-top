"""Estrategia do piloto automatico: contexto aprovado + gatilho do operacional.

Um operador profissional precisa de duas coisas para entrar, e nunca de uma
so: **permissao** (o contexto justifica operar este par agora?) e **timing**
(o setup apareceu nesta barra?). Este adaptador impoe exatamente isso:

- a **permissao** vem do motor de analise (`app.services.analysis_service`),
  que ja carrega os gates profissionais — score minimo, cobertura dos nove
  timeframes, noticias/fundamentos confirmados, volume favoravel — e
  entrega os niveis de entrada/stop/alvo calculados por estrutura;
- o **timing** vem da estrategia eleita pelo seletor de operacional
  (`app.execution.playbook`), uma das ja implementadas e testadas em
  `app.strategies.registry`.

Sem as duas, nao ha sinal. Quando so uma acontece, o motivo fica registrado
em `last_block_reason` para o status ao vivo poder dizer ao operador o que
faltou — "analise aprovou, aguardando o gatilho" e uma informacao util,
nao um silencio.

Os niveis executados sao SEMPRE os da analise (stop por estrutura, alvo por
R multiplo ja validado), nunca os do gatilho: o gatilho responde "agora",
nao "onde".
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.execution.analysis_strategy import AnalysisReportStrategy
from app.services.analysis_service import AnalysisReport
from app.strategies.base import MarketState, Signal, Strategy

AUTOPILOT_STRATEGY_NAME = "autopilot"
"""Nome unico e ESTAVEL, independente do operacional eleito no ciclo.

O motor de execucao usa o nome da estrategia para cursor, contadores do dia
e busca de posicao aberta. Se o nome mudasse junto com o operacional, cada
troca de playbook zeraria os limites de risco — por isso o operacional
eleito viaja no `reason`/`features_used` do sinal, nunca no nome."""


class PlaybookConfluenceStrategy(Strategy):
    """Emite no maximo um sinal por ciclo, so com dupla confirmacao."""

    name = AUTOPILOT_STRATEGY_NAME

    def __init__(
        self,
        report: AnalysisReport,
        *,
        trigger: Strategy,
        expected_open_time: datetime,
        playbook_label: str,
        playbook_kind: str,
        fit_score: float,
    ) -> None:
        self._analysis = AnalysisReportStrategy(report, expected_open_time=expected_open_time)
        self._trigger = trigger
        self._report = report
        self._playbook_label = playbook_label
        self._playbook_kind = playbook_kind
        self._fit_score = fit_score
        self.last_block_reason: str | None = None
        self.context_approved = False
        self.trigger_fired = False

    def generate_signal(self, state: MarketState) -> Signal | None:
        context_signal = self._analysis.generate_signal(state)
        trigger_signal = self._trigger.generate_signal(state)
        self.trigger_fired = self.trigger_fired or trigger_signal is not None
        self.context_approved = self.context_approved or context_signal is not None

        if context_signal is None:
            if trigger_signal is not None:
                self.last_block_reason = (
                    f"Gatilho de {self._playbook_label} apareceu, mas a analise nao "
                    f"aprovou o contexto (score {self._report.score.total_score:.1f} / "
                    f"minimo {self._report.score.threshold:.0f})."
                )
            return None

        if trigger_signal is None:
            self.last_block_reason = (
                f"Contexto aprovado (score {self._report.score.total_score:.1f}), "
                f"aguardando o gatilho de {self._playbook_label}."
            )
            return None

        if trigger_signal.direction != context_signal.direction:
            self.last_block_reason = (
                f"Analise aponta {context_signal.direction.value} e o gatilho de "
                f"{self._playbook_label} aponta {trigger_signal.direction.value} — "
                "sem acordo, nenhuma ordem e enviada."
            )
            return None

        self.last_block_reason = None
        generated_at = self._report.generated_at
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)

        return replace(
            context_signal,
            strategy_name=self.name,
            reason=(
                f"{self._playbook_label}: gatilho {trigger_signal.strategy_name} "
                f"confirmou o contexto aprovado pela analise (score "
                f"{self._report.score.total_score:.1f}, aderencia do operacional "
                f"{self._fit_score:.0f}/100). {trigger_signal.reason}"
            )[:1000],
            valid_until=generated_at + timedelta(minutes=15),
            features_used={
                **context_signal.features_used,
                "playbook_fit_score": self._fit_score,
            },
            model_version=f"autopilot:{self._playbook_kind}",
        )
