"""Executor em conta demo (Fase 11).

`app.execution.order_state` implementa a máquina de estados de ordem
(`docs/architecture.md`, seção 5) de forma pura (sem I/O), mesmo padrão
de `app.core.system_mode`. `app.execution.engine.DemoExecutionEngine`
liga estratégia (Fase 5/6) → motor de risco (`app.risk`) → envio de
ordem real em conta demo (`app.mt5.orders.send_market_order`) →
persistência (`app.database.models.live_trade`) → reconciliação contra
o estado real reportado pelo MetaTrader 5.

Simplificação deliberada em relação à máquina de estados completa do
prompt mestre: para ordens a mercado (retail, sem profundidade de livro),
`order_send` do MetaTrader 5 é síncrono — o retcode já informa sucesso ou
rejeição imediatamente, sem preenchimento parcial assíncrono a
acompanhar. Por isso `ORDER_ACCEPTED`/`PARTIALLY_FILLED`/`FILLED` são
tratados como um único resultado síncrono (`POSITION_OPEN` ou
`REJECTED`), não estados intermediários monitorados separadamente."""
