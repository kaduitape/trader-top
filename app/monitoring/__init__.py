"""Monitoramento (Fase 13): detecção de drift de modelos e de saúde do
feed de dados.

`app.monitoring.drift` é puramente funcional (sem I/O) — compara duas
distribuições (referência vs atual) ou dois valores de métrica
(treino/registro vs recente) e classifica a severidade
(`NONE`/`WARNING`/`CRITICAL`). Nunca decide sozinho o que fazer com um
drift detectado (não desativa um modelo, não força `EMERGENCY_STOP`) —
apenas relata, de forma explícita e auditável
(`app.database.models.drift_event`), para uma decisão humana."""
