"""Critérios formais de aprovação/rejeição de um modelo (Fase 9).

Aplica os 5 critérios do prompt mestre (seção 12, documentados em
`docs/ml.md`) sobre um `MLWalkForwardReport` (Fase 9) em vez de uma
única divisão (Fase 8) — "estável entre períodos" só pode ser julgado
com múltiplas janelas.

**Nunca aprova nada automaticamente.** Esta função só produz um
relatório com veredito por critério; a decisão de fato (`--approve` na
CLI, que grava `approved=True` no registro) continua sendo manual,
sempre — mesmo que todos os critérios passem aqui.

Simplificação documentada: o critério "supera o baseline" é avaliado
como "expectativa por trade positiva depois de custos" (baseline
implícito = 0, ou seja, não operar) — comparar diretamente contra a
expectativa da própria estratégia SEM o filtro de IA, nas mesmas
janelas, é uma comparação mais rigorosa e fica para quando essa
integração (modelo como filtro de sinal) existir de fato, na Fase 11+.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ml.walk_forward import MLWalkForwardReport


@dataclass(frozen=True, slots=True)
class ApprovalCriterion:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ApprovalReport:
    criteria: list[ApprovalCriterion]

    @property
    def all_passed(self) -> bool:
        return len(self.criteria) > 0 and all(c.passed for c in self.criteria)


def evaluate_approval(
    report: MLWalkForwardReport,
    *,
    min_trades_total: int = 30,
    min_profitable_window_ratio: float = 0.6,
    max_expectancy_relative_std: float = 2.0,
    max_mean_brier_score: float = 0.25,
) -> ApprovalReport:
    if not report.windows:
        return ApprovalReport(
            criteria=[
                ApprovalCriterion(
                    name="janelas_disponiveis",
                    passed=False,
                    detail="nenhuma janela de walk-forward produziu resultado avaliável.",
                )
            ]
        )

    criteria: list[ApprovalCriterion] = []

    total_trades = sum(w.trading_metrics.num_trades for w in report.windows)
    criteria.append(
        ApprovalCriterion(
            name="numero_de_trades_suficiente",
            passed=total_trades >= min_trades_total,
            detail=(
                f"{total_trades} trade(s) no total das janelas de teste "
                f"(mínimo: {min_trades_total})."
            ),
        )
    )

    criteria.append(
        ApprovalCriterion(
            name="edge_positivo_apos_custos",
            passed=report.mean_expectancy_after_costs > 0,
            detail=(
                f"expectativa média após custos entre janelas: "
                f"{report.mean_expectancy_after_costs:.6f} (baseline implícito: 0)."
            ),
        )
    )

    criteria.append(
        ApprovalCriterion(
            name="estavel_entre_periodos",
            passed=report.profitable_window_ratio >= min_profitable_window_ratio,
            detail=(
                f"{report.profitable_window_ratio:.0%} das janelas de teste tiveram "
                f"expectativa >= 0 (mínimo: {min_profitable_window_ratio:.0%})."
            ),
        )
    )

    if report.mean_expectancy_after_costs != 0:
        relative_std = abs(report.std_expectancy_after_costs / report.mean_expectancy_after_costs)
        not_erratic = relative_std <= max_expectancy_relative_std
        relative_std_detail = f"{relative_std:.2f}"
    else:
        not_erratic = False
        relative_std_detail = "indefinido (expectativa média = 0)"
    criteria.append(
        ApprovalCriterion(
            name="nao_dependente_de_janela_excepcional",
            passed=not_erratic,
            detail=(
                f"desvio-padrão relativo da expectativa entre janelas: {relative_std_detail} "
                f"(máximo: {max_expectancy_relative_std})."
            ),
        )
    )

    brier_scores = [
        w.classification_metrics.brier_score
        for w in report.windows
        if w.classification_metrics.brier_score is not None
    ]
    if brier_scores:
        mean_brier = sum(brier_scores) / len(brier_scores)
        calibrated_ok = mean_brier < max_mean_brier_score
        brier_detail = (
            f"Brier score médio entre janelas: {mean_brier:.4f} (limite: {max_mean_brier_score}). "
            "Heurística aproximada — inspecione a curva de calibração real "
            "(`app.ml.calibration.compute_calibration_curve`) antes de aprovar."
        )
    else:
        calibrated_ok = False
        brier_detail = "nenhuma janela com Brier score calculável."
    criteria.append(
        ApprovalCriterion(
            name="probabilidades_razoavelmente_calibradas",
            passed=calibrated_ok,
            detail=brier_detail,
        )
    )

    return ApprovalReport(criteria=criteria)
