# ApexFlow AI

> Advanced Tick Flow & Price Action Engine

Motor de decisão que interpreta o **comportamento** do mercado em tempo real
— fluxo de ticks, microestrutura, price action e contexto multi-timeframe —
em vez de reagir a um cruzamento de indicador. RSI, MACD e médias entram
apenas como contexto no vetor de features; **nenhum deles é gatilho**.

A saída tem exatamente três possibilidades:

**COMPRAR** · **VENDER** · **NÃO OPERAR**

E a abstenção é a resposta padrão. Não existe caminho no código que force
uma entrada para "aproveitar" um ciclo.

## Arquitetura

Módulos independentes, todos puros (sem banco, sem MetaTrader, sem envio de
ordem) exceto o orquestrador e o journal:

```
ticks ──► tick_flow ──┬──► spread ────┐
                      ├──► volatility ┤
candles ──► features ─┼──► momentum ──┼──► context ──► feature vector ──► decisão
                      ├──► liquidity ─┤                      │
                      └──► mtf ───────┘                      └──► journal
```

| Módulo | Arquivo | Responsabilidade |
|--------|---------|------------------|
| Tick Collector | `app/apexflow/tick_flow.py` | Buffer circular O(1) e métricas de fluxo |
| Market Context | `app/apexflow/context.py` | Nove regimes de mercado |
| Multi-Timeframe | `app/apexflow/mtf.py` | Papéis por timeframe |
| Momentum | `app/apexflow/momentum.py` | Aceleração, força, exaustão, persistência |
| Volatility | `app/apexflow/volatility.py` | ATR, volatilidade de segundos, expansão |
| Spread | `app/apexflow/spread.py` | Três vetos de spread |
| Liquidity | `app/apexflow/liquidity.py` | Sweep, caça de stops, falso rompimento |
| Feature Vector | `app/apexflow/features.py` | Vetor nomeado, ordenado e versionado |
| AI Decision | `app/apexflow/decision.py` | Probabilidades e ação final |
| Risk Manager | `app/apexflow/risk.py` | Alvo dinâmico, trailing, break-even, limites |
| Learning Engine | `app/apexflow/journal.py` | Registra toda decisão para reavaliação |
| Orquestrador | `app/apexflow/engine.py` | Une tudo, lê o banco, devolve a análise |

## 1. Tick Collector

`TickBuffer` é um `deque` com `maxlen`: inserção e descarte O(1), memória
constante, nada de consulta lenta. Todas as métricas saem de **uma passada**
sobre a janela.

Métricas: ticks/s, aceleração (metade recente vs. anterior), intervalo médio
e máximo, razão de upticks, viés direcional, velocidade do preço,
**caminho percorrido**, **eficiência** (deslocamento líquido ÷ caminho),
spread atual/médio/máximo, tendência do spread e latência do feed.

A eficiência é a métrica que separa movimento direcional limpo (perto de 1)
de vaivém que não sai do lugar (perto de 0).

Quando a janela não tem ticks suficientes, a métrica vem `None` e o motivo
fica em `warnings`. Nenhum número é estimado para preencher lacuna.

## 2. Market Context Engine

Nove estados, avaliados em ordem de **prioridade** — do mais perigoso ao
mais operável, e o primeiro que casa vence:

| # | Estado | Opera? |
|---|--------|--------|
| 1 | `WIDE_SPREAD` — spread alto | não |
| 2 | `EXPLOSIVE` — explosão de fluxo / evento | não |
| 3 | `POST_NEWS` — até 30 min após evento de alto impacto | não |
| 4 | `LIQUIDITY_HUNT` — manipulação em andamento | não |
| 5 | `HIGH_VOLATILITY` — ATR ≥ 2× a média | não |
| 6 | `DEAD` — sem amplitude para pagar o custo | não |
| 7 | `ILLIQUID` — fluxo de ticks raro demais | não |
| 8 | `TRENDING` | **sim** |
| 9 | `RANGING` | **sim** |

A ordem importa: um mercado simultaneamente explosivo e com spread largo é
reportado pelo motivo que **bloqueia**, não pelo que parece oportunidade.
Cada estado leva parâmetros próprios — nunca uma configuração fixa para
todos.

## 3. Multi-Timeframe: papéis fixos

| Timeframe | Papel | Responde | Peso |
|-----------|-------|----------|------|
| H1 | MACRO | Para que lado o mercado está inclinado? | 40% |
| M15 | CONTEXTO | A estrutura permite operar esse lado? | 30% |
| M5 | CONFIRMAÇÃO | O movimento está se desenvolvendo? | 20% |
| M1 | TIMING | O gatilho apareceu agora? | 10% |
| tick | EXECUÇÃO | O fluxo suporta o preço de entrada? | veto |

**H1 nunca gera entrada** — regra imposta por código
(`ENTRY_TIMEFRAMES` + `ensure_entry_timeframe`), não convenção.

Um timeframe ausente **reduz** o alinhamento em vez de ser normalizado para
fora do cálculo: lacuna nunca conta como concordância.

## 4. Vetos antes da probabilidade

```
        vetos duros  ──────────────────────────────► NÃO OPERAR
             │ (nenhum disparou)
             ▼
    modelo de probabilidade ──► p(compra), p(venda), p(abstenção)
             │
             ▼
      confiança >= limite?  ──── não ──────────────► NÃO OPERAR
             │ sim
             ▼
        COMPRAR / VENDER
```

Os vetos vêm **antes** do modelo e não podem ser sobrepostos por ele. Um
modelo com 99% de confiança em COMPRAR continua recusado se o spread
estourou o limite (`test_veto_beats_a_maximally_confident_model`).

Vetos duros: contexto não operável, spread fora dos três critérios,
volatilidade insuficiente, caça de liquidez ativa, fluxo de ticks não
confiável, cobertura de sensores abaixo do mínimo.

### Os três vetos de spread

1. **Acima do limite** — absoluto, configurado pelo operador.
2. **Alargando rápido** — spread recente ≥ 1,6× o anterior.
3. **Incompatível com o alvo** — consome mais de 20% do alvo. É o veto mais
   esquecido e o que mais protege: um alvo de 20 pontos com spread de 8
   nasce com expectativa negativa.

## 5. Dois cérebros, mesma interface

- **`ScorecardModel`** (padrão) — evidência ponderada, determinística e
  totalmente explicável: cada contribuição aparece em
  `ApexFlowDecision.evidence`. Sem treino, sem overfitting, sem caixa preta.
  É o baseline honesto contra o qual um modelo treinado precisa provar que é
  melhor.
- **Qualquer `ProbabilityModel`** — um modelo treinado e aprovado no
  registro (`app/ml/registry.py`) substitui o scorecard sem tocar em mais
  nada, porque consome o mesmo `FeatureVector` versionado.

A abstenção começa com peso alto (`BASE_ABSTAIN = 0.35`) de propósito: não
operar é o estado natural, e a evidência precisa vencer essa inércia.
Evidência contraditória vira **probabilidade de abstenção**, nunca um empate
desempatado por ruído.

## 6. Feature Vector

Três propriedades obrigatórias, todas testadas:

1. **Ordem estável** — `FEATURE_NAMES` é a fonte única; um modelo treinado
   hoje nunca recebe as colunas embaralhadas amanhã.
2. **Versionamento** — qualquer mudança em nomes/ordem exige subir
   `FEATURE_VERSION`. O journal grava a versão com cada decisão.
3. **Ausência declarada** — feature indisponível vale `None` no dicionário e
   um valor neutro no vetor numérico, com `missing_mask()` indicando quais
   foram preenchidas. Nunca se confunde "zero" com "não sei".

## 4b. Estruturas de gráfico

`app/apexflow/structures.py` completa o Price Action Engine com o que não
cabe em uma ou três velas — `price_action.py` continua responsável pelos
padrões de candle (pin bar, engolfo, doji, inside/outside bar, fakey, falso
rompimento):

| Estrutura | Critério |
|-----------|----------|
| Topo / fundo duplo | dois extremos dentro de 0,35 ATR **com** recuo ≥ 0,8 ATR entre eles |
| Compressão / expansão | amplitude média das últimas 20 barras ≤ 0,70× ou ≥ 1,40× a das 20 anteriores |
| Acumulação / distribuição | faixa estreita **com volume alto** — perto do fundo é acumulação, perto do topo é distribuição |
| Range | largura ≤ 2,5 ATR com ≥ 2 toques em cada lado |
| Micro tendência | movimento ininterrupto ≥ 0,5 ATR nas últimas 5 barras |
| Micro pullback | micro tendência com a última barra contra, **sem** desfazer o movimento |

Duas distinções fazem o trabalho aqui: faixa estreita com volume baixo é só
**compressão** (falta de interesse), enquanto com volume alto é **absorção**;
e um recuo que desfaz todo o movimento é **reversão**, não pullback.
Tolerâncias em ATR, nunca em pontos fixos — o mesmo critério serve para
EURUSD e XAUUSD.

## 7. Risk Manager

O que o módulo **nunca** faz, por arquitetura — não existe parâmetro que
ligue nada disto: martingale, grid, dobrar lote, recuperação de prejuízo.

O que faz:

- **Take profit dinâmico** — parte de `risk_reward_min` e estica com a
  volatilidade; nunca encolhe abaixo do mínimo.
- **Break-even** — a partir de `break_even_r`, tira o risco da mesa.
- **Trailing stop** — a partir de `trailing_start_r`, trava lucro em passos
  de `trailing_step_r`.
- **Limites do dia** — perda diária, **meta de lucro** (bater a meta é motivo
  legítimo para parar), perdas consecutivas e drawdown desde o pico.

Duas regras invioláveis, ambas testadas:

1. **O stop nunca anda para trás.** Uma modificação que aumentaria o risco é
   recusada mesmo que a fórmula a proponha.
2. **Break-even antes de trailing.**

As funções de cálculo são puras: recebem números, devolvem uma **intenção**.
Quem executa é `app/execution/position_manager.py`, que envia
`modify_position` (`TRADE_ACTION_SLTP` — altera só os níveis de proteção,
nunca abre nem fecha) e persiste o novo stop **somente depois** da corretora
aceitar: gravar um nível recusado faria o sistema acreditar em uma proteção
que não existe.

O gerenciamento roda **antes de tudo** em cada ciclo, e continua rodando
mesmo quando o dia já está fechado para novas entradas — uma posição aberta
precisa seguir protegida. Os limites do dia (perda, lucro, perdas seguidas,
drawdown intradiário desde o pico) são avaliados em seguida e, quando
disparam, encerram o dia para entradas novas sem tocar na posição aberta.

`live_trades.initial_stop_loss` (migration 0010) preserva o risco com que a
operação nasceu, porque o trailing reescreve `stop_loss` — sem ele, 1R
ficaria incalculável exatamente nas operações que deram certo.

## 8. Learning Engine

Tabela `apexflow_decisions` (migration 0009). Uma linha por decisão —
**inclusive as de NÃO OPERAR**, que são a maioria. Registrar só as entradas
produziria um histórico enviesado: sem as abstenções é impossível descobrir
depois se o robô deixou passar boas oportunidades ou acertou ao ficar fora.

Cada linha guarda o `feature_vector` completo em JSON junto de
`feature_version`, permitindo reavaliar um modelo novo contra exatamente os
sensores que o motor tinha naquele instante.

O loop é **fechado**: quando a decisão vira ordem, `attach_live_trade` liga
as duas; quando a reconciliação detecta o fechamento, `record_trade_result`
anexa resultado e R realizado (calculado sobre `initial_stop_loss`). É isso
que faz win rate, profit factor e expectância saírem de "amostra
insuficiente" — sem esse fechamento as métricas ficariam em zero para
sempre.

Métricas agregadas (win rate, profit factor, expectância) retornam `None` —
não zero — abaixo de `MIN_TRADES_FOR_STATISTICS`. Um profit factor calculado
sobre duas operações é uma mentira mais perigosa que a ausência do número.

## Uso

### Dashboard

`/dashboard/apexflow` — probabilidades ao vivo (compra/venda/abstenção),
ticks/s, ATR, spread, alinhamento MTF, **latência do feed**, **drawdown do
dia**, resultado do dia, estado do robô, desempenho registrado e as últimas
decisões. Mostra também o motivo da parada quando um limite do dia dispara e
o último movimento de stop aplicado. Atualiza sozinho a cada 5 segundos.

Marcar **"Usar o ApexFlow AI para decidir"** troca o cérebro do piloto
automático. **Isso não liga o robô**: ligar continua exigindo os portões de
`/dashboard/trading` (modo `DEMO`/`REAL` compatível com a conta conectada).

### CLI

```powershell
python -m app.cli apexflow analyze --symbol XAUUSD --timeframe M5
python -m app.cli apexflow analyze --symbol XAUUSD --json
python -m app.cli apexflow history --symbol XAUUSD
```

`analyze` é consultivo: explica a decisão sem enviar ordem nem gravar no
journal.

### Como parte do piloto automático

Com `engine="apexflow"`, o ciclo do piloto (`app/execution/autopilot.py`)
passa a decisão para o ApexFlow. Tudo o que vem depois é **idêntico** ao
caminho por playbook: motor de risco com poder de veto, `DemoExecutionEngine`,
conta demo obrigatória, auditoria. O cérebro muda; os limites, não.

## Limitações declaradas

- **Volume real não existe em Forex.** O `tick_volume` do MetaTrader é uma
  contagem de atualizações, não volume negociado — é o que o motor usa, e
  está documentado como tal (`docs/assumptions.md`).
- **Delta e book de ofertas** não estão implementados: dependem de dados que
  a maioria das corretoras de Forex não fornece de forma confiável. O vetor
  tem espaço para eles quando existirem.
- **As janelas de sessão ignoram horário de verão** (até 1 h de
  deslocamento) — por isso o horário é hipótese e o volume medido é a prova.
- **O `ScorecardModel` não foi validado em produção.** Ele é um baseline
  determinístico e explicável, não um modelo treinado. Qualquer alegação de
  desempenho precisa vir do `apexflow history` sobre operações reais.
- **O feature vector está em `apexflow-2`.** Um modelo treinado sobre
  `apexflow-1` não pode receber o vetor atual sem retreino: as colunas de
  VWAP e das nove estruturas mudaram o tamanho do vetor.
- **Não há benchmark de latência medido.** A arquitetura prioriza baixo
  custo (buffer circular O(1), uma passada por métrica, nenhuma consulta
  por tick), e a latência do feed é medida e exibida — mas não existe
  número de referência de latência de decisão fim a fim.
