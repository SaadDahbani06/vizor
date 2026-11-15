"""Decision follow-up: check an approved decision against what actually happened, and learn from it.

A chat session with a general AI assistant ends when the dashboard is drawn. Vizor keeps the
prediction it made when the owner approved a decision and checks it later:

  1. Prediction receipt. On approval the Decision Lab's simulation is frozen: for every simulated
     future, the product's daily orders and the business's daily profit before fixed costs, both
     with the change and with no change. Nothing is refitted later, so the check uses only what
     was known on the approval date (no hindsight).
  2. Follow-up. Once the owner says when they made the change and sales records for the days
     after it exist, actual totals over those days are placed inside both frozen predictions.
     Verdicts: as predicted / above / below the likely range with the change, and whether the
     result can be told apart from making no change at all.
  3. Evidence about price sensitivity (price changes only). The product's actual orders divided
     by the no-change forecast say something about how strongly customers reacted. The assumed
     range is the prior; the likelihood uses the frozen no-change paths (forecast errors replayed
     from history) as the noise. The result is reported with its strength and is NEVER applied to
     future simulations automatically. Measured on synthetic replays (docs/DECISION_FOLLOWUP.md):
     one 4-week test of a 5-10% price change narrows the range by only a few percent, because
     day-to-day variation in one restaurant's item orders is larger than the price effect, and a
     before-and-after comparison mistakes a seasonal forecast miss for a price effect (burritos).

What this is NOT: proof that the change caused the result. The comparison assumes nothing else
unusual happened during the test (weather, a local event, a competitor's promotion). A single
test is weak evidence when normal forecast error is as large as the expected effect; the output
says how large both are.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..products import PRODUCT_IDS, PRODUCTS
from .types import Assumption

MIN_DAYS = 7           # fewer days after the change: too early to say anything
LEARN_MIN_DAYS = 14    # fewer days: no statement about price sensitivity
BAND = (10, 90)        # "likely range" in follow-ups: 8 in 10 simulated futures
GRID_POINTS = 601
NAMES = {p.product_id: p.name for p in PRODUCTS}
PATH_KEYS = ("orders_no_change", "orders_with_change", "contribution_no_change",
             "contribution_with_change")


# ------------------------------------------------------------------------------ actuals
@dataclass
class FollowupActuals:
    """Observed records after the prediction was made.

    ``products``: date, product_id, units_sold, demand_est (sales corrected for sell-outs, the same
    quantity the demand forecasts are trained on). ``daily``: date, revenue, variable_costs,
    contribution (= revenue - variable costs, i.e. profit before fixed costs) and closed (the
    business did not open; such days are left out of comparisons because the forecasts do not
    know about closures such as Thanksgiving)."""
    products: pd.DataFrame
    daily: pd.DataFrame
    label: str
    origin: str                      # "SYNTHETIC_REPLAY" | data label of the business's records
    meta: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        def recs(df):
            out = df.copy()
            out["date"] = out["date"].dt.strftime("%Y-%m-%d")
            return out.to_dict(orient="records")
        return {"products": recs(self.products), "daily": recs(self.daily), "label": self.label,
                "origin": self.origin, "meta": self.meta}

    @classmethod
    def from_json(cls, d: dict) -> FollowupActuals:
        def frame(rows):
            df = pd.DataFrame(rows)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
            return df
        return cls(frame(d["products"]), frame(d["daily"]), d["label"], d["origin"], d.get("meta", {}))


def actuals_from_history(history, start: pd.Timestamp, data_label: str) -> FollowupActuals | None:
    """Records on or after ``start`` from a business's own product history (``ProductHistory``).
    Returns None when there are none (the usual case right after approval)."""
    s = history.sales[history.sales["date"] >= start]
    if s.empty:
        return None
    r = history.recon[history.recon["date"] >= start].copy()
    r["variable_costs"] = r["food_cost"] + r["packaging"] + r["card_fees"]
    r["contribution"] = r["revenue"] - r["variable_costs"]
    return FollowupActuals(s[["date", "product_id", "units_sold", "demand_est"]].reset_index(drop=True),
                           r[["date", "revenue", "variable_costs", "contribution", "closed"]].reset_index(drop=True),
                           label=f"Your records ({data_label})", origin=data_label)


# ------------------------------------------------------------------------------ receipts
def make_receipt(ctx, sc, scen, base, *, business: str, dataset: str, action: str,
                 checks: list[str] | None = None, approved_at: str | None = None
                 ) -> tuple[dict, dict[str, np.ndarray]]:
    """Freeze the Decision Lab's prediction for an approved product decision.

    ``scen`` / ``base``: ``SimResult`` of the decision and of no change on the same simulated
    paths. Returns (receipt, paths); paths are sims x days arrays."""
    i = PRODUCT_IDS.index(sc.product_id)
    el = ctx.elasticity_for(sc.product_id)
    paths = {"orders_no_change": base.demand[:, :, i], "orders_with_change": scen.demand[:, :, i],
             "contribution_no_change": base.revenue - base.variable,
             "contribution_with_change": scen.revenue - scen.variable}
    d_profit = scen.profit.sum(1) - base.profit.sum(1)

    def summ(x):
        t = x.sum(1)
        return {"expected": float(t.mean()), "likely_range": _band(t)}

    receipt = {
        "id": uuid.uuid4().hex[:10], "kind": "product_scenario",
        "approved_at": approved_at or datetime.now().isoformat(timespec="seconds"),
        "business": business, "dataset": dataset, "data_label": ctx.data_label,
        "action": action, "scenario": asdict(sc), "product_id": sc.product_id,
        "product": NAMES[sc.product_id], "horizon_days": int(sc.horizon_days),
        "data_through": str(ctx.origin.date()),
        "forecast_start": str(scen.dates[0].date()), "forecast_end": str(scen.dates[-1].date()),
        "current_price": float(ctx.prices[i]), "new_price": float(scen.prices[i]),
        "price_sensitivity_used": {"value": el.value, "low": el.low, "high": el.high,
                                   "source": el.source},
        "predicted": {
            "profit_change": float(d_profit.mean()),
            "profit_change_90pct_range": [float(np.percentile(d_profit, 5)),
                                          float(np.percentile(d_profit, 95))],
            "probability_profit_improves": float(np.mean(d_profit > 0)),
            **{k: summ(v) for k, v in paths.items()}},
        "n_simulations": int(base.demand.shape[0]),
        "change_made_on": None,
        "checks": list(checks or []),
        "executed_automatically": False,
    }
    return receipt, paths


class DecisionStore:
    """Approved decisions, their frozen predictions and demo replays.

    Local files under ``root`` (default ``data/cache/decisions``, gitignored):
    receipts.json, paths/<id>.npz, actuals/<id>.json."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.getenv("MSRD_DECISIONS_DIR") or "data/cache/decisions")

    # -- files
    def _read(self, name: str, default):
        p = self.root / name
        try:
            return json.loads(p.read_text()) if p.exists() else default
        except (OSError, json.JSONDecodeError):
            return default

    def _write(self, name: str, obj) -> None:
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1, default=str))
        tmp.replace(p)

    # -- receipts
    def add(self, receipt: dict, paths: dict[str, np.ndarray]) -> dict:
        (self.root / "paths").mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.root / "paths" / f"{receipt['id']}.npz",
                            **{k: np.asarray(paths[k], dtype=np.float32) for k in PATH_KEYS})
        rs = self._read("receipts.json", [])
        rs.append(receipt)
        self._write("receipts.json", rs)
        return receipt

    def receipts(self, dataset: str | None = None) -> list[dict]:
        rs = [r for r in self._read("receipts.json", []) if dataset is None or r.get("dataset") == dataset]
        return sorted(rs, key=lambda r: r["approved_at"], reverse=True)

    def get(self, rid: str) -> dict:
        for r in self._read("receipts.json", []):
            if r["id"] == rid:
                return r
        raise KeyError(f"no approved decision with id {rid!r}")

    def update(self, rid: str, **fields) -> dict:
        rs = self._read("receipts.json", [])
        for r in rs:
            if r["id"] == rid:
                r.update(fields)
                self._write("receipts.json", rs)
                return r
        raise KeyError(f"no approved decision with id {rid!r}")

    def paths(self, rid: str) -> dict[str, np.ndarray]:
        with np.load(self.root / "paths" / f"{rid}.npz") as z:
            return {k: z[k].astype(float) for k in PATH_KEYS}

    # -- actuals (demo replays; a connected business's records are read from the business)
    def save_actuals(self, rid: str, actuals: FollowupActuals) -> None:
        self._write(f"actuals/{rid}.json", actuals.to_json())

    def actuals(self, rid: str) -> FollowupActuals | None:
        d = self._read(f"actuals/{rid}.json", None)
        return FollowupActuals.from_json(d) if d else None


# ------------------------------------------------------------------------------ evaluation
def _band(x: np.ndarray) -> list[float]:
    return [float(np.percentile(x, BAND[0])), float(np.percentile(x, BAND[1]))]


def _metric(actual: float, with_change: np.ndarray, no_change: np.ndarray) -> dict:
    lo, hi = _band(with_change)
    nlo, nhi = _band(no_change)
    verdict = "as_predicted" if lo <= actual <= hi else "above" if actual > hi else "below"
    return {"actual": float(actual),
            "with_change": {"expected": float(with_change.mean()), "likely_range": [lo, hi]},
            "no_change": {"expected": float(no_change.mean()), "likely_range": [nlo, nhi]},
            "share_of_predictions_below_actual": float(np.mean(with_change < actual)),
            "verdict": verdict,
            "inside_no_change_range": bool(nlo <= actual <= nhi)}


def evaluate(receipt: dict, paths: dict[str, np.ndarray] | None,
             actuals: FollowupActuals | None) -> dict:
    """Where the decision stands, and (with enough days) actual vs predicted."""
    H = receipt["horizon_days"]
    dates = pd.date_range(receipt["forecast_start"], periods=H, freq="D")
    out = {"id": receipt["id"], "action": receipt["action"], "product": receipt["product"],
           "approved_at": receipt["approved_at"], "change_made_on": receipt.get("change_made_on"),
           "forecast_window": [receipt["forecast_start"], receipt["forecast_end"]],
           "predicted_profit_change": receipt["predicted"]["profit_change"],
           "predicted_profit_change_90pct_range": receipt["predicted"]["profit_change_90pct_range"],
           "days_observed": 0, "days_needed": MIN_DAYS, "horizon_days": H,
           "effect_type": "comparison with the prediction frozen at approval; not proof of cause"}
    if not receipt.get("change_made_on"):
        return out | {"status": "waiting_for_change",
                      "summary": "Approved. Waiting for you to make the change and mark the date."}
    changed = max(pd.Timestamp(receipt["change_made_on"]), dates[0])
    k0 = int((changed - dates[0]).days)
    if k0 > H - MIN_DAYS:
        return out | {"status": "changed_too_late",
                      "summary": (f"The change was made on {changed.date()}, too late in the predicted window "
                                  f"(ends {dates[-1].date()}) to check it. Approve it again in the Decision Lab "
                                  "on the day you make it.")}
    if actuals is None or actuals.products.empty or paths is None:
        return out | {"status": "tracking", "summary": f"Tracking from {changed.date()}: waiting for sales records."}
    pa = actuals.products[actuals.products["product_id"] == receipt["product_id"]].set_index("date")
    da = actuals.daily.set_index("date")
    window = []
    for k in range(k0, H):  # consecutive days with both product and daily records
        d = dates[k]
        if d in pa.index and d in da.index:
            window.append(k)
        else:
            break
    n = len(window)
    out["days_observed"] = n
    out["actuals_label"] = actuals.label
    out["actuals_origin"] = actuals.origin
    if n < MIN_DAYS:
        return out | {"status": "too_early",
                      "summary": (f"Day {n} of {H - k0} since the change. Vizor needs at least {MIN_DAYS} days "
                                  "before comparing.")}
    closed = (da.loc[dates[window], "closed"].to_numpy(bool) if "closed" in da.columns
              else np.zeros(n, dtype=bool))
    w = np.array(window)[~closed]
    wd = dates[w]
    out["closed_days_left_out"] = [str(d.date()) for d in dates[np.array(window)[closed]]]
    a_orders = float(pa.loc[wd, "demand_est"].sum())
    a_contrib = float(da.loc[wd, "contribution"].sum())
    orders = _metric(a_orders, paths["orders_with_change"][:, w].sum(1), paths["orders_no_change"][:, w].sum(1))
    contrib = _metric(a_contrib, paths["contribution_with_change"][:, w].sum(1),
                      paths["contribution_no_change"][:, w].sum(1))
    complete = k0 + n >= H
    out |= {"status": "complete" if complete else "partial",
            "window": [str(dates[window[0]].date()), str(dates[window[-1]].date())],
            "orders": orders, "profit_before_fixed_costs": contrib}
    out["summary"] = _summary(receipt, out, n, complete)
    out["daily"] = {
        "dates": [str(d.date()) for d in wd],
        "actual_orders": [float(x) for x in pa.loc[wd, "demand_est"]],
        "with_change": _daily_band(paths["orders_with_change"][:, w]),
        "no_change": _daily_band(paths["orders_no_change"][:, w])}
    sc = receipt["scenario"]
    if (sc.get("price_change_pct") and not sc.get("demand_shock_pct") and not sc.get("marketing_change_pct")):
        if n >= LEARN_MIN_DAYS:
            out["price_evidence"] = price_evidence(
                a_orders, paths["orders_no_change"][:, w].sum(1), sc["price_change_pct"],
                _prior(receipt), n_days=n)
        else:
            out["price_evidence"] = {"available": False, "strength": "none", "applied_to_simulations": False,
                                     "text": f"Needs at least {LEARN_MIN_DAYS} days after the change ({n} so far)."}
    return out


def _daily_band(x: np.ndarray) -> dict:
    """Cumulative orders: expected and likely range per day (for charts)."""
    c = np.cumsum(x, axis=1)
    return {"expected": [float(v) for v in c.mean(0)],
            "low": [float(v) for v in np.percentile(c, BAND[0], axis=0)],
            "high": [float(v) for v in np.percentile(c, BAND[1], axis=0)]}


def _prior(receipt: dict) -> Assumption:
    u = receipt["price_sensitivity_used"]
    return Assumption(f"price_elasticity_{receipt['product_id']}", u["value"], "ratio", u["source"],
                      u["low"], u["high"], "triangular")


def _summary(receipt: dict, ev: dict, n: int, complete: bool) -> str:
    name = receipt["product"].lower()
    o, c = ev["orders"], ev["profit_before_fixed_costs"]
    lo, hi = o["with_change"]["likely_range"]
    word = {"as_predicted": "within the range Vizor predicted",
            "above": "above the range Vizor predicted", "below": "below the range Vizor predicted"}
    when = f"over the {n} days since the change" + ("" if complete else " so far")
    text = (f"{receipt['product']} orders {when}: {o['actual']:,.0f}, {word[o['verdict']]} "
            f"({lo:,.0f} to {hi:,.0f}). Profit before fixed costs: ${c['actual']:,.0f}, "
            f"{word[c['verdict']]} (${c['with_change']['likely_range'][0]:,.0f} to "
            f"${c['with_change']['likely_range'][1]:,.0f}).")
    if o["verdict"] != "as_predicted":
        text += (" Outside the predicted range means customers reacted differently than assumed, or the "
                 "forecast was off for another reason (season, weather, a local event); this check cannot "
                 "tell which.")
    if o["inside_no_change_range"] and c["inside_no_change_range"]:
        text += (f" Both are also inside the range expected with no change, so normal variation is still too "
                 f"large to tell whether the {name} change made a difference.")
    if ev.get("closed_days_left_out"):
        text += f" Days you were closed are left out ({', '.join(ev['closed_days_left_out'])})."
    return text


# ------------------------------------------------------------------------------ price evidence
STRONG_NARROWING_PCT = 50   # "clear" evidence
SOME_NARROWING_PCT = 20     # below this: "very little"


def _tri_pdf(x: np.ndarray, lo: float, mode: float, hi: float) -> np.ndarray:
    if hi <= lo:
        return np.ones_like(x)
    up = np.where(x <= mode, (x - lo) / max(mode - lo, 1e-12), (hi - x) / max(hi - mode, 1e-12))
    return np.clip(up, 0, None) * 2 / (hi - lo)


def _quantiles(grid: np.ndarray, pdf: np.ndarray, qs=(0.05, 0.50, 0.95)) -> list[float]:
    cdf = np.cumsum(pdf)
    cdf = cdf / cdf[-1]
    return [float(np.interp(q, cdf, grid)) for q in qs]


def price_evidence(actual_orders: float, no_change_totals: np.ndarray, price_change_pct: float,
                   prior: Assumption, *, n_days: int) -> dict:
    """What one before-and-after price test says about the product's price sensitivity e.

    Model: actual orders = (orders with no change) x (1 + p)^e. The no-change total is uncertain;
    its distribution is the frozen no-change paths (forecast errors replayed from history). On the
    log scale, log A - e log(1+p) must look like a draw of log B, so the likelihood of e is a
    kernel density of the log no-change totals evaluated there. Prior: the assumed triangular
    range (the posterior cannot leave it).

    Reported, never applied: a before-and-after test cannot separate a forecast miss (for example
    a seasonal shift the forecast lags behind) from customers' reaction to the price, and the
    synthetic-replay check found it misleads in exactly that case (docs/DECISION_FOLLOWUP.md)."""
    p = price_change_pct / 100
    lp = float(np.log1p(p))
    lb = np.log(np.maximum(np.asarray(no_change_totals, dtype=float), 1e-6))
    noise = float(lb.std())
    grid = np.linspace(prior.low, prior.high, GRID_POINTS)
    prior_pdf = _tri_pdf(grid, prior.low, prior.value, prior.high)
    prior_q = _quantiles(grid, prior_pdf)
    out = {"available": False, "n_days": n_days, "applied_to_simulations": False,
           "assumed": {"low": round(prior_q[0], 2), "middle": round(prior_q[1], 2), "high": round(prior_q[2], 2)},
           "ranges_mean": "5th to 95th percentile (9 in 10); middle = median",
           "normal_variation_pct": round(noise * 100, 1),
           "expected_effect_pct": round(((1 + p) ** prior.value - 1) * 100, 1),
           "limits": ("Before-and-after comparison: it assumes the no-change forecast was right and nothing "
                      "else unusual happened, so a forecast miss can look like a price effect. Shown for "
                      "information; Vizor keeps using the assumed range.")}
    if abs(lp) < 1e-9 or actual_orders <= 0:
        return out | {"strength": "none", "text": "No price change to learn from."}
    h = max(1.06 * noise * len(lb) ** (-0.2), 1e-3)
    z = (np.log(actual_orders) - grid[:, None] * lp - lb[None, :]) / h
    post = prior_pdf * np.exp(-0.5 * z ** 2).mean(axis=1)
    if not np.isfinite(post).all() or post.sum() <= 1e-300:
        return out | {"strength": "none",
                      "text": ("The result is outside anything the assumed range can explain. Check whether "
                               "something else changed during the test.")}
    q = _quantiles(grid, post)
    w0, w1 = prior_q[2] - prior_q[0], q[2] - q[0]
    narrowed = round((1 - w1 / w0) * 100) if w0 > 0 else 0
    strength = ("clear" if narrowed >= STRONG_NARROWING_PCT else
                "some" if narrowed >= SOME_NARROWING_PCT else "very little")
    eff, var = abs(out["expected_effect_pct"]), out["normal_variation_pct"]
    text = (f"This test says {strength} about how customers react to the price: the likely range moved from "
            f"{prior_q[0]:.2f} to {prior_q[2]:.2f} to {q[0]:.2f} to {q[2]:.2f}.")
    if var > eff:
        text += (f" Normal variation in these orders (about {var:.0f}%) is larger than the effect a "
                 f"{price_change_pct:+g}% price change is expected to have (about {eff:.0f}%), so one test "
                 "cannot pin it down.")
    return out | {"available": True, "strength": strength, "range_narrowed_pct": narrowed,
                  "after_test": {"low": round(q[0], 2), "middle": round(q[1], 2), "high": round(q[2], 2)},
                  "text": text}
