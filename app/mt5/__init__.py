"""Camada de integracao com o MetaTrader 5.

Esta e a UNICA camada do sistema que importa o pacote `MetaTrader5`. Todo o
restante do sistema (estrategias, risco, execucao, API) depende apenas dos
tipos definidos aqui (`app.mt5.client.MT5ClientProtocol` e as dataclasses de
`account.py`, `market_data.py`, `positions.py`, `orders.py`,
`symbol_mapper.py`), nunca do pacote externo diretamente. Isso permite
testar o sistema inteiro com um cliente fake, sem depender de um terminal
MetaTrader 5 instalado (ver docs/architecture.md secao 3).

Nesta fase (Fase 2), o modulo e estritamente somente-leitura: nenhuma
funcao aqui envia ordens (`order_send`) ou modifica o estado da conta.
"""
