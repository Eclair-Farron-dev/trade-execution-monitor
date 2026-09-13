from dataclasses import dataclass
from datetime import datetime

START = datetime(2026, 9, 11, 9, 30)  # Naive timestamps consistently mean Asia/Shanghai.
SCENARIOS = {"normal": "正常运行", "delay": "成交反馈延迟", "reject": "真实拒单增加"}
CHANNELS = ("通道 A", "通道 B")


@dataclass(frozen=True)
class Settings:
    window_seconds: int = 300
    maturity_seconds: int = 60
    min_orders: int = 30
    sigma: float = 3.0
    min_fill_drop: float = 0.08
    min_reject_rise: float = 0.08
    missing_threshold: float = 0.05
    delay_threshold_seconds: float = 10.0
    max_ai_rounds: int = 6


DEFAULTS = Settings()
