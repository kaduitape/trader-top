# Paper Trading e Máquina de Estados — Fase 10

Este documento é a versão em prosa de `app/core/system_mode.py` e
`app/paper_trading/engine.py` (a fonte de verdade é o código — mantenha
os dois sincronizados).

## Máquina de estados do sistema

`SystemMode` (`app/core/enums.py`) existe desde a Fase 1, mas nenhuma
fase anterior validava ou persistia transições de fato — o enum existia
"no papel". A Fase 10 é o primeiro ponto em que o modo tem efeito real:
`paper run` só executa se o sistema estiver em `PAPER`.

```text
DISABLED -> DATA_ONLY -> BACKTEST -> REPLAY -> PAPER -> DEMO
                                                          |
                                              REAL_LOCKED -> REAL_ENABLED
                                                          |
                                                  EMERGENCY_STOP (de qualquer estado ativo)
```

Regras impostas por `app.core.system_mode.validate_transition` (não
apenas convenção):

- **Avanço**: só um passo por vez, na ordem acima — nunca pulando
  estado intermediário (ex.: `DISABLED -> PAPER` direto é rejeitado).
- **Retrocesso**: sempre permitido, para qualquer estado anterior —
  parar/resetar o sistema não deveria exigir "desfazer" passo a passo.
- **`EMERGENCY_STOP`**: alcançável a partir de qualquer estado ativo,
  nunca a partir de `DISABLED` (que já é o estado mais seguro).
- **Recuperação de `EMERGENCY_STOP`**: só é permitido voltar para
  `DISABLED` — nunca retomar diretamente de onde parou.
- **`DEMO`, `REAL_LOCKED`, `REAL_ENABLED`**: **permanecem bloqueados**
  nesta fase. A maquinaria que os torna seguros (motor de risco com
  poder de veto, máquina de estados de ordem, confirmação manual
  multi-etapa — prompt mestre, seção 2) só existe a partir da Fase 11.
  Tentar transicionar para qualquer um deles levanta `SystemModeError`
  imediatamente, independente do estado atual.

A validação (`app/core/system_mode.py`) é deliberadamente livre de
acesso a banco — não importa `app.database`, para evitar import
circular e para poder ser testada em isolamento total. A orquestração
que de fato lê/grava o modo persistido (`system_settings`) e escreve
auditoria (`audit_logs`) vive em
`app.database.repositories.system_setting_repository`
(`get_current_mode`/`set_mode`).

```powershell
python -m app.cli mode show
python -m app.cli mode set DATA_ONLY --reason "iniciando coleta"
python -m app.cli mode set BACKTEST
python -m app.cli mode set REPLAY
python -m app.cli mode set PAPER
```

## Motor de paper trading

`PaperTradingEngine` (`app/paper_trading/engine.py`) reusa a mesma
interface `Strategy`/`Signal` das Fases 5/6 e a mesma regra conservadora
de stop-vs-alvo na mesma candle das Fases 5/6/7/8 (se ambas cabem na
mesma barra, assume-se o stop, nunca o resultado favorável) — mas com
duas diferenças deliberadas em relação ao backtester:

1. **Execução no fechamento da própria barra do sinal**, não na
   abertura da barra seguinte. No backtester (Fase 5/7), esperar a
   barra seguinte evita usar dados que ainda não existiam no momento do
   sinal (look-ahead bias) — uma preocupação real ao REPROCESSAR
   histórico. Em paper trading ao vivo, o sinal só é detectado depois
   que a barra já fechou (o "agora" real já é posterior a esse
   fechamento) — não há look-ahead a evitar quando o tempo só anda para
   frente. Executar no fechamento da própria barra é uma aproximação
   razoável do preço de execução ao vivo, sem esperar artificialmente.
2. **Processamento incremental e persistido**: o motor nunca reprocessa
   o histórico inteiro a cada chamada — mantém um cursor persistido
   (`system_settings`, chave por símbolo/timeframe/estratégia) com o
   horário da última barra já avaliada. Na primeiríssima chamada (sem
   cursor ainda), só a barra mais recente é considerada nova — paper
   trading começa "a partir de agora", nunca retroativamente. A posição
   aberta (no máximo uma por símbolo/timeframe/estratégia, imposta pelo
   `PaperTradeRepository`) é persistida em `paper_trades`
   (migration `0004`) e sobrevive a reinício do processo.

### Bug real corrigido durante o desenvolvimento

O SQLite (via SQLAlchemy) devolve `DateTime(timezone=True)` como
**naive** na leitura, mesmo quando o valor originalmente gravado era
*aware* — uma candle recém-buscada do MetaTrader (aware) comparada ou
subtraída contra uma posição já persistida e relida do banco (naive)
levantava `TypeError: can't subtract offset-naive and offset-aware
datetimes`. Corrigido normalizando ambos os lados para naive
(`_as_naive`, assumindo UTC — convenção já usada em todo o projeto)
antes de qualquer comparação/subtração de datetime dentro do motor.

## Comandos CLI

```powershell
# Exige modo PAPER (ver acima)
python -m app.cli paper run --symbol EURUSD --timeframe M1 --strategy ema_crossover --iterations 1

# Roda continuamente, verificando a cada 30s (Ctrl+C para parar)
python -m app.cli paper run --symbol EURUSD --timeframe M1 --iterations 999999 --poll-seconds 30

# Lista os paper trades mais recentes (abertos e fechados) de um simbolo/estrategia
python -m app.cli paper status --symbol EURUSD --strategy ema_crossover
```

## Limitações e decisões conhecidas

- `bars_held` é calculado por tempo decorrido dividido pelo tamanho da
  barra (`bar_seconds`), não por contagem de índice — necessário porque
  a barra de entrada pode já ter saído da janela de lookback buscada num
  poll posterior.
- Não há gestão de risco real (dimensionamento de posição, limites
  diários, circuit breakers) — isso é `app/risk`, Fase 11.
- Não há dashboard/visualização — `paper status` é a única forma de
  inspecionar o histórico nesta fase (Fase 12 trata o dashboard).
- O modelo de custos é o mesmo `CostModel` do backtest por candle
  (spread do símbolo, slippage, comissão configuráveis) — sem latência
  de execução simulada (isso existe no backtest por tick, Fase 7, mas
  paper trading nesta fase só processa candles, não ticks).
