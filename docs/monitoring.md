# Monitoramento e Detecção de Drift — Fase 13

Este documento é a versão em prosa de `app/monitoring/drift.py` e
`app/risk/feed_health.py` (a fonte de verdade é o código — mantenha os
dois sincronizados).

## O que esta fase monitora

Um modelo aprovado (Fase 8/9) não fica bom para sempre — o mercado muda,
o comportamento do modelo em produção pode se afastar do que foi visto
no treino. Esta fase adiciona a capacidade de **detectar** essa
degradação (não de corrigi-la automaticamente): dois eixos independentes.

1. **Drift de features**: a distribuição das features recentes se
   afastou da distribuição vista no treino?
2. **Drift de métricas**: a calibração/desempenho do modelo, recalculado
   sobre dados recentes, degradou em relação ao que foi registrado no
   momento do treino?

Mais um item, que na verdade fecha uma pendência da Fase 11: **saúde do
feed de dados** — o motor de risco agora recusa operar com dados
atrasados.

## Drift de features (PSI)

`compute_psi(reference, current, bins=10)` implementa o Population
Stability Index, a métrica padrão da indústria (originada em risco de
crédito, hoje amplamente usada em monitoramento de ML) para comparar
duas distribuições univariadas. Os bins são definidos pelos **quantis da
referência** — o PSI mede o quanto a amostra atual se afasta do que era
"normal" na referência, nunca o contrário.

Limiares (convenção comum da literatura, não inventados para este
projeto):

| PSI | Severidade |
|---|---|
| < 0.10 | `NONE` |
| 0.10 – 0.25 | `WARNING` |
| ≥ 0.25 | `CRITICAL` |

`detect_feature_drift(reference_df, current_df, feature_columns)` roda
isso para cada feature presente em ambos os DataFrames — features
ausentes em um dos dois são puladas, nunca tratadas como drift infinito.

**A referência usada por `ml monitor model` é o conjunto de teste salvo
pelo registro de modelos** (`ModelRegistry.load_test_set`, Fase 8) — os
dados fora da amostra do momento do treino, não o dataset de treino
inteiro (que incluiria a fatia de calibração e enviesaria a comparação).

## Drift de métricas

`detect_metric_drift(metric_name, baseline_value, current_value, *,
higher_is_better, warning_pct=20.0, critical_pct=50.0)` compara um valor
de métrica **recente** contra o valor **gravado no manifesto do modelo
no momento do treino** (`ModelManifestEntry.metrics`, Fase 8) — nunca
contra um número "esperado" arbitrário definido à parte.

Funciona tanto para métricas "maior é melhor" (`expectancy_after_costs`)
quanto "menor é melhor" (`brier_score`) — `degradation_pct` é sempre
expresso na mesma direção (piora = positivo), independente de qual das
duas famílias a métrica pertence.

## Persistência seletiva (`drift_events`)

Só ocorrências `WARNING`/`CRITICAL` viram uma linha em `drift_events`
(migration `0006`) — um resultado `NONE` nunca é gravado. Mesmo
raciocínio de uma tabela de alertas, não de um log de aplicação que
registraria "está tudo bem" a cada execução, inchando a tabela sem
nenhum valor de auditoria adicional.

**Este módulo nunca decide sozinho o que fazer com um drift detectado**
— não retreina, não desativa uma versão, não força `EMERGENCY_STOP`.
Apenas classifica, relata e persiste; a decisão continua humana (rever
`/dashboard/drift` ou a saída de `monitor model`/`monitor feed`).

## Saúde do feed de dados (fecha pendência da Fase 11)

`app/risk/feed_health.py::check_feed_health(last_update_time, now,
max_delay_seconds)` usa a mesma semântica de atraso já empregada em
`app.market.data_quality._check_feed_delay` (Fase 3) — não uma segunda
definição de "atraso" divergente.

`app.risk.engine.evaluate_signal` ganhou um parâmetro obrigatório
`feed_last_update_time` e agora rejeita incondicionalmente um sinal
quando o feed está mais atrasado que `RiskLimits.max_feed_delay_seconds`
(padrão: 300s, igual a `Settings.quality_max_feed_delay_seconds`).

**Ajuste necessário no `DemoExecutionEngine`**: a checagem de feed exige
um "agora" de parede real, não o horário das próprias candles (que não
tem relação nenhuma com o instante em que o processo está rodando de
verdade). O motor ganhou um `clock: Callable[[], datetime]` injetável
(padrão: `datetime.now(UTC)`), permitindo produção correta e testes
determinísticos ao mesmo tempo.

## Comandos CLI

```powershell
# Compara um modelo registrado contra um dataset recente
python -m app.cli monitor model --recent-dataset datasets/eurusd_m1_recente.csv

# Verifica se o feed de candles de um simbolo/timeframe esta atualizado
python -m app.cli monitor feed --symbol EURUSD --timeframe M1 --max-delay-seconds 300
```

`monitor model` imprime um relatório de drift de features seguido de
drift de calibração/desempenho, e persiste qualquer ocorrência
`WARNING`/`CRITICAL` em `drift_events`. `monitor feed` retorna código de
saída `1` (falha) quando o feed está atrasado — útil para agendar como
uma checagem de saúde externa (cron, monitoramento de infraestrutura),
embora esta fase não inclua um agendador próprio (ver limitações).

## Dashboard

`/dashboard/drift` lista as ocorrências mais recentes; a visão geral
(`/dashboard`) ganhou um card "Drift recente" — mesma convenção
somente-leitura das demais páginas (Fase 12).

## Limitações e decisões conhecidas

- Nenhuma decisão automática a partir de um drift detectado — sempre
  uma recomendação para revisão humana.
- Drift univariado apenas (feature por feature) — drift multivariado
  (mudança na estrutura de correlação entre features) fica para quando
  houver um consumidor concreto.
- `monitor model`/`monitor feed` são comandos ad-hoc, rodados sob
  demanda — não existe um processo de fundo/agendador que os execute
  periodicamente nesta fase.
- Limiares de PSI (`0.10`/`0.25`) e de degradação de métrica
  (`20%`/`50%`) são os valores convencionais da literatura — ainda não
  calibrados especificamente para as estratégias/símbolos deste projeto
  (sem histórico de produção suficiente para isso ainda).
