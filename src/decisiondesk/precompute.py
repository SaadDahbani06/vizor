"""Warm the leaderboard disk cache for the demo businesses (reproducible; same results as cold).

  python -m decisiondesk.precompute          # all models (what the dashboard uses by default)
  python -m decisiondesk.precompute --fast   # the 4-model set used with MSRD_FAST=1

Includes the five product demand forecasters of the demo restaurant (Scenario Lab).
"""
from __future__ import annotations

import argparse
import time

from .agent.tools import BusinessSession

FAST_MODELS = ("seasonal_naive", "weekday_mean_12w", "historical_mean_28d", "ridge")

TARGETS = {"restaurant": ("revenue", "variable_costs", "fixed_costs", "units_sold", "net_cash_flow"),
           "retail": ("revenue", "units_sold", "variable_costs", "net_cash_flow")}


def main() -> None:
    ap = argparse.ArgumentParser(description="Warm the forecast caches")
    ap.add_argument("--fast", action="store_true", help="warm the 4-model MSRD_FAST set")
    args = ap.parse_args()
    models = FAST_MODELS if args.fast else None
    for key, targets in TARGETS.items():
        s = BusinessSession.from_demo(key, models=models)
        for t in targets:
            start = time.perf_counter()
            board = s.board(t)
            print(f"{key:10s} {t:15s} {board.selected:18s} cache_hit={board.meta['cache']['hit']} "
                  f"{time.perf_counter() - start:5.1f}s")
        if s.has_products:
            start = time.perf_counter()
            ctx = s.product_context()
            for p, b in ctx.boards.items():
                print(f"{key:10s} demand_{p:9s} {b['selected_model']:18s} (product forecast)")
            print(f"{key:10s} product context ready in {time.perf_counter() - start:5.1f}s")


if __name__ == "__main__":
    main()
