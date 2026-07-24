# Matriz de Riscos e Gestão de Risco — MT5 AI Scalper

## 1. Matriz de riscos do projeto (Fase 0)

| Risco | Impacto | Probabilidade | Mitigação |
|---|---|---|---|
| Python 3.14 sem wheels para MetaTrader5/XGBoost/LightGBM na Fase 2/8 | Alto (bloqueia fases) | Média | Instalar Python 3.12 pontualmente se ocorrer (ver `assumptions.md` §2.1); `requires-python` não trava em 3.14 |
| MySQL real não disponível ainda | Médio (adia validação de conexão real) | Alta (confirmado nesta fase) | Camada de config pronta + testes com SQLite; comando `db check` para validar quando o servidor existir |
| Spread/latência/slippage inviabilizarem scalping em conta real | Alto (financeiro) | Alta | Backtest por ticks obrigatório antes de qualquer promoção; teste de robustez com custos aumentados (`app/backtesting/robustness.py`, Fase 9) — atraso simulado já configurável no backtest por tick (`--latency-ms`, Fase 7) |
| Overfitting de modelos de ML | Alto (falsa confiança) | Alta | Embargo + calibração (Fase 8); walk-forward + critérios formais de aprovação (`app/ml/walk_forward.py`, `app/ml/approval.py`, Fase 9); múltiplos símbolos ainda não testado |
| Ativação acidental de modo real | Crítico | Baixa (mitigada por design) | Máquina de estados com validação real desde a Fase 10 (`app/core/system_mode.py`): avanço só um passo por vez; `REAL_LOCKED`/`REAL_ENABLED` bloqueados incondicionalmente. `app.mt5.orders.send_market_order` (Fase 11) recusa qualquer conta que não seja demo, verificado a cada iteração — dupla checagem, nunca uma flag booleana |
| Perda de conexão/dados atrasados durante operação | Alto | Média | Circuit breakers (`WARNING`→`HARD_BLOCK`/`EMERGENCY_STOP`) implementados na Fase 11 (`app/risk/circuit_breaker.py`) para perdas consecutivas/prejuízo diário; bloqueio por dados atrasados implementado na Fase 13 (`app/risk/feed_health.py`, rejeita incondicionalmente em `evaluate_signal`) |
| Dependência de uma única corretora/feed incompleto (livro/volume) | Médio | Alta | Sistema não assume livro disponível; estratégias de microestrutura são opcionais |
| Falta de repositório git nesta fase | Baixo | Certa (decisão do usuário) | Decisões registradas em `docs/`; git pode ser inicializado a qualquer momento futuro |

Esta matriz deve ser revisada ao final de cada fase subsequente.

## 2. Gestão de risco de trading (implementada a partir da Fase 11)

O motor de risco (`app/risk`) é independente da estratégia e tem poder de
veto sobre qualquer sinal. Nenhuma fase anterior à 11 envia ordens reais;
a partir da Fase 11, toda ordem passa por `app.risk.engine.
evaluate_signal` antes de `app.mt5.orders.send_market_order` — ver
`docs/execution.md` para os detalhes completos.

Implementado nesta fase (`app/risk/`):
- risco fixo por operação (`risk_per_trade_pct`) e tamanho de posição
  derivado da distância até o stop (`app/risk/position_sizing.py`) —
  nunca em função do resultado de um trade anterior (sem martingale/
  soros, por construção);
- normalização de lote pelas especificações do símbolo (mínimo/máximo/step);
- limites: por símbolo/estratégia (uma instância de `RiskLimits` por
  execução), posições simultâneas, trades/dia, intervalo mínimo entre
  operações, spread máximo;
- circuit breakers em 4 níveis (`app/risk/circuit_breaker.py`): `NONE`,
  `WARNING` (aviso, ainda opera), `SOFT_BLOCK` (perdas consecutivas —
  bloqueia), `HARD_BLOCK` (prejuízo diário — bloqueia),
  `EMERGENCY_STOP` (saldo inicial inválido — bloqueia);
- bloqueio por dados atrasados (Fase 13, `app/risk/feed_health.py`):
  rejeita incondicionalmente um sinal quando o feed está mais velho que
  `RiskLimits.max_feed_delay_seconds`.

Detecção (não bloqueio automático) de degradação de modelo a partir da
Fase 13: `app/monitoring/drift.py` + comandos `monitor model`/`monitor
feed` (ver `docs/monitoring.md`) — relatam e persistem ocorrências,
nunca decidem sozinhos parar o sistema ou desativar uma versão.

Ainda não implementado (sem consumidor concreto nesta fase): limite
agregado/semanal entre múltiplas estratégias ou símbolos simultâneos,
bloqueio automático por modelo degradado (hoje é detecção + alerta, não
veto automático)/divergência de saldo/erro de execução/mudança de
conta/proximidade de notícia, e limite de drawdown como circuit breaker
próprio (hoje coberto indiretamente pelo prejuízo diário).

## 3. Regras inegociáveis (válidas em todas as fases)

Ver lista completa em `docs/security.md` §6 e no prompt mestre, seção 2.
Resumo operacional: sem martingale/soros/grade infinita, sem operação sem
stop-loss, sem uso de dados futuros no treino, sem backtest sem custos, sem
ocultar perdas, sem escolher estratégia só pelo lucro líquido, sem
credenciais no código, sem ativação automática de conta real.
