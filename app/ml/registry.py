"""Registro de modelos (Fase 8): versionamento, serializacao e rollback.

Cada modelo treinado e salvo como um artefato `joblib` (o pipeline
completo: pre-processador + modelo, ja calibrado) mais uma entrada num
manifesto JSON (`manifest.json`) com metadados — nunca sobrescreve uma
versao anterior, apenas adiciona uma nova e, opcionalmente, reaponta o
ponteiro "current". Rollback e apenas reapontar "current" para uma
versao anterior; o artefato antigo nunca e apagado por este modulo.

Aviso de seguranca: `joblib.load` desserializa via pickle e NAO e seguro
contra artefatos de origem nao confiavel. Isso e aceitavel aqui porque
este registro so armazena/carrega artefatos produzidos internamente pelo
proprio pipeline de treino (`app.ml.train`) — nunca um upload externo ou
de origem nao verificada.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sklearn.pipeline import Pipeline

MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class ModelManifestEntry:
    version: str
    model_name: str
    symbol: str
    timeframe: str
    strategy_name: str
    trained_at: str
    """ISO 8601 UTC."""
    feature_columns: list[str]
    metrics: dict[str, Any]
    approved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ModelManifestEntry:
        return ModelManifestEntry(
            version=data["version"],
            model_name=data["model_name"],
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            strategy_name=data["strategy_name"],
            trained_at=data["trained_at"],
            feature_columns=list(data["feature_columns"]),
            metrics=dict(data["metrics"]),
            approved=bool(data.get("approved", False)),
        )


@dataclass(slots=True)
class _Manifest:
    current: str | None = None
    entries: list[ModelManifestEntry] = field(default_factory=list)


class ModelRegistryError(Exception):
    pass


class ModelRegistry:
    """Um registro por diretorio (`models_dir`). Cria o diretorio e um
    manifesto vazio se ainda nao existirem."""

    def __init__(self, models_dir: Path | str) -> None:
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self.models_dir / MANIFEST_FILENAME
        if not self._manifest_path.exists():
            self._write_manifest(_Manifest())

    def _read_manifest(self) -> _Manifest:
        raw = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        entries = [ModelManifestEntry.from_dict(item) for item in raw.get("entries", [])]
        return _Manifest(current=raw.get("current"), entries=entries)

    def _write_manifest(self, manifest: _Manifest) -> None:
        payload = {
            "current": manifest.current,
            "entries": [entry.to_dict() for entry in manifest.entries],
        }
        self._manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _artifact_path(self, version: str) -> Path:
        return self.models_dir / f"{version}.joblib"

    def test_set_path(self, version: str) -> Path:
        return self.models_dir / f"{version}_test.csv"

    def save_test_set(self, version: str, dataset: pd.DataFrame) -> None:
        """Salva o conjunto de teste (fora da amostra) usado na avaliacao
        desta versao, para que `ml evaluate` possa recalcular as metricas
        depois sem precisar reconstruir o dataset a partir do zero."""
        dataset.to_csv(self.test_set_path(version), index=False)

    def load_test_set(self, version: str) -> pd.DataFrame:
        path = self.test_set_path(version)
        if not path.exists():
            raise ModelRegistryError(f"conjunto de teste ausente para a versao {version!r}: {path}")
        return pd.read_csv(path, parse_dates=["signal_time"])

    def register(
        self,
        pipeline: Pipeline,
        *,
        model_name: str,
        symbol: str,
        timeframe: str,
        strategy_name: str,
        feature_columns: list[str],
        metrics: dict[str, Any],
        approved: bool = False,
        set_as_current: bool = True,
    ) -> str:
        """Salva `pipeline` como uma nova versao e retorna o identificador
        de versao gerado (timestamp UTC, unico e ordenavel)."""
        version = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        joblib.dump(pipeline, self._artifact_path(version))

        manifest = self._read_manifest()
        manifest.entries.append(
            ModelManifestEntry(
                version=version,
                model_name=model_name,
                symbol=symbol,
                timeframe=timeframe,
                strategy_name=strategy_name,
                trained_at=datetime.now(UTC).isoformat(),
                feature_columns=list(feature_columns),
                metrics=metrics,
                approved=approved,
            )
        )
        if set_as_current:
            manifest.current = version
        self._write_manifest(manifest)
        return version

    def get_entry(self, version: str) -> ModelManifestEntry:
        for entry in self._read_manifest().entries:
            if entry.version == version:
                return entry
        raise ModelRegistryError(f"versao nao encontrada no registro: {version!r}")

    def list_versions(self) -> list[ModelManifestEntry]:
        return self._read_manifest().entries

    def current_version(self) -> str | None:
        return self._read_manifest().current

    def set_current(self, version: str) -> None:
        """Rollback (ou avanco) de versao ativa: apenas reaponta o
        ponteiro `current`; nenhum artefato e apagado ou recriado."""
        manifest = self._read_manifest()
        if version not in {entry.version for entry in manifest.entries}:
            raise ModelRegistryError(f"versao nao encontrada no registro: {version!r}")
        manifest.current = version
        self._write_manifest(manifest)

    def load(self, version: str | None = None) -> Pipeline:
        """Carrega o pipeline treinado da versao pedida, ou da versao
        `current` se `version` for None. Levanta `ModelRegistryError` se
        nao houver versao `current` definida ainda."""
        resolved = version or self.current_version()
        if resolved is None:
            raise ModelRegistryError("nenhuma versao 'current' definida no registro.")
        path = self._artifact_path(resolved)
        if not path.exists():
            raise ModelRegistryError(f"artefato ausente para a versao {resolved!r}: {path}")
        pipeline: Pipeline = joblib.load(path)
        return pipeline
