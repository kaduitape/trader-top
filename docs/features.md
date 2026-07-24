# Catálogo de Features — Fase 4

Este documento é a versão em prosa de `app/market/features.py::FEATURE_CATALOG`
(a fonte de verdade é o código — mantenha os dois sincronizados). Cada
feature documenta fórmula, janela, atraso (barras necessárias antes do
primeiro valor não-nulo), risco de vazamento de dados, tratamento de valor
nulo, custo computacional e importância observada.

Por exigência do prompt mestre ("Não crie centenas de features sem
controle"), o conjunto abaixo é deliberadamente restrito — cada feature
tem uso concreto planejado em uma estratégia (Fase 6) ou no motor de regime
(`app/market/regimes.py`). Ver seção "Fora de escopo" ao final.

## Retornos e momentum

| Feature | Fórmula | Janela | Atraso |
|---|---|---|---|
| `log_return` | `ln(close[t]/close[t-1])` | 1 | 1 barra |
| `return_10` | `close[t]/close[t-10] - 1` | 10 | 10 barras |
| `roc_10` | `(close[t]-close[t-10])/close[t-10]*100` | 10 | 10 barras |
| `momentum_10` | `close[t]-close[t-10]` | 10 | 10 barras |

Sem risco de vazamento (usam apenas `shift()` positivo). Nulo nas barras
iniciais da janela.

## Médias móveis e distância

| Feature | Fórmula | Janela |
|---|---|---|
| `ema_9`, `ema_21`, `ema_50`, `ema_200` | EMA recursiva, `adjust=False` | span correspondente |
| `dist_ema_9`, `dist_ema_21`, `dist_ema_50`, `dist_ema_200` | `(close-ema)/close*100` | idem |
| `ema_21_slope` | coeficiente angular (regressão linear) da EMA21 sobre 20 barras | 20 |

`ema_200` é a mais lenta a "esquentar" (200 barras de atraso) — o
`required_lookback_bars()` do módulo reflete isso.

## Osciladores e volatilidade

| Feature | Fórmula | Janela |
|---|---|---|
| `rsi_14` | RSI de Wilder (`alpha=1/14`) | 14 |
| `macd_line`, `macd_signal`, `macd_histogram` | EMA12-EMA26; sinal=EMA9(MACD); histograma=MACD-sinal | 12/26/9 |
| `atr_14` | EWM(True Range, `alpha=1/14`) | 14 |
| `adx_14`, `plus_di_14`, `minus_di_14` | ADX de Wilder sobre ±DM suavizado pelo True Range | 14 (dupla suavização ⇒ ~28 barras de atraso real) |
| `bollinger_upper/middle/lower` | SMA(20) ± 2×desvio-padrão móvel(20) | 20 |
| `zscore_20` | `(close-média_móvel_20)/desvio_padrão_móvel_20` | 20 |
| `realized_volatility_20` | desvio-padrão móvel(20) do `log_return` | 20 |

`adx_14`/`plus_di_14`/`minus_di_14` alimentam diretamente a classificação
de tendência em `app/market/regimes.py`.

## Preço médio ponderado por volume

| Feature | Fórmula | Janela |
|---|---|---|
| `vwap_20` | `Σ(preço_típico×volume,20) / Σ(volume,20)`, preço típico = `(high+low+close)/3` | 20 |
| `dist_vwap_20` | `(close-vwap)/vwap*100` | 20 |

**Limitação documentada:** este é um VWAP por janela móvel, não um VWAP de
sessão verdadeiro (que exigiria alinhamento de sessão/dia de negociação,
fora do escopo desta fase). Reavaliar se uma estratégia precisar do VWAP de
sessão real.

## Forma do candle

| Feature | Fórmula |
|---|---|
| `candle_amplitude` | `high - low` |
| `candle_body` | `\|close - open\|` |
| `candle_upper_wick` | `high - max(open, close)` |
| `candle_lower_wick` | `min(open, close) - low` |
| `candle_streak` | contagem de candles consecutivas na mesma direção (positivo = sequência de alta, negativo = sequência de baixa) |

Nunca nulas — dependem apenas da própria barra (já fechada quando
processada) e, no caso de `candle_streak`, de barras anteriores.

## Volume

| Feature | Fórmula | Janela |
|---|---|---|
| `relative_volume_20` | `tick_volume[t] / média_móvel_20(tick_volume)` | 20 |
| `volume_acceleration` | `(tick_volume[t]-tick_volume[t-1])/tick_volume[t-1]` | 1 |

`relative_volume_20` alimenta a checagem de liquidez adequada em
`app/market/regimes.py`.

## Spread

| Feature | Fórmula | Janela |
|---|---|---|
| `avg_spread_20` | média móvel(20) do campo `spread` (pontos) da candle | 20 |
| `spread_variation_20` | desvio-padrão móvel(20) do `spread` | 20 |
| `relative_spread_bps` | `spread_pontos × point / close × 10000` | — (por barra) |

`relative_spread_bps` fica `NaN` se `point` (tamanho de tick do símbolo) não
for passado para `build_candle_features` — nunca inventamos um valor.
`avg_spread_20` alimenta a checagem de spread adequado em
`app/market/regimes.py` (comparado diretamente em pontos, sem precisar de
`point`, pois o campo `spread` do MT5 já vem em pontos).

## Tempo e sessão

| Feature | Fórmula |
|---|---|
| `hour_utc`, `minute_of_day`, `day_of_week` | derivados diretos de `open_time` (UTC) |
| `session` | heurística por faixa de hora UTC: `ASIA` (22h–7h), `LONDON` (7h–12h), `LONDON_NY_OVERLAP` (12h–16h), `NEW_YORK` (16h–22h) |

**Limitação documentada:** a heurística de sessão não usa calendário de
feriados nem ajuste de horário de verão. Suficiente como feature de
contexto macro; revisar se a Estratégia G (abertura de sessão, Fase 6)
precisar de precisão maior.

## Fora de escopo nesta fase (com justificativa)

- **Microestrutura de tick** (order flow imbalance, microprice, velocidade/
  direção de tick, desequilíbrio bid/ask): pertence à Estratégia H
  (microestrutura), que só faz sentido junto do módulo de livro de ofertas.
- **Proximidade de notícia**: depende do calendário econômico (Fase 19,
  ainda não implementada).
- **Correlação com ativos relacionados**: exige alinhamento multi-símbolo e
  configuração explícita de quais ativos correlacionar; sem consumidor
  concreto ainda.
- **Tendência do timeframe superior**: exige juntar duas séries de
  timeframes diferentes; entra quando uma estratégia multi-timeframe
  (Estratégia I, Fase 6) precisar dela.

## Processamento incremental

`required_lookback_bars()` retorna o maior atraso entre todas as features
(hoje, 200 barras — por causa de `ema_200`). Uma coleta incremental não
precisa reprocessar todo o histórico do símbolo: basta manter essa
quantidade de barras mais as novas candles coletadas para que a última
linha da matriz seja válida. Um motor verdadeiramente streaming (O(1) por
barra) fica para quando paper trading (Fase 10+) exigir latência sub-barra.
