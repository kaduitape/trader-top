"""Paper trading (Fase 10): execucao simulada, nunca uma ordem real.

`PaperTradingEngine` reusa a mesma interface `Strategy`/`Signal` das
Fases 5/6 e a mesma regra conservadora de stop-vs-alvo na mesma candle
das Fases 5/7/8, mas processa dados de forma INCREMENTAL (uma chamada
por novo lote de candles, tipicamente vindo de um poll periodico ao
MetaTrader 5) e persiste o estado (posicao aberta, cursor de progresso)
em banco — sobrevive a reinicio do processo, ao contrario dos motores de
backtest (Fase 5/7), que processam um array fixo em memoria uma unica
vez."""
