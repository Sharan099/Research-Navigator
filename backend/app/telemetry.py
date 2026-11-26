"""Small bounded operational metrics; labels never contain research or chat text."""

import logging
from collections import defaultdict
from contextvars import ContextVar
from threading import Lock

request_id = ContextVar("request_id", default="")
logger = logging.getLogger("research_navigator")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


class Metrics:
    def __init__(self):
        self._lock = Lock()
        self.counters = defaultdict(float)
        self.histograms = defaultdict(list)
        self.buckets = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 5, 15, 30, 60)

    @staticmethod
    def key(name, labels):
        return "research_" + name, tuple(sorted((labels or {}).items()))

    def increment(self, name, labels=None, value=1):
        with self._lock:
            self.counters[self.key(name, labels)] += value

    def observe(self, name, seconds, labels=None):
        with self._lock:
            key = self.key(name, labels)
            if not self.histograms[key]:
                self.histograms[key] = [0] * (len(self.buckets) + 1) + [0.0]
            values = self.histograms[key]
            for index, boundary in enumerate(self.buckets):
                if seconds <= boundary:
                    values[index] += 1
            values[-2] += 1
            values[-1] += seconds

    @staticmethod
    def labels(labels):
        if not labels:
            return ""
        return "{" + ",".join(f'{key}="{value}"' for key, value in labels) + "}"

    def render(self):
        lines = []
        with self._lock:
            for (name, labels), value in sorted(self.counters.items()):
                lines.append(f"{name}_total{self.labels(labels)} {value}")
            for (name, labels), values in sorted(self.histograms.items()):
                for index, boundary in enumerate(self.buckets):
                    lines.append(f"{name}_bucket{self.labels((*labels, ('le', boundary)))} {values[index]}")
                lines.append(f"{name}_bucket{self.labels((*labels, ('le', '+Inf')))} {values[-2]}")
                lines.append(f"{name}_count{self.labels(labels)} {values[-2]}")
                lines.append(f"{name}_sum{self.labels(labels)} {values[-1]}")
        return "\n".join(lines) + "\n"
