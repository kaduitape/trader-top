# FotoAnálise — GPS visual do trade

Camada **visual** sobre a análise que já existe. Não é um segundo motor de
análise: é a mesma conclusão da Análise PRO projetada no eixo de preço.

Rota: `/dashboard/foto-analise` • API: `POST /api/foto-analise`

## O que foi reaproveitado

Nada de indicador, estrutura ou tendência é recalculado aqui.

| Já existia | Onde | Usado para |
|---|---|---|
| `analyze_symbol` | `app/services/analysis_service.py` | tendência, score, confluências, motivos, trade levels |
| `build_multi_timeframe_snapshot` | `app/market/multi_timeframe.py` | candles reais + features do timeframe |
| `atr_14`, `vwap_20`, `ema_9/21/50/200` | `app/market/features.py` | fatores de confluência por faixa |
| `detect_swings`, `cluster_swing_levels` | `app/market/structure.py` | suportes e resistências |
| `detect_order_blocks`, `detect_liquidity_sweeps`, `compute_premium_discount` | `app/market/smc.py` | liquidez, blocos, desconto/prêmio |
| `classify_latest_regime` → `Trend` | `app/market/regimes.py` | viés automático |
| `TradeLevels` | `app/market/trade_levels.py` | invalidação estrutural |
| `Symbol.point` | catálogo do MT5 | converter "20 ticks" em preço |
| `SignalDirection` | `app/strategies/base.py` | vocabulário de direção |
| Auth, templates, `base.html`, polling | dashboard existente | tela e atualização |

**Novo apenas:** a projeção no eixo de preço (`OpportunityHeatmapEngine`), a
escolha da região (`EntryZoneEngine`), o desenho (`ChartAnnotationService`) e
a orquestração (`FotoAnaliseService`).

## Os componentes

```text
app/foto_analise/
├── heatmap.py      OpportunityHeatmapEngine  — score por faixa de preço
├── entry_zone.py   EntryZoneEngine           — zona, sweet spot, entrar/esperar
├── annotations.py  ChartAnnotationService    — SVG (candles reais + zonas)
└── service.py      FotoAnaliseService        — orquestra e monta o resultado
```

## Decisões que valem conhecer

**Buy e sell são scores separados, não complementares.** Uma faixa colada
numa resistência forte é ruim para comprar *e* ruim para vender. Forçar
`sell = 100 - buy` inventaria uma vantagem vendedora que nenhum dado
sustenta.

**O take entra no score, não só no desenho.** Se há resistência entre a
faixa e o alvo, o alvo não é alcançável dali — então a mesma região pontua
diferente para 10 e para 50 ticks. Era o requisito declarado como
importante.

**A zona é sempre uma região.** Mesmo quando uma única faixa vence, ela é
expandida para a largura que representa. `min == max` seria um preço único
disfarçado de zona, e ninguém executa no centésimo.

**Entrar agora é pergunta separada de onde entrar.** `READY`,
`WAIT_PULLBACK`, `MISSED` e `NO_SETUP` são estados distintos: preço acima de
uma zona de compra é aguardar, preço abaixo é perseguir — erros opostos.

**Os portões de notícia/calendário não bloqueiam o desenho.** Eles decidem
se o *robô* opera. Esta tela é consultiva, e esconder o mapa durante um
evento tiraria justamente a informação que ajuda a esperar. Os bloqueios
aparecem como aviso.

**A seta é tracejada.** É projeção do cenário analisado, não previsão.
Sólida, leria como afirmação sobre o futuro.

## Atualidade dos dados

A foto vale o que valem os candles no banco. Se o coletor MT5 parar, o
desenho continua bonito e passa a retratar o passado — e é assim que alguém
opera em cima de preço velho sem perceber.

Por isso a idade é tratada como parte do resultado, não como detalhe:

- `last_candle_at`, `data_age_minutes` e `is_stale` saem no payload e na tela;
- acima de **3 barras** do timeframe escolhido, o gráfico recebe uma tarja
  vermelha atravessada (`DADOS DESATUALIZADOS`) e um aviso no topo da página.
  O limite é relativo porque 30 minutos de atraso é irrelevante no D1 e
  inaceitável no M1;
- o preço atual usa o **tick mais recente** quando ele é mais novo que a
  última candle fechada (`price_source: TICK`), com o mid entre bid e ask —
  usar só o bid enviesaria a geometria da zona pelo lado comprado do spread.

A coleta grava apenas candles **fechadas**, então a última já nasce até um
timeframe atrasada. Isso é do desenho, não defeito; o tick cobre a diferença
quando existe.

## Vocabulário: o que o score NÃO é

O número de cada faixa é **confluência e qualidade relativa da região**.

Não é probabilidade de lucro, e nada na tela o apresenta assim. Não existe
backtest neste projeto que sustente essa leitura — e um "82% de chance de
ganhar" inventado faria alguém arriscar dinheiro por um motivo falso. Um
teste (`test_nothing_claims_probability_of_profit`) trava esse contrato.

## API

```json
POST /api/foto-analise
{"symbol": "MNQ", "timeframe": "M15", "take_ticks": 20, "direction": "AUTO"}
```

`direction`: `AUTO` | `COMPRA` | `VENDA` — a direção forçada vence a
automática, porque quem pede "só venda" quer ver a melhor venda possível; o
contexto adverso aparece no score e nos motivos, não como recusa.

`detail`: `SIMPLIFICADO` | `NORMAL` | `AVANCADO` — granularidade do mapa
(9, 17 ou 33 faixas), mesma cobertura de preço.

Resposta: `decision`, `bias`, `score`, `current_price`, `entry_zone`
(`min`/`sweet_spot`/`max`/`distance_ticks`), `stop`, `take`, `status`,
`decision_level`, `heatmap[]` (com `factors` de cada faixa), `candles[]`,
`levels[]`, `reasons_for[]`, `reasons_against[]`, `warnings[]`.

## Limitação conhecida

Os pesos de confluência do heatmap são **escolhas conservadoras, não
parâmetros otimizados**. Não há backtest que os valide — o mesmo vale para
`MIN_ZONE_SCORE = 62`. Eles ordenam regiões de forma coerente com a análise
existente; não prometem resultado.
