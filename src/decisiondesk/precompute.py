"""Warm the leaderboard disk cache for the demo businesses (reproducible; same results as cold).

  python -m decisiondesk.precompute
"""
from __future__ import annotations

import time

from .agent.tools import BusinessSession

TARGETS = {"restaurant": ("revenue", "variable_costs", "fixed_costs", "units_sold", "net_cash_flow"),
           "retail": ("revenue", "units_sold", "variable_costs", "net_cash_flow")}


def main() -> None:
    for key, targets in TARGETS.items():
        s = BusinessSession.from_demo(key)
        for t in targets:
            start = time.perf_counter()
            board = s.board(t)
            print(f"{key:10s} {t:15s} {board.selected:18s} cache_hit={board.meta['cache']['hit']} "
                  f"{time.perf_counter() - start:5.1f}s")


if __name__ == "__main__":
    main()
