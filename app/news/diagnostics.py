"""Teste da API paga, feito de propósito FORA do cache e do orçamento.

Testar através do caminho normal não testaria nada: o cache devolveria a
resposta guardada e a tela diria "funcionou" sem ter falado com ninguém.
Por isso a sonda monta o cliente direto.

Ela consome cota de verdade — duas chamadas — e é isso mesmo. Um teste que
não gasta não prova que a chave gasta. As duas chamadas entram no registro
com origem própria, para não se confundirem depois com consumo da operação.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.news.aisa import AisaFundamentalsProvider, AisaMarketPulseClient, AisaNewsProvider
from app.news.call_log import record_api_call
from app.news.provider import ProviderStatus

ORIGIN_TEST = "teste"


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    kind: str
    status: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status == ProviderStatus.OK.value


@dataclass(frozen=True, slots=True)
class ProbeResult:
    symbol: str
    base_url: str
    outcomes: tuple[ProbeOutcome, ...]

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(item.ok for item in self.outcomes)

    @property
    def partial(self) -> bool:
        """Uma consulta respondeu e a outra nao.

        E o caso mais provavel com par de moedas: noticias existem para
        EURUSD, fundamentos anuais nao — eles descrevem empresa, nao
        cambio. Chamar isso de "falhou" esconderia que metade funciona.
        """
        return any(item.ok for item in self.outcomes) and not self.ok


def probe_api(
    session: Session,
    settings: Settings,
    *,
    api_key: str,
    base_url: str | None,
    symbol: str,
) -> ProbeResult:
    del settings
    agora = datetime.now(UTC)
    client = AisaMarketPulseClient(api_key=api_key, base_url=base_url)

    resultados: list[ProbeOutcome] = []
    for rotulo, provider in (
        ("noticias", AisaNewsProvider(client)),
        ("fundamentos", AisaFundamentalsProvider(client)),
    ):
        assessment = provider.fetch_assessment(symbol, now=agora)
        resultados.append(
            ProbeOutcome(
                kind=rotulo,
                status=str(assessment.status),
                message=assessment.message or "(sem mensagem)",
            )
        )
        record_api_call(
            session,
            kind=rotulo,
            symbol=symbol,
            outcome=str(assessment.status),
            duration_ms=0,
            origin=ORIGIN_TEST,
            now=agora,
        )

    return ProbeResult(
        symbol=symbol,
        base_url=base_url or "https://api.aisa.one",
        outcomes=tuple(resultados),
    )
