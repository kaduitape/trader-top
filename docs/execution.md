# Executor em Conta Demo — Fase 11

Este documento é a versão em prosa de `app/risk/` e `app/execution/` (a
fonte de verdade é o código — mantenha os dois sincronizados).

## O que muda nesta fase

Até a Fase 10, nenhum sinal virava uma ordem de verdade — paper trading
(Fase 10) apenas registrava o que *teria* acontecido. A Fase 11 é a
primeira vez que o sistema envia uma ordem real ao MetaTrader 5 — sempre
contra uma conta **DEMO**, nunca uma conta real. Dois portões de
segurança independentes garantem isso:

1. **Máquina de estados do sistema** (`app.core.system_mode`): o comando
   `demo run` só executa se o modo persistido for `DEMO` — e `DEMO` só é
   alcançável avançando um passo por vez a partir de `PAPER`.
2. **`app.mt5.orders.send_market_order`**: recusa (`MT5RealAccountError`)
   qualquer chamada onde `AccountSnapshot.is_demo` seja `False`, a menos
   que quem chama passe `allow_real_account=True` — o que só acontece no
   caminho de operação em modo REAL (`/dashboard/trading`). `demo run`
   nunca passa esse argumento. A guarda vale nas duas direções: com
   `allow_real_account=True`, uma conta **demo** também é recusada, para
   que ninguém opere "real" contra uma conta que não é. Verificado de novo
   a cada iteração (a conta pode mudar entre polls), não só no início.

Entre esses dois portões fica o motor de risco (`app.risk`), com poder
de veto sobre todo sinal — nenhuma ordem é sequer verificada
(`order_check`) sem sua aprovação explícita.

## Motor de risco (`app/risk/`)

| Módulo | Responsabilidade |
|---|---|
| `config.py` | `RiskLimits` — limites configuráveis, valores padrão conservadores. |
| `circuit_breaker.py` | `classify_circuit_breaker` — 4 níveis (`NONE`/`WARNING`/`SOFT_BLOCK`/`HARD_BLOCK`/`EMERGENCY_STOP`), puramente funcional. |
| `position_sizing.py` | `compute_position_size` — dimensiona o lote a partir do risco. |
| `engine.py` | `evaluate_signal` — combina tudo acima numa única decisão auditável. |

### Regras inegociáveis implementadas em código (prompt mestre, seção 2)

- **Sem stop-loss, sem aprovação**: `signal.stop_loss == signal.
  reference_price` é rejeitado incondicionalmente.
- **Sem martingale/soros**: `compute_position_size` não tem — nem pode
  ter, por construção — nenhum parâmetro relacionado ao resultado de
  trades anteriores. É sempre `risk_pct` fixo do saldo **atual**,
  recalculado do zero a cada sinal a partir da distância até o stop
  *deste* sinal. Testado explicitamente
  (`test_position_sizing_does_not_depend_on_consecutive_losses`).
- **Circuit breakers bloqueiam ANTES do prejuízo**: `SOFT_BLOCK` (perdas
  consecutivas — "sem recuperação compulsiva de perdas") e
  `HARD_BLOCK`/`EMERGENCY_STOP` (prejuízo diário) rejeitam qualquer sinal
  novo incondicionalmente; só `WARNING` permite continuar operando (com
  o aviso explícito no motivo da decisão — nunca escondido).
- **Conta real nunca opera aqui**: `evaluate_signal` também rejeita se
  `account.is_demo` for `False` — defesa em profundidade, redundante com
  o portão de `send_market_order`.

`RiskDecision` nunca esconde o motivo: `approved=False` sempre vem
acompanhado de `reason` (string legível) e `circuit_breaker_level`
explícito — nunca um booleano isolado.

## Máquina de estados de ordem (`app/execution/order_state.py`)

```text
SIGNAL_CREATED -> RISK_REJECTED
               -> RISK_APPROVED -> ORDER_CHECKED -> ORDER_SENT -> POSITION_OPEN
                                                               -> REJECTED / CANCELLED
                    POSITION_OPEN -> CLOSE_PENDING -> CLOSED
                    POSITION_OPEN -> RECONCILING -> CLOSED | POSITION_OPEN
```

Simplificação deliberada frente à máquina de estados completa do prompt
mestre: para ordens a mercado (retail, sem profundidade de livro),
`order_send` do MetaTrader 5 é **síncrono** — o retcode já informa
sucesso ou rejeição imediatamente. Por isso `ORDER_ACCEPTED`/
`PARTIALLY_FILLED`/`FILLED` são tratados como um único resultado
síncrono (`POSITION_OPEN` ou `REJECTED`), não estados intermediários
monitorados separadamente.

Puramente funcional (`validate_order_transition`), mesmo padrão de
`app.core.system_mode` — testável em isolamento, sem import de
`app.database` (evita ciclo).

## Motor de execução (`app/execution/engine.py`)

`DemoExecutionEngine` reusa o mesmo desenho incremental e persistido do
`PaperTradingEngine` (Fase 10): um cursor (`system_settings`) evita
reprocessar o histórico inteiro a cada chamada; na primeiríssima
chamada, só a barra mais recente conta como nova.

Diferença central: **quem fecha a posição é o broker**, não este
processo. O stop-loss/take-profit vão anexados ao próprio pedido de
`order_send` — o broker os executa do lado dele. O motor nunca envia uma
ordem de fechamento por conta própria; a reconciliação (`_reconcile`)
apenas detecta que uma posição já foi fechada pelo broker:

1. Consulta `positions_get` — se o ticket ainda aparece, a posição
   continua aberta de verdade e **nenhum evento é emitido** (estado
   normal, não uma pendência).
2. Se o ticket não aparece mais, procura em `history_deals_get` (janela
   de 7 dias) o deal de fechamento (`DEAL_ENTRY_OUT`) correspondente.
   Encontrado → fecha a posição localmente com o preço/lucro reais do
   deal (`PositionClosed`). Não encontrado → marca `RECONCILING` e emite
   `PositionReconciling` — **nunca inventa um preço ou resultado de
   saída** nesse caso; fica sinalizado para revisão manual.

Toda avaliação de sinal gera uma linha em `live_trades`
(`app/database/models/live_trade.py`, migration `0005`) — inclusive
quando rejeitada pelo risco (`RISK_REJECTED`) ou pelo broker
(`REJECTED`) — nenhum sinal é descartado silenciosamente, satisfazendo a
exigência de auditoria completa do prompt mestre.

## Comandos CLI

```powershell
python -m app.cli mode set DEMO   # exige ja estar em PAPER

python -m app.cli demo run --symbol EURUSD --timeframe M1 --strategy ema_crossover `
    --risk-per-trade-pct 1.0 --max-daily-loss-pct 3.0 --max-consecutive-losses 3 `
    --iterations 1

python -m app.cli demo status --symbol EURUSD --strategy ema_crossover
```

`demo run` verifica a conta conectada a **cada iteração** (não apenas
uma vez): se o modo é `DEMO` mas a conta não é (`AccountSnapshot.
is_demo=False`), a iteração inteira aborta com erro antes de qualquer
coleta ou avaliação de sinal.

## Limitações e decisões conhecidas

- Sem preenchimento parcial monitorado (`PARTIALLY_FILLED`) — ordens a
  mercado retail são resolvidas sincronamente pelo próprio `order_send`.
- Sem `CLOSE_PENDING` ativo (fechar uma posição por decisão do sistema,
  não do broker) — todo fechamento nesta fase vem do stop-loss/take-
  profit anexado ao pedido original.
- Reconciliação consulta uma janela fixa de 7 dias em
  `history_deals_get` — suficiente para qualquer atraso razoável entre
  polls, mas não uma busca ilimitada.
- Sem múltiplos símbolos/estratégias simultâneos numa única invocação de
  `demo run` — uma execução cobre um símbolo/timeframe/estratégia por
  vez (mesma simplificação do paper trading, Fase 10).
- `REAL_LOCKED`/`REAL_ENABLED` continuam bloqueados incondicionalmente
  (`app.core.system_mode.NOT_YET_IMPLEMENTED_MODES`) — ainda fora de
  escopo.
