"""O que a API paga consegue responder — e o que ela nunca vai responder.

A causa do erro nao era a chave nem a URL. Era pergunta errada: o sistema
consulta `financial/news?ticker=EURUSD` e
`financial/financial-metrics?ticker=EURUSD&period=annual`, que sao
endpoints de **mercado acionario**. "Metricas financeiras anuais" descrevem
uma EMPRESA — receita, margem, alavancagem. Par de moedas nao tem balanco.

A cobertura anunciada da AIsa para essa familia de endpoints e acoes (mais
cripto por outra rota): precos, noticias, demonstrativos, metricas,
estimativas de analistas, participacoes, arquivamentos na SEC. Nada disso
existe para EURUSD, e nenhuma chave faz existir.

Enquanto o sistema pergunta assim, cada analise gasta duas chamadas para
receber erro. Este modulo corta a pergunta ANTES de sair — resultado
`SKIPPED`, que ja tira o fator do calculo redistribuindo o peso, em vez de
`ERROR`, que descreveria uma falha da API que nao aconteceu.

A regra e conservadora de proposito: bloqueia so o que se SABE que nao e
coberto (par de moedas, metal). Um ticker de acao de verdade passa. Assim o
dia em que a AIsa publicar um endpoint de cambio, ou o dia em que este
sistema operar acoes, nada aqui precisa mudar para destravar.
"""

from __future__ import annotations

from app.calendar_feed.blackout import currencies_for_symbol
from app.market.catalog import MARKET_CATALOG

_CATALOG_CODES = frozenset(item.code for item in MARKET_CATALOG)

# Metais e negociado como par (XAUUSD), entao `currencies_for_symbol` nao o
# reconhece — XAU nao e moeda ISO. Listados a parte para nao escaparem.
_METALS = frozenset({"XAU", "XAG", "XPT", "XPD"})


def _normalize(symbol: str) -> str:
    """Nome sem sufixo de corretora: EURUSD.a, EURUSD_i -> EURUSD."""
    limpo = "".join(ch for ch in symbol.upper() if ch.isalnum())
    for codigo in _CATALOG_CODES:
        if limpo.startswith(codigo):
            return codigo
    return limpo


def is_covered(symbol: str) -> bool:
    """A API de acoes tem alguma chance de responder sobre este ativo?"""
    normalizado = _normalize(symbol)
    if normalizado in _CATALOG_CODES:
        return False
    if len(normalizado) >= 6 and normalizado[:3] in _METALS:
        return False
    return not currencies_for_symbol(symbol)


def describe_gap(symbol: str) -> str:
    """Por que a consulta nao foi feita, em texto para o relatorio.

    Diz que NAO houve custo: sem isso o operador leria "sem dados" e
    concluiria que a assinatura esta sendo gasta a toa.
    """
    return (
        f"MarketPulse nao consultada para {symbol.upper()}: a API da AIsa cobre "
        "acoes e cripto, e nao pares de moedas ou metais — nao existe "
        "demonstrativo financeiro de EURUSD. Nenhuma chamada foi feita e "
        "nenhuma cota foi gasta; o fator sai do calculo com o peso "
        "redistribuido."
    )


class CoverageGuard:
    """Corta a consulta antes de qualquer camada que custe algo.

    Fica por FORA do cache, do armazenamento e do orcamento de proposito:
    nao ha o que guardar nem o que contabilizar sobre uma chamada que nunca
    devia acontecer.
    """

    def __init__(self, inner, *, skipped_factory) -> None:
        self._inner = inner
        self._skipped = skipped_factory

    def fetch_assessment(self, symbol: str, *, now):
        if not is_covered(symbol):
            return self._skipped(describe_gap(symbol))
        return self._inner.fetch_assessment(symbol, now=now)
