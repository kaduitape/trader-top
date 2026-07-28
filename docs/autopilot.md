# Piloto automático

> Escolha a moeda e ligue. O robô lê a sessão de negociação e o volume
> daquele horário, escolhe sozinho o melhor operacional e mostra, ao vivo,
> o que está fazendo.

Conta real permanece bloqueada incondicionalmente
(`app/mt5/orders.py::send_market_order`). O piloto opera **somente** em
conta demo, com o sistema em modo `DEMO`.

## Como funciona um ciclo

O worker Windows (`python -m app.mt5.auto_sync`) roda um ciclo a cada
sincronização. Cada etapa é publicada no status ao vivo **antes** de
começar — o painel mostra o raciocínio acontecendo, não só o resultado.

| # | Etapa | Módulo | O que decide |
|---|-------|--------|--------------|
| 1 | Portões de segurança | `app/execution/autopilot.py` | modo `DEMO`, conta demo, símbolo existente, dados sincronizados |
| 2 | Leitura do mercado | `app/market/sessions.py`, `app/market/volume_profile.py`, `app/market/regimes.py` | sessão do par, volume relativo à mesma hora, regime vigente |
| 3 | Escolha do operacional | `app/execution/playbook.py` | estratégia, timeframe, score mínimo, multiplicador de risco |
| 4 | Permissão | `app/services/analysis_service.py` | score, cobertura dos 9 timeframes, notícias, fundamentos, volume |
| 5 | Timing | `app/execution/autopilot_strategy.py` | gatilho da estratégia eleita, na mesma direção da análise |
| 6 | Veto de risco | `app/risk/engine.py` | lote, perda diária, perdas seguidas, spread, intervalo, feed |
| 7 | Execução | `app/execution/engine.py` | ordem a mercado com stop/alvo anexados, em conta demo |

Nada aqui contorna uma camada existente: o piloto apenas **escolhe os
parâmetros** com que as camadas já existentes rodam.

## O que é "melhor operacional"

`select_playbook` elege uma das estratégias já implementadas e validadas em
`app/strategies/registry.py` — nenhuma lógica de entrada nova nasce no
seletor.

| Condição | Operacional eleito | Estratégia |
|----------|--------------------|-----------|
| Abertura de sessão + volume normal/forte | Rompimento de faixa | `range_breakout` |
| Tendência confirmada + volume forte | Continuidade de momentum | `momentum_continuation` |
| Tendência recém-estabelecida | Tendência por cruzamento | `ema_crossover` |
| Tendência com fluxo comportado | Tendência com pullback | `trend_pullback` |
| Lateral + volatilidade alta | Rompimento de faixa | `range_breakout` |
| Lateral + volatilidade comportada | Retorno à média | `zscore_mean_reversion` |

### Quando o robô fica de fora (`STAND_ASIDE`)

Ficar de fora é uma decisão válida e frequente, nunca uma falha:

- mercado fechado (fim de semana) ou a menos de 60 minutos do fechamento
  de sexta — risco de gap que o stop não cobre;
- volume `DEAD` (spread e execução imprevisíveis) ou `EXTREME` (pico de
  evento, não fluxo);
- sem histórico suficiente para medir o volume daquele horário — o robô
  não opera às cegas;
- evento extraordinário, spread médio acima do aceitável, regime ainda não
  classificável;
- fora do horário nobre do par **e** sem volume que compense.

O último item tem a exceção que importa: **evidência vence hipótese**. Se o
relógio diz "horário fraco" mas o volume medido está forte, o robô opera —
o relógio é só a hipótese, o volume observado é a prova.

## Sessões e volume

`app/market/sessions.py` define as janelas em UTC (Sydney 21–06, Tóquio
0–9, Londres 7–16, Nova York 12–21) e quais moedas têm cada sessão como
principal. Um par recebe:

- `PRIME` — as duas moedas em sessão principal (ex.: EURUSD às 14:00 UTC);
- `ACTIVE` — só uma das duas (ex.: USDJPY às 02:00 UTC);
- `QUIET` — nenhuma;
- `CLOSED` — fim de semana.

As janelas ignoram o horário de verão (deslocamento de até 1 h) — por isso
são tratadas como aproximação declarada e **nunca** decidem sozinhas.

`app/market/volume_profile.py` compara o volume corrente com a **mediana
histórica da mesma hora**, não com uma média global. É a única comparação
que responde "está forte ou fraco *para este horário*?": 10 de volume às
03:00 em EURUSD é normal, o mesmo 10 às 14:00 é um mercado morto. Mediana,
não média, porque um único dia de notícia distorce a média de uma hora
inteira.

## Invariantes de segurança

Dois comportamentos são impostos por código e cobertos por teste
(`tests/unit/execution/test_playbook.py`):

1. **O score mínimo nunca fica abaixo do configurado.** Horário ou volume
   ruim só tornam o robô *mais* exigente — em nenhuma combinação ele afrouxa
   o critério que você definiu.
2. **O multiplicador de risco nunca passa de 1.0.** O seletor só pode
   reduzir a exposição configurada (0,5× em horário fraco, 0,75× em volume
   baixo), jamais ampliá-la.

Além disso, como o piloto **troca de timeframe** entre ciclos, os limites de
risco e a busca por posição aberta passam a valer para o símbolo inteiro
(`DemoExecutionEngine(scope_across_timeframes=True)`). Sem isso, mudar de
M5 para M15 zeraria os contadores do dia e esconderia a posição já aberta —
os limites seriam contornados sem intenção.

## Status ao vivo

`app/execution/autopilot_status.py` publica o estado no mesmo canal
chave-valor que o worker e o dashboard já compartilham. As fases:

`OFF` → `STARTING` → `READING_MARKET` → `CHOOSING_PLAYBOOK` → `ANALYZING`
→ `WAITING_TRIGGER` → `RISK_CHECK` → `SENDING_ORDER` → `POSITION_OPEN`

mais `STANDING_ASIDE` (decisão consciente), `BLOCKED` (exige ação humana) e
`ERROR`.

O status **nunca inventa progresso**. Se o worker cair, `updated_at` para de
avançar e o painel mostra "desatualizado" em vez de fingir que o robô
continua trabalhando. O feed de atividades guarda as últimas 20 linhas e
ignora repetição imediata — sem isso, "aguardando o gatilho" publicado a
cada 15 segundos empurraria para fora exatamente os eventos que importam.

## Uso

### Pelo dashboard

`/dashboard/trading` — a tela única de operação, em três passos: escolha a
moeda entre as já sincronizadas, escolha **DEMO** ou **REAL** e clique em
*Começar a operar*. O status atualiza sozinho a cada 4 segundos.

As telas antigas (`/dashboard/autopilot` e `/dashboard/settings/trading`)
redirecionam para cá: há um lugar só para ligar o robô.

Ligar exige moeda sincronizada, conector MT5 online e — a guarda que mais
importa — **a conta do MetaTrader do mesmo tipo do modo escolhido**, nas
duas direções: DEMO com conta real é recusado, e REAL com conta demo
também. Um único clique percorre a escada de modos do sistema até `DEMO`
ou `REAL_ENABLED`, registrando o caminho inteiro em auditoria. Desligar
nunca falha por pré-requisito: parar tem que funcionar sempre.

Os limites de risco (score mínimo, risco por operação, perda diária,
perdas seguidas, spread máximo, operações por dia) ficam no bloco
*Limites de risco* da mesma tela, recolhido por padrão — o piloto os
respeita e só pode apertá-los.

O mesmo bloco de status, com botões *Começar a operar* / *Parar*, aparece
embutido em **Dados de mercado** (um botão por moeda coletada) e em
**Análise PRO** (para a moeda analisada), sempre lendo o mesmo estado da
tela de operação.

### Pela CLI

```powershell
python -m app.cli autopilot status              # o que ele está fazendo agora
python -m app.cli autopilot status --json
python -m app.cli autopilot run --iterations 20 --poll-seconds 15
python -m app.cli autopilot run --symbol EURUSD --force   # ciclo avulso
```

`autopilot run` usa o mesmo caminho de código do worker; só fica em
primeiro plano, útil para acompanhar as decisões no terminal.

## Modo manual

Desligar o piloto (`autopilot=false` na configuração) mantém o
comportamento anterior intacto: `timeframe` e `analysis_threshold` fixos,
definidos à mão no bloco *Limites de risco* de `/dashboard/trading`.
