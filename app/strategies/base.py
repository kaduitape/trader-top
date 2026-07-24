"""Interface de estrategia e tipos de sinal, conforme prompt mestre secao 11.

Uma estrategia nunca envia ordens nem conhece o motor de risco/execucao —
ela apenas observa o estado de mercado ate a barra atual (nunca dados
futuros, ver `MarketState`) e, opcionalmente, devolve um `Signal`. Tudo o
que acontece depois (aprovacao de risco, dimensionamento, execucao) e
responsabilidade de outras camadas (Fases 11/17), nao da estrategia.
"""

from __future__ import annotations

import enum
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.market.regimes import MarketRegime


class SignalDirection(enum.StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class Signal:
    """Um sinal de entrada. Todos os campos exigidos pelo prompt mestre
    (secao 2/11) para rastreabilidade — nenhum campo "inventado" depois."""

    symbol: str
    strategy_name: str
    direction: SignalDirection
    generated_at: datetime
    reference_price: float
    stop_loss: float
    take_profit: float
    valid_until: datetime
    reason: str
    regime_required: str
    confidence: float
    features_used: dict[str, float]
    model_version: str = "rule-based"
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True, slots=True)
class MarketState:
    """Estado de mercado visivel para uma estrategia na barra atual.

    `features` contem apenas barras ate a atual (inclusive) — nunca barras
    futuras. Isso e garantido por quem monta o `MarketState` (o motor de
    backtest, `app/backtesting/engine.py`), nao pela estrategia."""

    symbol: str
    timeframe: str
    features: pd.DataFrame
    regime: MarketRegime | None

    @property
    def current(self) -> pd.Series:
        return self.features.iloc[-1]

    @property
    def previous(self) -> pd.Series | None:
        if len(self.features) < 2:
            return None
        return self.features.iloc[-2]


class Strategy(ABC):
    """Interface base. Cada estrategia concreta implementa apenas
    `generate_signal` — toda a logica de regime permitido/proibido deve ser
    verificada dentro dela (usando `state.regime`), nao fora."""

    name: str

    @abstractmethod
    def generate_signal(self, state: MarketState) -> Signal | None:
        """Retorna um `Signal` se as condicoes da estrategia forem
        atendidas na barra atual (`state.current`), ou `None` caso
        contrario. Nunca levanta excecao por "nao ter sinal" — isso e um
        resultado normal, nao um erro."""
        raise NotImplementedError
