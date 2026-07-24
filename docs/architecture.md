# Arquitetura — MT5 AI Scalper

## 1. Visão geral

MT5 AI Scalper é uma plataforma modular para pesquisa, simulação e execução
controlada de estratégias de scalping no MetaTrader 5. A construção segue 15
fases incrementais (ver `docs/development-phases.md`); este documento descreve
a arquitetura-alvo completa, mesmo que apenas uma fração exista após a
Fase 1.

## 2. Princípios arquiteturais

1. **Separação domínio / infraestrutura / interface.** Estratégias, risco e
   backtesting não conhecem detalhes do MetaTrader5 ou do FastAPI; falam
   através de interfaces (`Strategy`, repositórios, `Signal`, `Order`).
2. **Risco é independente da estratégia e tem poder de veto.** Nenhuma
   estratégia pode enviar ordens diretamente — todo sinal passa pelo motor de
   risco antes de chegar ao executor.
3. **Máquina de estados explícita para o modo do sistema e para cada ordem.**
   Nunca uma variável booleana solta habilita operação real ou confirma
   execução.
4. **Nada é assumido sobre o MetaTrader.** Toda resposta do terminal é
   conferida (retcode, volume executado, ticket) antes de qualquer decisão.
5. **Auditabilidade total.** Todo sinal e toda ordem carregam identificador
   único, timestamps de cada etapa, versão de estratégia/modelo e motivo.
6. **Modo real desabilitado por padrão**, exigindo liberação manual explícita,
   conta confirmada como real, chave de liberação e expiração.

## 3. Camadas do sistema

```text
┌─────────────────────────────────────────────────────────────┐
│ Interface (dashboard, API REST, WebSocket)                  │
├─────────────────────────────────────────────────────────────┤
│ Serviços de aplicação (app/services)                         │
│  orquestram: estratégias -> risco -> execução -> persistência│
├───────────────┬───────────────┬───────────────┬─────────────┤
│ Estratégias    │ Risco          │ Execução       │ ML/Backtest │
│ (app/strategies│ (app/risk)     │ (app/execution)│ (app/ml,    │
│  , app/market) │                │                │  app/backtesting)│
├───────────────┴───────────────┴───────────────┴─────────────┤
│ Infraestrutura: MT5 connector (app/mt5) | MySQL (app/database)│
│                 | Monitoramento (app/monitoring) | Notícias   │
└─────────────────────────────────────────────────────────────┘
```

- **app/mt5**: única camada que importa o pacote `MetaTrader5`. Todo o resto
  do sistema depende de tipos próprios (dataclasses/Pydantic), nunca de
  tipos da biblioteca do MetaTrader diretamente. Isso permite testar o
  sistema inteiro com mocks, sem terminal MT5 instalado.
- **app/database**: modelos SQLAlchemy, sessões, repositórios. Repositórios
  encapsulam queries; serviços não escrevem SQL/ORM diretamente.
- **app/strategies**: cada família de estratégia é um módulo independente que
  implementa a interface `Strategy.generate_signal(market_state) -> Signal | None`.
- **app/risk** (Fase 11): motor de risco único, chamado sempre entre
  estratégia e execução. Tem poder de veto (`evaluate_signal`) e circuit
  breakers próprios (`NONE`/`WARNING`/`SOFT_BLOCK`/`HARD_BLOCK`/
  `EMERGENCY_STOP`) — ver `docs/execution.md`.
- **app/execution** (Fase 11): máquina de estados por ordem
  (`order_state.py`, pura) e `DemoExecutionEngine` — reconciliação contra
  o estado real do MetaTrader 5. Idempotência e lock por símbolo ainda
  não são necessários numa única instância de processo (uma execução por
  símbolo/estratégia); revisitar se/quando múltiplas instâncias
  concorrentes existirem.
- **app/ml**: pipeline de dataset, labels (triple-barrier), treino,
  calibração, explicabilidade, registro de versão de modelo.
- **app/backtesting**: dois motores (candles e tick/evento), custos,
  walk-forward, Monte Carlo.
- **app/monitoring**: métricas, alertas, auditoria, health checks.

## 4. Máquina de estados do sistema

```text
DISABLED -> DATA_ONLY -> BACKTEST -> REPLAY -> PAPER -> DEMO
                                                          |
                                              REAL_LOCKED -> REAL_ENABLED
                                                          |
                                                  EMERGENCY_STOP (a partir de qualquer estado ativo)
```

Definida em `app/core/enums.py` (`SystemMode`) desde a Fase 1, mas só
ganhou validação e persistência reais na Fase 10
(`app/core/system_mode.py` + `app/database/repositories/
system_setting_repository.py`) — até então o enum existia "no papel". A
Fase 11 estendeu o avanço até `DEMO` (a maquinaria que o torna seguro —
motor de risco, máquina de estados de ordem, bloqueio incondicional de
conta não-demo — já existe a partir desta fase). Avanço permitido apenas
um passo por vez (`DISABLED -> DATA_ONLY -> BACKTEST -> REPLAY -> PAPER
-> DEMO`); retrocesso sempre permitido; `EMERGENCY_STOP` a partir de
qualquer estado ativo. `REAL_LOCKED` e `REAL_ENABLED` permanecem
bloqueados (transição rejeitada incondicionalmente) — ver
`docs/paper-trading.md` e `docs/execution.md`.

Regras de transição para `REAL_ENABLED` (ainda não implementadas):
tipo de conta confirmado como real, chave de liberação segura, confirmação
manual no painel, prazo de expiração, valor máximo diário, lista de símbolos
autorizados, log de auditoria, todos os testes obrigatórios aprovados.

## 5. Máquina de estados de ordem (Fase 11)

Implementada em `app/execution/order_state.py` (`OrderState`,
`validate_order_transition`) com uma simplificação deliberada frente ao
desenho-alvo original: para ordens a mercado (retail, sem profundidade
de livro), `order_send` do MetaTrader 5 é **síncrono** — o retcode já
informa sucesso ou rejeição imediatamente, sem preenchimento parcial
assíncrono a acompanhar. Por isso `ORDER_ACCEPTED`/`PARTIALLY_FILLED`/
`FILLED` são tratados como um único resultado síncrono
(`POSITION_OPEN` ou `REJECTED`), não estados monitorados separadamente:

```text
SIGNAL_CREATED -> RISK_REJECTED
               -> RISK_APPROVED -> ORDER_CHECKED -> ORDER_SENT -> POSITION_OPEN
                                                               -> REJECTED / CANCELLED
                    POSITION_OPEN -> CLOSE_PENDING -> CLOSED
                    POSITION_OPEN -> RECONCILING -> CLOSED | POSITION_OPEN
```

`CLOSE_PENDING` existe na máquina mas nenhuma transição para ele é
disparada nesta fase — todo fechamento vem do stop-loss/take-profit
anexado ao próprio pedido (o broker fecha, não este processo);
`CLOSE_PENDING` fica reservado para quando o sistema precisar fechar uma
posição por decisão própria (fora do escopo da Fase 11). Ver
`docs/execution.md` para o motor de execução completo.

## 6. Escolha de interface web

Avaliadas três opções:

| Opção | Prós | Contras |
|---|---|---|
| FastAPI + React + TypeScript | Melhor para dashboards ricos em tempo real, tipagem ponta a ponta | Maior superfície: build, bundler, dois runtimes, mais lento para a v1 |
| **FastAPI + Jinja2 + Bootstrap** | Um único processo Python, deploy simples, sem build step, fácil de depurar no Windows | Menos fluido para UI muito interativa (mitigado com HTMX/WebSocket quando necessário) |
| FastAPI + React + Tailwind | Visual mais polido | Mesma complexidade de build do React, sem ganho adicional na v1 |

**Decisão:** FastAPI + Jinja2 + Bootstrap para a primeira versão, priorizando
simplicidade, estabilidade e manutenção (conforme pedido explícito da seção
22/Fase 12 do prompt mestre). Implementada na Fase 12
(`app/api/routes/{web_auth,dashboard}.py`, `app/api/templates/`) — a
complexidade do dashboard (somente leitura, sem atualização em tempo
real) não justificou migrar para React; a decisão se manteve. WebSocket
nativo do FastAPI cobriria atualização em tempo real (preços, status)
sem precisar de SPA, mas ainda não foi implementado — cada página exige
recarregar (ver `docs/dashboard.md`).

## 7. Infraestrutura e separação de processos

- O **conector MT5** roda obrigatoriamente no Windows, pois depende do
  terminal MetaTrader5 instalado e autenticado localmente — não é
  containerizado em Linux.
- API + dashboard, MySQL, serviços de treinamento e tarefas agendadas são
  logicamente separados em módulos/processos distintos (mesmo que hoje
  rodem todos no mesmo host Windows), preparando para separação física
  futura (`docker-compose.yml` documentará isso a partir da fase em que
  Docker estiver disponível no ambiente — ver `docs/assumptions.md` §2.2).

## 8. Convenções

- Todo timestamp é armazenado em UTC.
- Preços/valores financeiros usam `DECIMAL`, nunca `FLOAT`, no banco.
- Toda função pública tem type hints (MyPy em modo estrito nos módulos de
  domínio).
- Toda estratégia declara os regimes de mercado em que pode operar.
- Todo sinal e ordem carregam `correlation_id` para rastreio em logs
  estruturados JSON.
