from datetime import timedelta
from functools import lru_cache
from statistics import mean, stdev

from monitor.config import CHANNELS, DEFAULTS, START
from monitor.engine import Store
from monitor.simulation import generate


@lru_cache(maxsize=16)
def baseline(settings=DEFAULTS):
    """Independent seed, historical day, non-overlapping windows; never incident data."""
    orders, events = generate("normal", seed=2025, minutes=100)
    for column in ("submitted_at",):
        orders[column] -= timedelta(days=1)
    for column in ("event_at", "arrived_at"):
        events[column] -= timedelta(days=1)
    store = Store(orders, events)
    store.ingest(START-timedelta(days=1)+timedelta(minutes=102))
    result = {}
    for channel in ("全部", *CHANNELS):
        samples = [store.metrics(START-timedelta(days=1)+timedelta(minutes=m), channel, settings)
                   for m in range(5, 101, 5)]
        fill = [r["fill_rate"] for r in samples]
        reject = [r["reject_rate"] for r in samples]
        result[channel] = {"fill_mean": mean(fill), "fill_std": stdev(fill),
                           "reject_mean": mean(reject), "reject_std": stdev(reject),
                           "fill_lower": max(0, mean(fill)-max(settings.sigma*stdev(fill),settings.min_fill_drop)),
                           "reject_upper": min(1, mean(reject)+max(settings.sigma*stdev(reject),settings.min_reject_rise)),
                           "windows": len(samples), "seed": 2025,
                           "source": "独立正常模拟样本，前一日，20 个不重叠窗口；不是置信区间"}
    store.close()
    return result


def classify(metrics, reference, settings=DEFAULTS):
    if not metrics["sufficient"]:
        return {"kind": "insufficient", "label": "样本不足", "simple_alarm": False,
                "business_alarm": False, "detail": "订单不足最低样本量，暂不判断比例异常。"}
    low = metrics["fill_rate"] < reference["fill_lower"]
    rejection = metrics["reject_rate"] > reference["reject_upper"]
    incomplete = metrics["missing_rate"] > settings.missing_threshold
    slow = metrics["delay_p95"] is not None and metrics["delay_p95"] > settings.delay_threshold_seconds
    if incomplete:
        kind, label = "data", "数据链路待核查"
        detail = "反馈缺失使当前比例暂定；不能排除业务问题，补齐后必须复核。"
    elif low or rejection:
        kind, label = "business", "业务异常待核查"
        detail = "已到达反馈支持成交或拒单比例异常；通道侧原因仍需外部日志核实。"
    elif slow:
        kind, label = "recovered", "迟到数据已回补"
        detail = "当前比例未越界，但到达延迟偏高；需检查此前告警和链路恢复情况。"
    else:
        kind, label = "normal", "未见显著异常"
        detail = "当前窗口未越过演示阈值，不代表业务不存在其他风险。"
    return {"kind": kind, "label": label, "simple_alarm": low,
            "business_alarm": kind == "business", "detail": detail}
