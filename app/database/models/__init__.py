"""Importa todos os modelos para que `Base.metadata` os conheca — necessario
para que o Alembic autogenerate e `Base.metadata.create_all` (usado nos
testes) enxerguem o schema completo."""

from app.database.models.audit_log import AuditLog
from app.database.models.candle import Candle
from app.database.models.data_quality_event import DataQualityEvent
from app.database.models.drift_event import DriftEvent
from app.database.models.live_trade import LiveTrade
from app.database.models.paper_trade import PaperTrade
from app.database.models.symbol import Symbol
from app.database.models.system_setting import SystemSetting
from app.database.models.tick import Tick
from app.database.models.user import Role, User, user_roles

__all__ = [
    "AuditLog",
    "Candle",
    "DataQualityEvent",
    "DriftEvent",
    "LiveTrade",
    "PaperTrade",
    "Symbol",
    "SystemSetting",
    "Tick",
    "Role",
    "User",
    "user_roles",
]
