"""Validacao de modelos (Fase 8): metricas de classificacao + metricas de
trading APOS custos.

O prompt mestre e explicito (secoes 12/31): um modelo nunca e aprovado
apenas por ROC-AUC ou acuracia alta. Ele precisa, no conjunto de TESTE
(fora da amostra), superar o baseline em resultado economico DEPOIS de
custos (spread, slippage, comissao) — por isso `TradingMetrics` reusa
literalmente `app.backtesting.costs` em vez de reinventar uma formula de
custo paralela.

Limitacao conhecida (documentada em docs/ml.md): o dataset de sinais
(`app.ml.datasets`) guarda apenas o spread no momento da ENTRADA
(`entry_spread`); o mesmo valor e usado como aproximacao do spread na
SAIDA por nao haver uma coluna separada — isso tende a ser conservador o
suficiente na pratica (spreads de saida raramente sao menores que os de
entrada), mas nao e uma medicao exata.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)

from app.backtesting.costs import CostModel, apply_entry_cost, apply_exit_cost, commission_cost
from app.strategies.base import SignalDirection


@dataclass(frozen=True, slots=True)
class ClassificationMetrics:
    num_samples: int
    num_positive: int
    precision: float | None
    recall: float | None
    f1: float | None
    roc_auc: float | None
    pr_auc: float | None
    brier_score: float | None
    log_loss_value: float | None


def compute_classification_metrics(
    y_true: np.ndarray, y_prob: np.ndarray, *, threshold: float = 0.5
) -> ClassificationMetrics:
    n = len(y_true)
    num_positive = int(np.sum(y_true))
    if n == 0:
        return ClassificationMetrics(0, 0, None, None, None, None, None, None, None)

    y_pred = (y_prob >= threshold).astype(int)

    # ROC-AUC/PR-AUC/Brier/log-loss exigem as duas classes presentes;
    # precision/recall/f1 exigem ao menos uma previsao positiva ou
    # verdadeira para nao gerar avisos de divisao por zero sem sentido.
    has_both_classes = 0 < num_positive < n
    precision = float(precision_score(y_true, y_pred, zero_division=0))
    recall = float(recall_score(y_true, y_pred, zero_division=0))
    f1 = float(f1_score(y_true, y_pred, zero_division=0))
    roc_auc = float(roc_auc_score(y_true, y_prob)) if has_both_classes else None
    pr_auc = float(average_precision_score(y_true, y_prob)) if has_both_classes else None
    brier = float(brier_score_loss(y_true, y_prob))
    logloss = float(log_loss(y_true, y_prob, labels=[0, 1])) if has_both_classes else None

    return ClassificationMetrics(
        num_samples=n,
        num_positive=num_positive,
        precision=precision,
        recall=recall,
        f1=f1,
        roc_auc=roc_auc,
        pr_auc=pr_auc,
        brier_score=brier,
        log_loss_value=logloss,
    )


@dataclass(frozen=True, slots=True)
class TradingMetrics:
    num_trades: int
    win_rate: float
    expectancy_after_costs: float
    profit_factor: float | None
    net_profit_after_costs: float
    expectancy_ci_low: float | None
    expectancy_ci_high: float | None
    result_by_regime_trend: dict[str, float]


def _confidence_interval(
    values: np.ndarray, *, confidence: float = 0.95
) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    mean = float(values.mean())
    std_err = float(values.std(ddof=1) / math.sqrt(len(values)))
    if std_err == 0.0:
        return mean, mean
    # Aproximacao normal (amostras suficientes) em vez de bootstrap —
    # suficiente para uma checagem de aprovacao, nao para um paper.
    z = 1.96 if confidence == 0.95 else 1.645
    return mean - z * std_err, mean + z * std_err


def compute_trading_metrics(
    dataset: pd.DataFrame,
    probabilities: np.ndarray,
    *,
    threshold: float,
    cost_model: CostModel,
    point: float,
    volume: float = 1.0,
    contract_size: float = 1.0,
) -> TradingMetrics:
    """Filtra o dataset pelas linhas onde a probabilidade prevista >=
    `threshold` (as unicas em que o modelo "recomendaria" operar) e
    recalcula o resultado economico real dessas linhas, aplicando spread +
    slippage + comissao exatamente como o backtester (Fase 5/7) faria.
    `volume`/`contract_size` sao 1.0 por padrao: o resultado fica expresso
    em pontos de preco, nao em moeda da conta — suficiente para comparar
    modelos/thresholds entre si."""
    selected = dataset.loc[probabilities >= threshold]
    if selected.empty:
        return TradingMetrics(0, 0.0, 0.0, None, 0.0, None, None, {})

    net_pnls = []
    regime_pnls: dict[str, float] = {}
    for _, row in selected.iterrows():
        direction = SignalDirection(row["direction"])
        sign = 1.0 if direction == SignalDirection.LONG else -1.0
        spread_points = int(row["entry_spread"])

        entry_price = apply_entry_cost(
            float(row["entry_price"]),
            direction,
            model=cost_model,
            candle_spread_points=spread_points,
            point=point,
        )
        exit_price = apply_exit_cost(
            float(row["exit_price"]),
            direction,
            model=cost_model,
            candle_spread_points=spread_points,
            point=point,
        )
        gross_pnl = (exit_price - entry_price) * sign * volume * contract_size
        net_pnl = gross_pnl - commission_cost(cost_model, volume)
        net_pnls.append(net_pnl)

        regime_key = str(row.get("regime_trend", "UNKNOWN"))
        regime_pnls[regime_key] = regime_pnls.get(regime_key, 0.0) + net_pnl

    pnls = np.array(net_pnls, dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    profit_factor: float | None
    if len(losses) == 0:
        profit_factor = float("inf") if len(wins) > 0 else None
    else:
        profit_factor = float(wins.sum() / abs(losses.sum()))

    ci_low, ci_high = _confidence_interval(pnls)

    return TradingMetrics(
        num_trades=len(pnls),
        win_rate=float(len(wins) / len(pnls)),
        expectancy_after_costs=float(pnls.mean()),
        profit_factor=profit_factor,
        net_profit_after_costs=float(pnls.sum()),
        expectancy_ci_low=ci_low,
        expectancy_ci_high=ci_high,
        result_by_regime_trend=regime_pnls,
    )
