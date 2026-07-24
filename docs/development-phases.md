# Fases de Desenvolvimento — MT5 AI Scalper

Resumo operacional das 15 fases definidas no prompt mestre (seção 27),
usado como checklist vivo. Status atualizado ao final de cada execução.

| Fase | Nome | Status |
|---|---|---|
| 0 | Descoberta e decisões | ✅ Concluída nesta execução |
| 1 | Fundação (estrutura, config, logging, FastAPI, MySQL, Alembic, auth básica, testes, lint) | ✅ Concluída nesta execução |
| 2 | Conector MT5 somente leitura | ✅ Concluída nesta execução |
| 3 | Qualidade e armazenamento de dados | ✅ Concluída nesta execução |
| 4 | Indicadores e regimes | ✅ Concluída nesta execução |
| 5 | Backtester por candles | ✅ Concluída nesta execução |
| 6 | Estratégias base | ✅ Concluída nesta execução |
| 7 | Backtest por ticks | ✅ Concluída nesta execução |
| 8 | IA inicial | ✅ Concluída nesta execução |
| 9 | Walk-forward e robustez | ✅ Concluída nesta execução |
| 10 | Paper trading | ✅ Concluída nesta execução |
| 11 | Executor em conta demo | ✅ Concluída nesta execução |
| 12 | Dashboard completo | ✅ Concluída nesta execução |
| 13 | Detecção de drift | ✅ Concluída nesta execução |
| 14 | Modelos avançados | ⏸️ Adiada — condição do prompt mestre não observada ainda |
| 15 | Preparação operacional | ✅ Concluída nesta execução |

## Critérios de aceite — Fase 1 (esta execução)

- [x] Estrutura de diretórios criada conforme arquitetura.
- [x] `pyproject.toml` com dependências da fundação.
- [x] `.env.example` documentando toda configuração sensível.
- [x] Configuração tipada validada na inicialização (`app/core/config.py`).
- [x] Logging estruturado em JSON.
- [x] FastAPI com `/health`.
- [x] Camada MySQL/SQLAlchemy configurada (validada via SQLite nos testes,
      MySQL real pendente de servidor — ver `assumptions.md`).
- [x] Alembic configurado com migration inicial.
- [x] Autenticação básica (hash de senha + login JWT) com modelos de
      usuário/papel.
- [x] Testes unitários e de integração passando.
- [x] Lint (Ruff), formatação (Black) e type check (MyPy) configurados e
      sem erros nos módulos criados.
- [x] README.md com instruções de instalação e execução.

Nenhuma conexão real ao MetaTrader, nenhum envio de ordem, nenhuma
estratégia e nenhum treinamento de modelo foram implementados nesta
execução, conforme instruído.

## Critérios de aceite — Fase 2

- [x] Camada `app/mt5` isolando o pacote `MetaTrader5` (unica camada que o
      importa), com `MT5ClientProtocol` tipado para permitir testes com
      fake, sem terminal instalado.
- [x] Conexão com reconexão e backoff exponencial (`app/mt5/connection.py`).
- [x] Informações da conta, incluindo identificação demo/real
      (`app/mt5/account.py`).
- [x] Símbolos e especificação (limites de volume, casas decimais) com
      normalização de preço/volume (`app/mt5/symbol_mapper.py`).
- [x] Candles e ticks de leitura (`app/mt5/market_data.py`).
- [x] Posições e ordens/histórico de leitura (`app/mt5/positions.py`,
      `app/mt5/orders.py`). Nenhuma função de envio de ordem existe.
- [x] Healthcheck do terminal (`app/mt5/terminal_health.py`) e deteccao de
      troca de conta.
- [x] Armazenamento: tabelas `symbols`, `candles`, `ticks` (migration 0002),
      com deduplicação (unicidade por símbolo+timeframe+horário e por
      símbolo+timestamp+bid+ask).
- [x] CLI (`python -m app.cli`) com `mt5 check`, `mt5 symbols`,
      `collect candles`, `collect ticks`.
- [x] Logs estruturados em cada falha/reconexão/aviso (ex.: conta real
      detectada).
- [x] Testes unitários e de integração (63 no total) rodando 100% com um
      cliente MT5 fake — nenhum depende de terminal instalado.
- [x] Validado manualmente contra o pacote `MetaTrader5` real instalado
      nesta máquina (sem terminal configurado): falha graciosamente,
      loga o motivo e retorna código de saída 1 — nenhuma resposta foi
      inventada.

Nenhum envio de ordem (`order_send`/`order_check`/`order_calc_*`) foi
implementado nesta fase, conforme instruído — fica para a Fase 11.

## Critérios de aceite — Fase 3

- [x] Checagens de qualidade puras e testáveis (`app/market/data_quality.py`):
      timestamps duplicados, buracos (com tolerância para fechamento de
      mercado), candles com OHLC inconsistente, preço/volume inválido,
      ticks fora de ordem, spread absurdo, atraso do feed e timestamp no
      futuro (indício de timezone incorreto).
- [x] Nota de qualidade (0-100, `compute_score`) e gate de aceitação
      (`is_acceptable`) — nunca escondendo as ocorrências individuais atrás
      do número, conforme exigido pelo prompt mestre.
- [x] Persistência das ocorrências em `data_quality_events` (migration
      0003), uma linha por ocorrência, associada ao símbolo/timeframe.
- [x] Preenchimento incremental real: `collect candles`/`collect ticks`
      agora buscam apenas o que falta desde o último dado conhecido
      (`fetch_candles_range`, `get_last_open_time`/`get_last_timestamp`),
      caindo para busca completa (`--count`/`--seconds`) somente na
      primeira coleta de um símbolo.
- [x] Retenção de ticks configurável (`TICK_RETENTION_DAYS`) via
      `TickRepository.purge_older_than` e comando `data purge-ticks`.
      Candles não são expurgadas (volume bem menor, base do backtest por
      candle).
- [x] Comando `quality check` para rodar as checagens sobre dados já
      armazenados, sem precisar reconectar ao MetaTrader.
- [x] Testes unitários e de integração (94 no total) — nenhum depende de
      terminal MT5 nem de MySQL real.

Deliberadamente fora do escopo desta fase (justificado em
`app/market/data_quality.py`): divergência entre candles e ticks (exige
alinhar janelas de coleta ainda não garantidas sobrepostas) — a ser
revisitada se uma necessidade concreta surgir.

## Critérios de aceite — Fase 4

- [x] Indicadores implementados internamente (não delegados a uma lib de
      terceiros), testáveis e sem vazamento por construção
      (`app/market/indicators.py`): retornos log/janela, SMA, EMA, RSI,
      MACD, ATR, ADX (+DI/-DI), Bollinger Bands, z-score, ROC, momentum,
      inclinação (regressão linear por janela), volatilidade realizada.
- [x] Matriz de features documentada (`app/market/features.py` +
      `docs/features.md`): cada feature tem fórmula, janela, atraso, risco
      de vazamento, tratamento de nulo e custo computacional — nenhuma
      "centena de features sem controle".
- [x] `required_lookback_bars()` expõe quantas barras anteriores bastam
      para a matriz ficar sem NaN — é a resposta deste projeto para
      "processamento incremental" nesta fase (não é preciso reprocessar
      todo o histórico, só manter essa janela).
- [x] Detecção de regime por regras (`app/market/regimes.py`): tendência
      (ADX + ±DI), volatilidade (realizada vs. baseline móvel), adequação
      de spread/liquidez, transição de regime e evento extraordinário
      (pico de ATR) — eixos ortogonais combináveis, como no exemplo do
      prompt mestre ("lateral + volatilidade normal").
- [x] Comando CLI `features build` — a "visualização" desta fase (tabela
      das últimas barras + regime atual), sobre dados já armazenados, sem
      reconectar ao MetaTrader.
- [x] Testes unitários (indicadores com valores conferidos à mão,
      vazamento, features e regimes) e de integração (CLI) — 144 no total,
      nenhum depende de terminal MT5 nem MySQL real.

Deliberadamente fora do escopo desta fase (justificado em
`app/market/features.py` e `docs/features.md`): features de microestrutura
de tick (Estratégia H), proximidade de notícia (Fase 19), correlação entre
ativos e tendência do timeframe superior (Estratégia I) — todas exigem
infraestrutura de fases/estratégias ainda não implementadas.

## Critérios de aceite — Fase 5

- [x] Interface de estratégia (`app/strategies/base.py`: `Signal`,
      `MarketState`, `Strategy`) com todos os campos de rastreabilidade
      exigidos (direção, preço de referência, stop, alvo, validade, motivo,
      regime exigido, confiança, features utilizadas).
- [x] Estratégia baseline (Estratégia B do prompt mestre — cruzamento de
      EMA9/EMA21, `app/strategies/trend/ma_crossover.py`), usada só para
      validar o motor, sem presumir que será lucrativa.
- [x] Motor de backtest por candle (`app/backtesting/engine.py`),
      determinístico: **nunca escolhe o resultado favorável** quando
      stop-loss e take-profit caem na mesma candle (sempre assume o pior
      caso); execução sempre na abertura da barra seguinte ao sinal
      (`entry_delay_bars`), nunca no fechamento da barra em que o sinal foi
      gerado.
- [x] Modelo de custos (`app/backtesting/costs.py`): spread (do próprio
      candle ou fixo), slippage e comissão, sempre aplicados — nunca um
      backtest sem custos.
- [x] Métricas obrigatórias (`app/backtesting/metrics.py`): lucro líquido,
      retorno %, retorno anualizado (quando o período permite), drawdown
      máximo e duração, profit factor, payoff, expectativa, taxa de
      acerto, médias de ganho/perda, Sharpe/Sortino/Calmar, MAE/MFE, custo
      total, sequência máxima de perdas, risco de ruína estimado
      (aproximado, não Monte Carlo), e resultado segmentado por hora, dia
      da semana, tendência do regime e direção — nenhuma métrica escondida
      atrás de um score único.
- [x] Relatório (`app/backtesting/reports.py`) com versão texto e JSON
      completo, via `python -m app.cli backtest run`.
- [x] Testes determinísticos e reproduzíveis (mesma entrada → mesmo
      resultado, exceto o `signal_id`, que é um UUID por design) e cenários
      extremos: lista vazia, uma única candle, sem nenhum sinal, posição
      aberta no fim dos dados, e sobretudo o caso conservador de
      stop+alvo na mesma candle (long e short). 186 testes no total.

Simplificações desta fase, documentadas em `app/backtesting/engine.py`: uma
única posição aberta por vez, volume fixo (dimensionamento de risco real é
Fase 17), sem conversão cambial. Custos segmentados por
corretora/conta/sessão/volume/tipo de ordem (prompt mestre, seção 13) ficam
para a Fase 7, quando houver dados reais de corretora para justificar a
granularidade.

## Critérios de aceite — Fase 6

- [x] Estratégia A — tendência com pullback (`app/strategies/trend/pullback.py`):
      exige regime de tendência (UP/DOWN) + inclinação da EMA21 na mesma
      direção + pullback via z-score se contraindo + RSI recuperando +
      spread adequado. Filtro de notícia de alto impacto deliberadamente
      fora do escopo (sem calendário econômico ainda, Fase 19).
- [x] Estratégia C — rompimento (`app/strategies/breakout/range_breakout.py`):
      variante "entrada após fechamento" (a mais conservadora das quatro
      citadas no prompt mestre); compressão via largura de Bandas de
      Bollinger vs. mínimo recente, expansão de volume, distância mínima
      de rompimento em ATR. As outras três variantes (imediata, reteste,
      ordem stop) ficam para os testes de robustez (Fase 9).
- [x] Estratégia D — retorno à média (`app/strategies/mean_reversion/zscore_reversion.py`):
      **proíbe literalmente operar fora de regime lateral** (SIDEWAYS),
      exige z-score extremo + RSI em exaustão recuperando + candle
      desacelerando; alvo é a própria média (banda média de Bollinger),
      não um R-múltiplo genérico.
- [x] Estratégia E — momentum (`app/strategies/momentum/momentum_continuation.py`):
      ROC acelerando + volume em expansão + sequência de candles na mesma
      direção, com teto de z-score para **evitar entrar em movimento já
      excessivamente estendido** (exigência literal do prompt mestre).
      Velocidade de tick e confirmação do timeframe superior ficam fora do
      escopo (dependem de microestrutura/multi-timeframe, não implementados).
- [x] `app/strategies/registry.py`: registro nomeado das 5 estratégias
      (baseline + 4 novas), usado pela CLI e reutilizável por fases futuras
      (walk-forward, dashboard).
- [x] Relatório comparativo (`app/backtesting/comparison.py`,
      `python -m app.cli backtest compare`): mesmas métricas lado a lado
      para todas as estratégias, **na ordem de entrada, nunca ordenado por
      lucro** — o prompt mestre proíbe eleger uma estratégia "pronta"
      automaticamente ou só pelo lucro líquido; esse julgamento continua
      sendo de um humano (e, tecnicamente, da Fase 9 em diante).
- [x] Cada estratégia isolada, com seu próprio `Config` dataclass
      (parâmetros configuráveis) e testável independentemente do motor de
      backtest. 239 testes no total.

Todas as 4 estratégias compartilham `app/strategies/risk_helpers.py`
(stop/alvo baseado em ATR) para reduzir duplicação, já que o cálculo é
idêntico entre elas (exceto a Estratégia D, cujo alvo é a média, não um
R-múltiplo).

## Critérios de aceite — Fase 7

- [x] Motor de backtest por tick (`app/backtesting/tick_engine.py`) que
      reusa a MESMA geração de sinal por candle (`Strategy`/`MarketState`)
      das Fases 5/6 — a diferença está em como a entrada e a saída são
      executadas: contra a sequência real de ticks (bid/ask), não a OHLC
      aproximada da candle seguinte.
- [x] **Resolve a ambiguidade do motor por candle**: quando stop e alvo
      cabem na mesma candle, o motor por candle sempre assume o pior caso
      (não tem como saber a ordem real). O motor por tick usa a ordem
      cronológica verdadeira dos ticks — testado explicitamente nos dois
      sentidos (alvo antes do stop, e stop antes do alvo).
- [x] Simulação de fill (`app/backtesting/fills.py`), com **auditabilidade
      de cada fill** (`FillResult`: preço solicitado, preço de execução,
      latência aplicada, spread no momento, motivo de rejeição): latência
      configurável, spread real do tick (bid/ask), slippage, e rejeição de
      entrada quando o spread excede um limite (saídas nunca são
      rejeitadas — fechar uma posição não pode ficar pendente por
      conveniência de execução).
- [x] Trailing stop e fechamento por tempo (`max_holding_seconds`),
      exigidos explicitamente pelo prompt mestre para este motor.
- [x] Aviso de liquidez insuficiente quando o gap entre ticks consecutivos
      excede um limite configurável, anexado ao trade (não bloqueia a
      execução, apenas documenta o risco).
- [x] `TickTrade.as_trade()` converte para o `Trade` do motor por candle,
      reaproveitando `compute_metrics`/`build_report`/`format_report_text`
      sem duplicar fórmulas de métricas.
- [x] Comando `python -m app.cli backtest run-ticks`, com auditoria
      completa de fills e rejeições exportável em JSON.
- [x] Testes determinísticos e reproduzíveis, incluindo os cenários mais
      importantes: resolução cronológica (nos dois sentidos), rejeição por
      spread, aviso de liquidez, fechamento por tempo, trailing stop e fim
      dos dados. 265 testes no total.

Dois bugs reais corrigidos durante o desenvolvimento (pegos pelos próprios
testes, não por inspeção manual):
1. `trailing_stop_points` era tratado como distância de preço bruta, sem
   multiplicar pelo `point` do símbolo — o trailing stop nunca se movia na
   prática.
2. `TickRepository.purge_older_than` (Fase 3) sempre purgava
   **globalmente** (todos os símbolos), o que quebrou um teste pré-existente
   quando dados de outro símbolo (desta fase) compartilhavam o banco.
   Corrigido adicionando um filtro opcional por `symbol_id` — uma melhoria
   real para bancos com múltiplos símbolos, não só um ajuste de teste.

Deliberadamente fora do escopo desta fase (documentado em
`app/backtesting/tick_engine.py`): execução parcial (exigiria profundidade
de livro de ofertas, que a Fase 2 já apontou como não garantida por todas
as corretoras) e horário de mercado/calendário de sessão (nenhum
calendário de feriados/sessão implementado ainda).

## Critérios de aceite — Fase 8

- [x] Rotulagem por barreira tripla (`app/ml/labels.py`): as barreiras
      superior/inferior são o próprio `take_profit`/`stop_loss` do sinal
      da estratégia (não um limiar arbitrário); **mesma regra
      conservadora das Fases 5/6** quando ambas cabem na mesma candle;
      retorna `None` (nunca inventa desfecho) sem barras suficientes.
- [x] Construção do dataset (`app/ml/datasets.py`) reusando a lógica de
      "uma posição por vez" do motor de backtest por candle — garante que
      as janelas de barreira tripla de amostras consecutivas nunca se
      sobrepõem. Exclui deliberadamente níveis de preço absolutos
      (EMA/Bollinger brutos) do conjunto de features de ML — ver
      `docs/ml.md`.
- [x] Divisão temporal com embargo (`app/ml/splits.py`): sempre
      cronológica, nunca embaralhada; walk-forward completo fica para a
      Fase 9.
- [x] Pré-processamento (`app/ml/preprocessing.py`), treino
      (`app/ml/train.py`: regressão logística, Random Forest,
      HistGradientBoosting, XGBoost) e calibração de probabilidades
      (`app/ml/calibration.py`, via `sklearn.frozen.FrozenEstimator` +
      `CalibratedClassifierCV` — a API que substituiu `cv="prefit"`,
      removida no sklearn 1.9).
- [x] Validação (`app/ml/validation.py`): métricas de classificação +
      métricas de **trading após custos reais** (reusa
      `app.backtesting.costs`, não uma fórmula paralela) — nenhum modelo
      é avaliado só por ROC-AUC/acurácia.
- [x] Explicabilidade (`app/ml/explainability.py`): SHAP para modelos em
      árvore, coeficientes para regressão logística.
- [x] Registro de modelos (`app/ml/registry.py`): versionamento,
      serialização (`joblib`) e rollback (reaponta o ponteiro `current`,
      nunca apaga um artefato anterior); aprovação sempre manual.
- [x] Comandos CLI `ml build-dataset`, `ml train`, `ml evaluate`.
- [x] Testes unitários e de integração (328 no total, +63 desta fase) —
      nenhum depende de terminal MT5 nem MySQL real.

Um bug real foi encontrado pelos próprios testes de integração da CLI
(não por inspeção manual): sinais gerados antes do aquecimento de
`required_lookback_bars()` (200 barras) continham features `NaN` (ex.:
`dist_ema_200`) e quebravam o treino de regressão logística — corrigido
pulando essas barras em `build_signal_dataset` em vez de imputar um
valor. Um segundo ajuste (não um bug, mas uma correção de robustez): o
`cv` do `CalibratedClassifierCV` é calculado a partir do tamanho da
classe minoritária de calibração, em vez de usar o padrão fixo (5), que
gerava avisos espúrios com conjuntos de calibração pequenos.

Deliberadamente fora do escopo desta fase (documentado em `docs/ml.md`):
walk-forward completo, estabilidade multi-símbolo/multi-período,
`LightGBM` (opcional, sem consumidor concreto ainda) e intervalo de
confiança por bootstrap (usa aproximação normal).

## Critérios de aceite — Fase 9

- [x] Walk-forward de estratégias (`app/backtesting/walk_forward.py`):
      janelas cronológicas contíguas e não sobrepostas (cada uma com
      saldo/instância de estratégia novos); curvas de patrimônio das
      janelas encadeadas (`_stitch_equity_curves`) para métricas
      agregadas coerentes, sem quedas artificiais entre janelas.
      Julgamento de estabilidade explícito: pelo menos metade das
      janelas elegíveis lucrativas E nenhuma janela isolada respondendo
      por mais de 80% do lucro positivo total — a resposta direta do
      prompt mestre contra eleger uma estratégia "pronta" por sorte de
      uma única janela.
- [x] Simulação de Monte Carlo (`app/backtesting/monte_carlo.py`):
      risco de ruína **empírico**, por reamostragem (bootstrap) da ORDEM
      dos trades já realizados — nunca inventa um trade novo. Substitui,
      para quem quiser a estimativa rigorosa, a aproximação puramente
      analítica da Fase 5 (`estimated_risk_of_ruin`, mantida como
      referência rápida).
- [x] Teste de robustez por stress de custos
      (`app/backtesting/robustness.py`): reexecuta o mesmo backtest com
      slippage/comissão multiplicados; `scale_cost_model` funciona
      genericamente tanto para `CostModel` (candle) quanto
      `TickCostModel` (tick), já que ambos compartilham os mesmos nomes
      de campo.
- [x] Walk-forward de ML (`app/ml/walk_forward.py`): janelas EXPANSIVAS
      (treino sempre cresce a partir do início do dataset; teste é
      sempre a próxima fatia cronológica), repetindo a disciplina da
      Fase 8 (embargo, calibração numa fatia separada) em cada janela.
      Uma janela sem dados suficientes (ex.: classe minoritária da
      calibração com menos de 2 amostras) é simplesmente pulada, nunca
      fabricada.
- [x] Critérios formais de aprovação (`app/ml/approval.py`): os 5
      critérios do prompt mestre (seção 12) aplicados sobre o relatório
      de walk-forward — número de trades, edge após custos, estabilidade
      entre períodos, não-dependência de uma janela excepcional,
      calibração razoável (heurística por Brier score). **Nunca aprova
      nada automaticamente** — só produz um veredito por critério; a
      decisão de gravar `approved=True` no registro continua sendo
      manual (`ml train --approve`).
- [x] Comandos CLI `backtest walk-forward`, `backtest monte-carlo`,
      `backtest stress-test` e `ml walk-forward`.
- [x] Testes unitários e de integração (375 no total, +47 desta fase) —
      nenhum depende de terminal MT5 nem MySQL real.

Simplificação documentada (`app/ml/approval.py`): o critério "supera o
baseline" é avaliado como "expectativa por trade positiva depois de
custos" (baseline implícito = 0, não operar) — uma comparação direta
contra a expectativa da própria estratégia SEM o filtro de IA, nas
mesmas janelas, fica para quando essa integração (modelo como filtro de
sinal) existir de fato, na Fase 11+.

## Critérios de aceite — Fase 10

- [x] Máquina de estados do modo do sistema ganha validação e persistência
      reais pela primeira vez (`app/core/system_mode.py` — puro, sem
      import de banco, evitando ciclo — e
      `app/database/repositories/system_setting_repository.py`, que
      persiste em `system_settings` e grava auditoria via
      `AuditLogRepository`). Avanço só um passo por vez, na ordem
      `DISABLED -> DATA_ONLY -> BACKTEST -> REPLAY -> PAPER`; retroceder é
      sempre permitido; `EMERGENCY_STOP` alcançável de qualquer estado
      ativo; `DEMO`/`REAL_LOCKED`/`REAL_ENABLED` **permanecem bloqueados**
      nesta fase — a maquinaria de risco/execução que os tornaria seguros
      só existe a partir da Fase 11.
- [x] Motor de paper trading incremental
      (`app/paper_trading/engine.py`): reusa a mesma interface
      `Strategy`/`Signal` e a mesma regra conservadora de stop-vs-alvo na
      mesma candle das Fases 5/6/7/8, mas processa dados
      **incrementalmente** — cada chamada só avalia as barras novas desde
      o cursor persistido (`system_settings`), nunca reprocessa o
      histórico inteiro. Diferença deliberada em relação ao backtester:
      a execução acontece no fechamento da própria barra do sinal, não na
      abertura da barra seguinte — não há look-ahead bias a evitar
      quando os dados chegam ao vivo (o "agora" já é posterior ao
      fechamento da barra).
- [x] Persistência de posições (`app/database/models/paper_trade.py`,
      migration `0004_paper_trades.py`): no máximo uma posição `OPEN` por
      símbolo/timeframe/estratégia, garantida a nível de aplicação
      (`PaperTradeRepository`); sobrevive a reinício do processo.
- [x] Comandos CLI `mode show`/`mode set` e `paper run`/`paper status` —
      `paper run` recusa rodar fora do modo `PAPER`.
- [x] Testes unitários e de integração (432 no total, +57 desta fase) —
      nenhum depende de terminal MT5 nem MySQL real.

Bug real encontrado pelos próprios testes (não por inspeção manual): o
SQLite (via SQLAlchemy) devolve `DateTime(timezone=True)` como *naive* na
leitura, mesmo quando o valor originalmente gravado era *aware* — uma
candle recém-buscada do MetaTrader (aware) comparada/subtraída contra uma
posição já persistida e relida do banco (naive) levantava `TypeError`.
Corrigido normalizando para naive (`_as_naive`) antes de qualquer
comparação/subtração de datetime dentro do motor — consistente com a
convenção do projeto de timestamps sempre em UTC.

Simplificação deliberada, documentada em `app/paper_trading/engine.py`:
na primeiríssima chamada de `step()` para um símbolo/timeframe/estratégia
(sem cursor ainda), só a barra mais recente conta como "nova" — paper
trading começa a partir de agora, nunca retroativamente reproduzindo
sinais históricos como se fossem novos.

## Critérios de aceite — Fase 11

- [x] Motor de risco com poder de veto (`app/risk/`): `RiskLimits`
      configurável, circuit breakers de 4 níveis
      (`NONE`/`WARNING`/`SOFT_BLOCK`/`HARD_BLOCK`/`EMERGENCY_STOP`,
      `app/risk/circuit_breaker.py`), dimensionamento de posição a partir
      do risco (`app/risk/position_sizing.py` — **sem nenhum parâmetro
      relacionado a resultado de trade anterior**, tornando martingale/
      soros estruturalmente impossíveis) e `evaluate_signal`
      (`app/risk/engine.py`) combinando tudo numa decisão auditável
      (`RiskDecision`: `approved`, `reason`, `circuit_breaker_level`,
      `computed_volume` — nunca um booleano isolado).
- [x] Regras inegociáveis do prompt mestre (seção 2) implementadas em
      código, não apenas em documentação: sinal sem stop-loss é
      rejeitado incondicionalmente; `SOFT_BLOCK` (perdas consecutivas)
      bloqueia novas entradas antes de qualquer "recuperação compulsiva
      de perdas"; `HARD_BLOCK`/`EMERGENCY_STOP` (prejuízo diário) idem.
- [x] Envio de ordem real a conta demo (`app/mt5/orders.py:
      send_market_order`), com stop-loss/take-profit anexados ao próprio
      pedido — o broker, não este processo, fecha a posição. Recusa
      incondicionalmente (`MT5RealAccountError`) qualquer conta que não
      seja demo — checado a cada iteração de `demo run`, não apenas uma
      vez.
- [x] Máquina de estados de ordem (`app/execution/order_state.py`:
      `SIGNAL_CREATED -> RISK_REJECTED/RISK_APPROVED -> ORDER_CHECKED ->
      ORDER_SENT -> POSITION_OPEN/REJECTED/CANCELLED -> CLOSE_PENDING/
      RECONCILING -> CLOSED`), pura e testável em isolamento — mesmo
      padrão de `app.core.system_mode`.
- [x] `DemoExecutionEngine` (`app/execution/engine.py`): mesmo desenho
      incremental/persistido do `PaperTradingEngine` (Fase 10), com
      reconciliação contra o estado real do MetaTrader 5 (`positions_get`
      + `history_deals_get`) — nunca envia uma ordem de fechamento por
      conta própria, nunca inventa preço/resultado de saída quando a
      divergência não pode ser explicada (`PositionReconciling`).
- [x] Toda avaliação de sinal gera uma linha em `live_trades`
      (`app/database/models/live_trade.py`, migration `0005`) — inclusive
      as rejeitadas pelo risco ou pelo broker — nenhum sinal é descartado
      silenciosamente.
- [x] `app.core.system_mode` estendido: `PAPER -> DEMO` agora permitido;
      `REAL_LOCKED`/`REAL_ENABLED` continuam bloqueados
      incondicionalmente.
- [x] Comandos CLI `demo run` (exige modo `DEMO`) e `demo status`.
- [x] Testes unitários e de integração (528 no total, +96 desta fase) —
      nenhum depende de terminal MT5 nem MySQL real; `FakeMT5Client`
      estendido com `order_send`/`order_check` fakes.

Nenhum bug real precisou de correção nesta fase além de um ajuste de
design descoberto pelos próprios testes: a primeira versão de
`_reconcile` retornava `PositionReconciling` também quando a posição
continuava normalmente aberta (nenhuma mudança de estado) — isso teria
impresso "reconciliação pendente" a cada poll de uma posição saudável.
Corrigido fazendo `_reconcile` retornar `None` (nenhum evento) nesse
caso, reservando `PositionReconciling` para quando o broker não reporta
mais a posição E nenhum deal de fechamento correspondente é encontrado.

Deliberadamente fora do escopo desta fase (documentado em
`docs/execution.md`): preenchimento parcial monitorado, fechamento de
posição por decisão do sistema (`CLOSE_PENDING` ativo), múltiplos
símbolos/estratégias numa única invocação, e — mais importante —
`REAL_LOCKED`/`REAL_ENABLED`, que exigem toda a confirmação manual
multi-etapa do prompt mestre (seção 2).

## Critérios de aceite — Fase 12

- [x] Dashboard HTML somente leitura (FastAPI + Jinja2 + Bootstrap,
      decisão registrada em `docs/architecture.md` seção 6, implementada
      pela primeira vez nesta fase): `/dashboard` (visão geral + modo do
      sistema), `/dashboard/paper-trades`, `/dashboard/live-trades`,
      `/dashboard/models`, `/dashboard/audit-log`. Nenhuma rota HTML muda
      estado — mudar modo/aprovar modelo/rodar paper-demo continuam
      exclusivos da CLI.
- [x] Autenticação do dashboard via cookie `httpOnly`
      (`get_current_user_for_web`, `app/api/dependencies/auth.py`) —
      reusa o MESMO JWT/segredo da API (`/api/auth/login`), apenas com
      transporte diferente (cookie em vez de header `Authorization`,
      que o navegador não anexa sozinho numa navegação de página).
      Cookie ausente/inválido/expirado redireciona para `/login`
      (`RedirectToLogin` + exception handler), nunca um 401 cru numa
      página HTML.
- [x] `PaperTradeRepository.list_all_recent`/`LiveTradeRepository.
      list_all_recent` (novos): consulta com `JOIN` contra `symbols` para
      resolver o nome do símbolo numa única query, cobrindo todos os
      símbolos/estratégias (as consultas anteriores, Fases 10/11, eram
      sempre escopadas a um símbolo/timeframe/estratégia específico).
- [x] Testes unitários e de integração (542 no total, +14 desta fase) —
      `TestClient` real da aplicação FastAPI, cookies de sessão
      persistindo entre chamadas como um navegador real.

Nenhum bug real precisou de correção nesta fase. Um ajuste de teste foi
necessário: `system_mode` é um valor global compartilhado por toda a
suíte (a mesma razão já documentada na Fase 10) — o teste do dashboard
não pode assumir que o modo é `DISABLED`, precisa ler o valor atual
antes de asserir que ele aparece na página renderizada.

Deliberadamente fora do escopo desta fase (documentado em
`docs/dashboard.md`): paginação de tabelas, métricas de backtest/
walk-forward persistidas (não existe uma tabela `backtest_runs` ainda),
atualização automática via WebSocket, renovação automática de sessão.

## Critérios de aceite — Fase 13

- [x] Detecção de drift de features (`app/monitoring/drift.py`):
      `compute_psi` implementa o Population Stability Index (limiares
      padrão da indústria — `0.10`/`0.25` — não inventados para este
      projeto); `detect_feature_drift` compara o conjunto de teste salvo
      no registro de um modelo (`ModelRegistry.load_test_set`, Fase 8)
      contra um dataset recente, uma linha por feature numérica
      compartilhada entre os dois.
- [x] Detecção de drift de métricas: `detect_metric_drift` compara uma
      métrica recente contra o valor gravado no manifesto no momento do
      treino (nunca contra um número "esperado" arbitrário) — funciona
      tanto para métricas "maior é melhor" (expectativa) quanto "menor é
      melhor" (Brier score), sempre expressando piora como `degradation_
      pct` positivo, independente da direção da métrica.
- [x] Persistência seletiva (`app/database/models/drift_event.py`,
      migration `0006`): só ocorrências `WARNING`/`CRITICAL` viram uma
      linha em `drift_events` — um resultado `NONE` (sem drift) nunca é
      gravado, mesmo raciocínio de uma tabela de alertas (não um log de
      aplicação que registra "está tudo bem" a cada execução).
- [x] Fecha uma pendência deixada em aberto na Fase 11
      (`docs/risk-management.md` §1, "bloqueio por dados atrasados"):
      `app/risk/feed_health.py` (`check_feed_health`) mede a idade do
      feed e `app.risk.engine.evaluate_signal` agora rejeita
      incondicionalmente um sinal quando o feed está atrasado além de
      `RiskLimits.max_feed_delay_seconds`. `DemoExecutionEngine` ganhou
      um `clock` injetável (Fase 13) — horário de parede real, nunca
      inferido do horário das próprias candles — para essa checagem
      funcionar corretamente ao vivo e de forma determinística nos
      testes.
- [x] Comandos CLI `monitor model` (compara um modelo registrado contra
      um dataset recente) e `monitor feed` (verifica se o feed de um
      símbolo/timeframe está atualizado) — ambos só relatam e persistem
      ocorrências; nenhum decide sozinho desativar um modelo ou parar o
      sistema.
- [x] Página `/dashboard/drift` (+ card "Drift recente" na visão geral)
      — mesma convenção somente-leitura das demais páginas do dashboard
      (Fase 12).
- [x] Testes unitários e de integração (577 no total, +35 desta fase).

Nenhum bug de produção precisou de correção nesta fase, mas um ajuste de
design real foi necessário durante o desenvolvimento: adicionar a
checagem de feed exigia uma noção de "agora" — usar o horário das
próprias candles sintéticas dos testes (datadas em 2026-01-05) quebrava
todos os testes existentes do `DemoExecutionEngine` assim que comparados
contra `datetime.now(UTC)` real (2026-07-22). Resolvido injetando um
`clock: Callable[[], datetime]` no motor (padrão: `datetime.now(UTC)`),
permitindo testes determinísticos sem acoplar a checagem de saúde do
feed ao horário arbitrário das candles de teste. Mesma classe de
problema já vista com `system_mode` (Fase 10) e `drift_events` (esta
fase): tabelas/checagens globais, sem uma chave natural por teste, foram
isoladas em um engine SQLite próprio (`isolated_session`) em vez do
`db_session` compartilhado por toda a suíte.

Deliberadamente fora do escopo desta fase (documentado em
`docs/monitoring.md`): decisão automática a partir de um drift detectado
(retreinar, desativar, reverter versão — permanece sempre manual);
drift multivariado (só univariado, feature por feature); monitoramento
contínuo/agendado (os comandos `monitor model`/`monitor feed` são
ad-hoc, rodados sob demanda, não um processo de fundo).

## Fase 14 — Modelos avançados: adiada

O prompt mestre autoriza considerar LSTM/Transformer/RL **somente se os
modelos tabulares (regressão logística, Random Forest,
HistGradientBoosting, XGBoost — Fase 8/9) mostrarem limitações reais em
produção**. Essa é uma condição observável, não uma etapa automática do
roadmap.

Neste momento não existe essa evidência: o sistema ainda não rodou em
produção real por nenhum período — paper trading (Fase 10) e o executor
em conta demo (Fase 11) foram exercitados apenas em testes automatizados
com dados sintéticos, nunca em uma sessão real e contínua de mercado.
Não há histórico de drift (Fase 13), degradação de calibração ou
desempenho abaixo do esperado que justifique a complexidade adicional
(mais dados, mais tempo de treino, menos explicabilidade) de um modelo
de sequência ou de reforço.

Construir esses modelos agora, sem essa justificativa, contradiria
diretamente a exigência do prompt mestre e arriscaria complexidade sem
benefício comprovado. Por isso a Fase 14 fica **deliberadamente adiada**
— não pulada silenciosamente, não implementada por completude — até que
uma dessas condições seja observada:

- `docs/monitoring.md`/`/dashboard/drift` mostrarem degradação
  persistente de um modelo tabular aprovado, sem que retreino resolva;
- um período real de operação (paper ou demo) evidenciar um padrão que
  os modelos tabulares comprovadamente não capturam (ex.: dependência
  temporal de longo alcance que as features atuais não expressam).

Quando isso acontecer, a Fase 14 retoma exatamente daqui, com a
motivação registrada nesta seção antes de qualquer código ser escrito.

## Critérios de aceite — Fase 15

- [x] Corrigido bug real encontrado por reinspeção (não por teste
      falhando): `/health` reportava `settings.system_mode` — configuração
      estática de boot, sempre `DISABLED` a menos que sobrescrita por
      variável de ambiente — em vez do modo persistido de verdade. Desde a
      Fase 10 (quando a persistência real do modo passou a existir),
      `/health` estava silenciosamente errado sempre que o modo real
      divergia do valor de boot. Corrigido consultando
      `get_current_mode(db)` (`app/api/routes/health.py`), com fallback
      para `settings.system_mode` apenas quando o banco está inacessível.
- [x] Checagens de prontidão operacional (`app/monitoring/preflight.py`):
      `check_secret_key` (segredo padrão/placeholder do `.env.example`
      ainda em uso fora de ambiente de teste), `check_database` (conexão
      viva), `check_migrations_current` (revisão do banco via
      `alembic_version` contra a HEAD real das migrations locais),
      `check_directory_writable` (diretórios de artefato — logs, modelos,
      datasets — graváveis) e `check_mt5_credentials` (credenciais MT5
      configuradas). Cada checagem devolve `OK`/`WARN`/`FAIL` +
      explicação — nunca corrige nada sozinha, só relata para decisão
      humana (`worst_status` agrega o pior resultado).
- [x] Comando `python -m app.cli preflight check` — imprime cada checagem
      e o resultado geral; código de saída `1` só quando há `FALHA`,
      adequado para uso em scripts/tarefas agendadas antes de operar por
      um período estendido.
- [x] `.env.example` corrigido e limpo: adicionadas as variáveis
      `ML_DATASETS_DIR`/`ML_MODELS_DIR` (existiam em `Settings` desde a
      Fase 8, mas nunca tinham sido documentadas no arquivo de exemplo);
      removido o bloco de placeholders de alertas por e-mail/Telegram que
      nenhum código deste projeto jamais leu — substituído por uma nota
      honesta apontando para `/dashboard/drift` e `monitor model/feed`
      (Fase 13) como os únicos mecanismos de alerta que de fato existem.
- [x] `docs/runbook.md` (novo): procedimentos práticos — primeira
      instalação, rotina de operação (progressão de modo + paper/demo),
      checagens periódicas recomendadas, resposta a incidentes
      (`EMERGENCY_STOP`, drift `CRITICAL`, MT5 desconectado/feed atrasado,
      banco inacessível), backup/retenção, e reafirmação literal das
      regras inegociáveis do prompt mestre (seção 2) — nenhum
      procedimento aqui contorna essas regras.
- [x] Testes unitários e de integração (599 no total, +22 desta fase: 19
      de `app/monitoring/preflight.py` + 3 do comando `preflight check` na
      CLI) — nenhum depende de terminal MT5 nem MySQL real.

Um segundo bug real foi encontrado durante a própria escrita desta fase
(não por um teste falhando, mas por inspeção ao redigir `.env.example`):
a primeira versão de `check_secret_key` comparava `settings.
app_secret_key` por igualdade exata contra o placeholder padrão da
classe `Settings` (`CHANGE_ME_in_dot_env`), mas `.env.example` mostra um
placeholder DIFERENTE (`CHANGE_ME_generate_with_openssl_rand_hex_32`) —
ou seja, quem copiasse o `.env.example` literalmente e esquecesse de
editar o segredo **não seria pego pela própria checagem feita para isso**.
Corrigido trocando a comparação por um prefixo (`CHANGE_ME`), com um
teste de regressão dedicado (`test_check_secret_key_fails_on_env_example_
placeholder`) para travar o comportamento.

Deliberadamente fora do escopo desta fase (mesma linha das fases
anteriores): nenhum agendador embutido para rodar `monitor feed`/`monitor
model`/`preflight check` periodicamente (o runbook aponta para Agendador
de Tarefas do Windows/cron externos); nenhuma notificação por canal
externo (e-mail/Telegram/Slack) quando uma checagem falha — só
saída/código de saída da CLI e as páginas do dashboard.

## Fase 15 é a última fase do roteiro explícito do prompt mestre (seção 27)

Com a Fase 15 concluída, as 15 fases numeradas do roteiro estão
endereçadas: 14 delas implementadas e testadas, e a Fase 14 adiada com a
condição de retomada documentada acima (não descartada — pendente de
evidência real de produção que ainda não existe, porque o sistema nunca
rodou fora de testes automatizados com dados sintéticos). Trabalho
adicional a partir daqui é: (a) retomar a Fase 14 quando sua condição for
observada, ou (b) qualquer item já listado como "deliberadamente fora do
escopo" em fases anteriores, caso uma necessidade concreta surja, ou (c)
a maquinaria de `REAL_LOCKED`/`REAL_ENABLED` (confirmação manual
multi-etapa, chave de liberação, prazo de expiração, valor máximo diário
— prompt mestre, seção 2), que permanece bloqueada incondicionalmente em
todas as 15 fases e não tem uma fase própria no roteiro original.

## Próxima fase

Nenhuma — Fase 15 é a última do roteiro explícito de 15 fases do prompt
mestre. Trabalho futuro depende de uma decisão explícita do usuário sobre
qual das opções (a)/(b)/(c) acima perseguir a seguir.
