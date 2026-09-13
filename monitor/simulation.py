"""Seeded paired scenarios; truth labels never enter the investigation tools."""
from datetime import timedelta
import random

import pandas as pd

from monitor.config import CHANNELS, START

ORDER_COLUMNS = ["order_id", "symbol", "channel", "quantity", "submitted_at"]
EVENT_COLUMNS = ["event_id", "order_id", "status", "quantity", "event_at", "arrived_at"]


def generate(scenario="normal", seed=42, minutes=25, dirty=False):
    if scenario not in {"normal", "delay", "reject"}:
        raise ValueError("Unknown scenario")
    rng = random.Random(seed)
    orders, events = [], []
    for minute in range(minutes):
        for channel_i, channel in enumerate(CHANNELS):
            for index in range(30):
                number = minute * 60 + channel_i * 30 + index
                submitted = START + timedelta(minutes=minute, seconds=index * 2)
                order_id = f"O{number:05d}"
                quantity = rng.choice([100, 200, 500])
                u = rng.random()
                event_at = submitted + timedelta(seconds=rng.randint(8, 25))
                latency = rng.randint(1, 3)
                affected = 10 <= minute < 15 and channel_i == 1
                reject = u < (0.45 if scenario == "reject" and affected else 0.03)
                if scenario == "delay" and affected:
                    latency += 360
                orders.append([order_id, f"SIM{number % 4 + 1:03d}", channel, quantity, submitted])
                events.append([f"E{number:05d}", order_id, "REJECTED" if reject else "FILLED",
                               0 if reject else quantity, event_at, event_at + timedelta(seconds=latency)])
    if dirty and len(events) > 730:
        for index in (650, 700):
            duplicate = events[index].copy()
            duplicate[-1] += timedelta(seconds=2)
            events.append(duplicate)
        events.append(["E-ORPHAN", "O-NOT-FOUND", "FILLED", 100,
                       START + timedelta(minutes=12), START + timedelta(minutes=12, seconds=1)])
    return pd.DataFrame(orders, columns=ORDER_COLUMNS), pd.DataFrame(events, columns=EVENT_COLUMNS)
