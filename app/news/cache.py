"""Cache com prazo de validade para as respostas da MarketPulse.

Motivo: cada analise (`analyze_symbol`) dispara DUAS chamadas HTTP — uma de
noticias e uma de fundamentos. Sem cache, abrir a tela de Analise PRO,
apertar F5 ou rodar um ciclo do piloto a cada 15 segundos multiplicava esse
custo sem nenhum ganho: o sentimento agregado das ultimas manchetes nao muda
a cada segundo.

Tres decisoes que valem explicar, porque sao o que separa um cache util de
um cache perigoso:

1. **So resultado bom entra.** `ERROR` e `NOT_CONFIGURED` passam direto toda
   vez. Congelar uma falha por dez minutos transformaria uma instabilidade
   momentanea da API em dez minutos de fator neutro sem ninguem perceber.

2. **A resposta servida do cache diz que veio do cache**, com a idade em
   segundos, na propria `message` que aparece na tela. O sistema inteiro
   segue a regra de nunca apresentar dado velho como se fosse fresco.

3. **O relogio e monotonico**, nao o `now` do chamador. Assim um backtest ou
   um replay passando datas do passado nao bagunca a expiracao, e o teste
   controla o tempo injetando o relogio.

O cache vive no processo. Web e conector Windows sao processos diferentes e
cada um tem o seu — o que e suficiente: o objetivo e cortar a repeticao
dentro de um mesmo processo (recarregar a pagina, ciclo do piloto), nao
coordenar uma frota.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from app.news.provider import (
    FundamentalsAssessment,
    FundamentalsProvider,
    NewsAssessment,
    NewsProvider,
    ProviderStatus,
)

DEFAULT_TTL_SECONDS = 600.0

Clock = Callable[[], float]


@dataclass(slots=True)
class _Entry:
    value: object
    stored_at: float


class AssessmentCache:
    """Guarda avaliacoes por (namespace, simbolo) durante `ttl_seconds`.

    `ttl_seconds <= 0` desliga o cache por completo — util para depurar um
    comportamento estranho da API sem precisar mexer no codigo.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Clock = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def get(self, namespace: str, symbol: str) -> tuple[object, float] | None:
        """Valor ainda valido e ha quantos segundos foi guardado, ou None."""
        if self._ttl <= 0:
            return None
        key = (namespace, symbol.upper())
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            age = self._clock() - entry.stored_at
            if age > self._ttl:
                # Expirado: sai do dicionario agora para nao acumular
                # simbolos que ninguem consulta mais.
                del self._entries[key]
                self.misses += 1
                return None
            self.hits += 1
            return entry.value, age

    def put(self, namespace: str, symbol: str, value: object) -> None:
        if self._ttl <= 0:
            return
        with self._lock:
            self._entries[(namespace, symbol.upper())] = _Entry(
                value=value, stored_at=self._clock()
            )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0


def _annotate(message: str, age_seconds: float) -> str:
    return f"{message} [cache MarketPulse: {int(age_seconds)}s]"


class CachedNewsProvider:
    """Envolve um `NewsProvider` real; a interface e identica."""

    def __init__(self, inner: NewsProvider, cache: AssessmentCache, *, namespace: str) -> None:
        self._inner = inner
        self._cache = cache
        self._namespace = f"news:{namespace}"

    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        cached = self._cache.get(self._namespace, symbol)
        if cached is not None:
            assessment, age = cached
            assert isinstance(assessment, NewsAssessment)
            return replace(assessment, message=_annotate(assessment.message, age))

        assessment = self._inner.fetch_assessment(symbol, now=now)
        if assessment.status == ProviderStatus.OK:
            self._cache.put(self._namespace, symbol, assessment)
        return assessment


class CachedFundamentalsProvider:
    def __init__(
        self, inner: FundamentalsProvider, cache: AssessmentCache, *, namespace: str
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._namespace = f"fundamentals:{namespace}"

    def fetch_assessment(self, symbol: str, *, now: datetime) -> FundamentalsAssessment:
        cached = self._cache.get(self._namespace, symbol)
        if cached is not None:
            assessment, age = cached
            assert isinstance(assessment, FundamentalsAssessment)
            return replace(assessment, message=_annotate(assessment.message, age))

        assessment = self._inner.fetch_assessment(symbol, now=now)
        if assessment.status == ProviderStatus.OK:
            self._cache.put(self._namespace, symbol, assessment)
        return assessment
