"""Scenario Lab: product-level counterfactual simulation for the demo restaurant (Milestone 5).

Three things are kept apart:
  * Historical observations - the SYNTHETIC records (``products.ProductHistory``).
  * Model forecasts - baseline daily demand per product from forecasters chosen by the existing
    chronological leaderboard (``leaderboard.build_leaderboard_cached``) on ``demand_est``, with
    uncertainty from their out-of-sample back-test errors (``ForecastBundle.simulate``). Fixed
    costs come from the existing aggregate bundle, as in the restaurant decision engine.
  * Counterfactual simulation - what may happen if the owner changes a price, ingredient
    purchases, demand or marketing. Effects are ASSUMPTIONS (below), never estimated causal
    effects: the data has only menu-wide price steps, so product own-price elasticities are not
    identifiable.

Per simulated path s and day t (all actions share the same random draws):
  demand    D'_i = D_i * price_mult_i * shock_i * marketing_mult
            price_mult = (1 + p)^e_i for the selected product, e_i ~ triangular(low, mode, high).
            A share ``substitution_share`` of the demand a main dish loses to a price increase moves
            to the other main dishes (in proportion to their demand); a price cut takes demand from
            them in the same way. Drinks have no substitutes.
  supply    Ingredients arrive Mon/Thu under the SAME order-up-to rule as the history
            (``products.plan_order``, 1.35 x expected usage from the baseline forecast). The owner's
            supply change scales the selected product's share of each order; an extra purchase
            arrives on day 1. Shared ingredients are rationed (``products.ration``).
  sales     sold = min(demand, what stock allows). A share ``substitution_share`` of unmet
            main-dish demand is re-offered to main dishes that still have stock; the rest is lost.
  money     revenue  = sum_i price_i * sold_i + other_items_per_main * mains_sold
            variable = sum_i unit_cost_i * sold_i + other_food_per_main * mains_sold
                       + packaging_per_guest * guests + card_fee_rate * revenue
            fixed    = fixed-cost forecast + marketing change
            profit   = revenue - variable - fixed       (operating, before owner draws and taxes)
            cash     = 15% of revenue same day + 85% next day - card fees - ingredient purchases
                       (paid net-7) - other food - packaging - fixed; differs from profit by
                       inventory build-up and payment timing.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd

from ..leaderboard import EvaluationConfig, build_leaderboard_cached
from ..products import (
    BOM,
    CARD_FEE_RATE,
    DELIVERY_WEEKDAYS,
    INGREDIENT_IDS,
    MAINS,
    PACKAGING_PER_GUEST,
    PRODUCT_IDS,
    PRODUCTS,
    SAFETY,
    ProductHistory,
    days_until_next_delivery,
    ration,
)
from .baseline import ForecastBundle, SimulationDraws, build_forecast_bundle, make_draws
from .types import Assumption

SEED = 20261114
HORIZONS = (7, 14, 28)
MAIN_IDX = [PRODUCT_IDS.index(p) for p in MAINS]

ELASTICITY: dict[str, Assumption] = {
    "tacos": Assumption("price_elasticity_tacos", -1.1, "ratio", "default_needs_confirmation",
                        -1.8, -0.6, "triangular"),
    "burritos": Assumption("price_elasticity_burritos", -1.0, "ratio", "default_needs_confirmation",
                           -1.6, -0.5, "triangular"),
    "quesadillas": Assumption("price_elasticity_quesadillas", -1.2, "ratio",
                              "default_needs_confirmation", -1.9, -0.6, "triangular"),
    "nachos": Assumption("price_elasticity_nachos", -1.3, "ratio", "default_needs_confirmation",
                         -2.0, -0.7, "triangular"),
    "drinks": Assumption("price_elasticity_drinks", -1.5, "ratio", "default_needs_confirmation",
                         -2.2, -0.8, "triangular"),
}
ELASTICITY_NOTE = ("Assumed, not estimated: the history has only menu-wide price steps, so a single "
                   "product's own-price effect cannot be identified. Item-level elasticities are "
                   "set more negative than the restaurant-wide -0.8 because diners can switch "
                   "within the menu.")
SUBSTITUTION = Assumption(
    "substitution_share", 0.30, "share", "default_needs_confirmation", 0.10, 0.50, "uniform",
    "Share of the demand a main dish loses (price increase or sell-out) that moves to the other "
    "main dishes; the rest is lost.")
MARKETING_RESPONSE = Assumption(
    "marketing_demand_response", 0.0167, "share per 100% spend change", "default_needs_confirmation",
    0.0, 0.05, "uniform",
    "Demand change for a 100% change in marketing & software spend (linear; same range as the "
    "restaurant engine's 0-1.5% demand loss for a 30% discretionary cut).")
MARKETING_MONTHLY_DEFAULT = 1050.0


@dataclass(frozen=True)
class ProductScenario:
    product_id: str = "tacos"
    price_change_pct: float = 0.0
    supply_change_pct: float = 0.0     # % change in this product's share of planned deliveries
    extra_units: float = 0.0           # one-time ingredient purchase for N extra units (day 1)
    demand_shock_pct: float = 0.0
    demand_shock_scope: str = "product"  # "product" | "all"
    marketing_change_pct: float = 0.0
    horizon_days: int = 28

    def __post_init__(self) -> None:
        if self.product_id not in PRODUCT_IDS:
            raise ValueError(f"unknown product {self.product_id!r}; choose from {PRODUCT_IDS}")
        if self.horizon_days not in HORIZONS:
            raise ValueError(f"horizon must be one of {HORIZONS} days")
        if not -50 <= self.price_change_pct <= 50:
            raise ValueError("price change must be between -50% and +50%")
        if not -100 <= self.supply_change_pct <= 300:
            raise ValueError("supply change must be between -100% and +300%")
        if self.extra_units < 0:
            raise ValueError("extra units must be zero or positive")
        if not -90 <= self.demand_shock_pct <= 200:
            raise ValueError("demand shock must be between -90% and +200%")
        if self.demand_shock_scope not in ("product", "all"):
            raise ValueError("demand shock scope must be 'product' or 'all'")
        if not -100 <= self.marketing_change_pct <= 100:
            raise ValueError("marketing change must be between -100% and +100%")

    @property
    def is_baseline(self) -> bool:
        return not any((self.price_change_pct, self.supply_change_pct, self.extra_units,
                        self.demand_shock_pct, self.marketing_change_pct))

    def describe(self) -> str:
        name = {p.product_id: p.name for p in PRODUCTS}[self.product_id]
        parts = []
        if self.price_change_pct:
            parts.append(f"{name} price {self.price_change_pct:+g}%")
        if self.supply_change_pct:
            parts.append(f"{name} ingredient orders {self.supply_change_pct:+g}%")
        if self.extra_units:
            parts.append(f"buy ingredients for {self.extra_units:,.0f} extra {name.lower()}")
        if self.demand_shock_pct:
            scope = name.lower() if self.demand_shock_scope == "product" else "all products"
            parts.append(f"demand for {scope} {self.demand_shock_pct:+g}%")
        if self.marketing_change_pct:
            parts.append(f"marketing spend {self.marketing_change_pct:+g}%")
        return ", ".join(parts) or "no change (current plan)"


@dataclass
class ProductContext:
    history: ProductHistory
    bundle: ForecastBundle            # demand_<product> forecasts
    agg: ForecastBundle               # revenue, variable_costs, fixed_costs (existing engine)
    draws: SimulationDraws
    origin: pd.Timestamp
    start_stock: np.ndarray           # ingredients, end of last observed day
    prices: np.ndarray                # current menu prices
    unit_cost: np.ndarray             # current ingredient cost per product unit
    ing_cost: np.ndarray              # current cost per ingredient unit
    other_rev_per_main: float
    other_food_per_main: float
    mains_per_guest: float
    marketing_monthly: float
    max_price_increase: float | None
    data_label: str
    boards: dict = field(default_factory=dict)       # product -> leaderboard summary
    pending_payables: np.ndarray | None = None       # purchases made before origin, by due day
    last_card_revenue: float = 0.0
    _base_demand: np.ndarray | None = None           # sims x 28 x products
    _base_fixed: np.ndarray | None = None            # sims x 28
    _cache: dict = field(default_factory=dict)

    @property
    def n_sims(self) -> int:
        return self.draws.n

    def base_demand(self) -> np.ndarray:
        if self._base_demand is None:
            sims = self.bundle.simulate(self.draws)
            self._base_demand = np.stack([sims[f"demand_{p}"] for p in PRODUCT_IDS], axis=-1)
        return self._base_demand

    def base_fixed(self) -> np.ndarray:
        if self._base_fixed is None:
            self._base_fixed = self.agg.simulate(self.draws)["fixed_costs"]
        return self._base_fixed

    def expected_usage(self) -> np.ndarray:
        """Expected daily ingredient usage under the baseline forecast (28 x ingredients)."""
        return np.array([self.bundle.targets[f"demand_{p}"].point for p in PRODUCT_IDS]).T.clip(0) @ BOM


def product_boards(history: ProductHistory, *, dataset: str, data_label: str,
                   models: tuple[str, ...] | None = None, cache_dir=None) -> dict:
    frame = history.forecast_frame()
    return {p: build_leaderboard_cached(frame, f"demand_{p}", dataset=f"{dataset}_products",
                                        data_label=data_label, models=models, cache_dir=cache_dir)
            for p in PRODUCT_IDS}


def build_product_context(history: ProductHistory, agg: ForecastBundle, *, dataset: str,
                          data_label: str, daily: pd.DataFrame, bills: pd.DataFrame | None,
                          max_price_increase: float | None, models=None, n_sims: int = 1000,
                          cache_dir=None) -> ProductContext:
    boards = product_boards(history, dataset=dataset, data_label=data_label, models=models,
                            cache_dir=cache_dir)
    frame = history.forecast_frame()
    bundle = build_forecast_bundle(frame, tuple(f"demand_{p}" for p in PRODUCT_IDS),
                                   dataset=f"{dataset}_products", data_label=data_label,
                                   cfg=EvaluationConfig(), boards={f"demand_{p}": b for p, b in boards.items()})
    if bundle.n_folds != agg.n_folds:
        raise ValueError("product and aggregate forecasts must share back-test folds")
    s = history.sales
    last = s[s["date"] == s["date"].max()].set_index("product_id").reindex(list(PRODUCT_IDS))
    led = history.ledger
    lastk = led[led["date"] == led["date"].max()].set_index("ingredient_id").reindex(list(INGREDIENT_IDS))
    r = history.recon[~history.recon["closed"]].tail(56)
    mains = r["mains_sold"].sum()
    purchases = led.groupby("date")["purchase_usd"].sum()
    marketing = MARKETING_MONTHLY_DEFAULT
    if bills is not None and (bills["category"] == "discretionary").any():
        marketing = float(bills.loc[bills["category"] == "discretionary", "amount"].iloc[-1])
    rev = daily["revenue"].to_numpy()
    summary = {}
    for p, b in boards.items():
        sel = next(m for m in b.models if m.name == b.selected)
        snaive = next(m for m in b.models if m.name == "seasonal_naive")
        summary[p] = {"selected_model": b.selected, "selection_reason": b.selection_reason,
                      "validation_mae": sel.validation["mae"], "validation_wape": sel.validation["wape"],
                      "test_mae": sel.holdout["mae"], "test_wape": sel.holdout["wape"],
                      "seasonal_naive_test_mae": snaive.holdout["mae"],
                      "test_interval_coverage_80": sel.holdout_interval_coverage,
                      "validation_period": b.meta["validation_period"],
                      "test_period": b.meta["holdout_period"],
                      "models_compared": [m.name for m in b.models if m.available]}
    return ProductContext(
        history=history, bundle=bundle, agg=agg, draws=make_draws(bundle, n_sims, SEED),
        origin=pd.Timestamp(s["date"].max()),
        start_stock=lastk["closing"].to_numpy(float), prices=last["price"].to_numpy(float),
        unit_cost=last["unit_cost"].to_numpy(float), ing_cost=lastk["unit_cost"].to_numpy(float),
        other_rev_per_main=float(r["other_items_revenue"].sum() / mains),
        other_food_per_main=float(r["other_food_cost"].sum() / mains),
        mains_per_guest=float(mains / r["covers"].sum()),
        marketing_monthly=marketing, max_price_increase=max_price_increase,
        data_label=data_label, boards=summary,
        pending_payables=purchases.tail(7).to_numpy(float),  # paid on days 1..7 (net-7)
        last_card_revenue=float(rev[-1] * 0.85))


# ------------------------------------------------------------------------------ simulation
@dataclass
class SimResult:
    scenario: ProductScenario
    horizon: int
    dates: pd.DatetimeIndex
    demand: np.ndarray        # sims x H x P (after scenario effects, before stock limits)
    sold: np.ndarray          # sims x H x P
    lost: np.ndarray          # sims x H x P (own unmet demand)
    makeable: np.ndarray      # sims x H: units of the selected product the morning stock allows
                              # at that day's menu mix (its share of each shared ingredient)
    revenue: np.ndarray       # sims x H
    variable: np.ndarray
    fixed: np.ndarray
    profit: np.ndarray
    cash: np.ndarray          # sims x H operating net cash flow
    purchases: np.ndarray     # sims x H ingredient purchases (USD)
    product_revenue: np.ndarray   # sims x P
    product_gross_profit: np.ndarray
    end_stock: np.ndarray     # sims x K
    end_inventory_value: np.ndarray  # sims
    prices: np.ndarray
    assumptions: list[dict]


def _draw(ctx: ProductContext, a: Assumption, override: str | None) -> np.ndarray:
    if override:
        a = a.at(override)
    return a.sample(ctx.draws.uniform(a.name))


def simulate(ctx: ProductContext, sc: ProductScenario, *, fixed_levels: dict[str, str] | None = None) -> SimResult:
    """One scenario over all simulated paths. ``fixed_levels`` pins assumptions at
    'low' / 'value' / 'high' (sensitivity analysis), e.g. {"elasticity": "high"}."""
    fixed_levels = fixed_levels or {}
    H, n, P, _K = sc.horizon_days, ctx.n_sims, len(PRODUCT_IDS), len(INGREDIENT_IDS)
    i_sel = PRODUCT_IDS.index(sc.product_id)
    # Demand cannot be negative. Forecast + borrowed historical error can be (e.g. a past closed
    # day), so clip BEFORE any effect is applied; the baseline itself is unchanged by this.
    D0 = np.maximum(ctx.base_demand()[:, :H, :], 0.0)
    D = D0.copy()
    dates = ctx.bundle.targets[f"demand_{PRODUCT_IDS[0]}"].dates[:H]
    sub = _draw(ctx, SUBSTITUTION, fixed_levels.get("substitution"))[:, None]       # n x 1
    prices = ctx.prices.copy()

    # Price effect on the selected product, with substitution among main dishes.
    if sc.price_change_pct:
        p = sc.price_change_pct / 100
        e = _draw(ctx, ELASTICITY[sc.product_id], fixed_levels.get("elasticity"))[:, None]
        delta = D0[:, :, i_sel] * ((1 + p) ** e - 1)                                   # n x H
        D[:, :, i_sel] += delta
        prices[i_sel] = round(prices[i_sel] * (1 + p), 2)
        if sc.product_id in MAINS:
            others = [j for j in MAIN_IDX if j != i_sel]
            w = D0[:, :, others]
            tot = w.sum(axis=-1, keepdims=True)
            # Split switching demand by the other dishes' shares; on a day with no demand for
            # them (e.g. closed) nobody switches. Shares are in [0, 1] and sum to 1 (or 0).
            w = np.divide(w, tot, out=np.zeros_like(w), where=tot > 1e-9)
            D[:, :, others] += (-sub * delta)[:, :, None] * w
    if sc.demand_shock_pct:
        mult = 1 + sc.demand_shock_pct / 100
        if sc.demand_shock_scope == "all":
            D *= mult
        else:
            D[:, :, i_sel] *= mult
    if sc.marketing_change_pct:
        k = _draw(ctx, MARKETING_RESPONSE, fixed_levels.get("marketing"))[:, None, None]
        D *= 1 + k * sc.marketing_change_pct / 100
    D = np.maximum(D, 0.0)

    # Supply: same order-up-to rule as history, sized to the baseline forecast.
    usage = ctx.expected_usage()                                                       # 28 x K
    sel_usage = np.outer(np.array([ctx.bundle.targets[f"demand_{sc.product_id}"].point]).clip(0)[0],
                         BOM[i_sel])                                                   # 28 x K
    stock = np.tile(ctx.start_stock, (n, 1))
    sold = np.zeros((n, H, P))
    lost = np.zeros((n, H, P))
    makeable = np.zeros((n, H))
    purchases = np.zeros((n, H))
    uses = BOM[i_sel] > 0
    for t in range(H):
        wd = dates[t].day_of_week
        if wd in DELIVERY_WEEKDAYS:
            span = range(t, min(t + days_until_next_delivery(int(wd)), 28))
            target = SAFETY * (usage[list(span)].sum(axis=0)
                               + sc.supply_change_pct / 100 * sel_usage[list(span)].sum(axis=0))
            q = np.maximum(0.0, target[None, :] - stock)
            stock = stock + q
            purchases[:, t] += q @ ctx.ing_cost
        if t == 0 and sc.extra_units:
            extra = sc.extra_units * BOM[i_sel]
            stock = stock + extra
            purchases[:, 0] += extra @ ctx.ing_cost
        d = D[:, t, :]
        makeable[:, t] = _makeable(stock, d, i_sel, uses)
        fill, _ = ration(d, stock)
        s1 = d * fill
        unmet = d - s1
        stock = np.maximum(stock - s1 @ BOM, 0.0)
        # Re-offer a share of unmet main-dish demand to main dishes that still have stock.
        unmet_mains = unmet[:, MAIN_IDX].sum(axis=1, keepdims=True)
        open_w = np.where(fill[:, MAIN_IDX] >= 1 - 1e-9, d[:, MAIN_IDX], 0.0)
        tot = open_w.sum(axis=1, keepdims=True)
        extra_d = np.zeros_like(d)
        extra_d[:, MAIN_IDX] = np.where(tot > 0, sub * unmet_mains * open_w / np.maximum(tot, 1e-9), 0.0)
        fill2, _ = ration(extra_d, stock)
        s2 = extra_d * fill2
        stock = np.maximum(stock - s2 @ BOM, 0.0)
        sold[:, t, :] = s1 + s2
        lost[:, t, :] = unmet

    mains_sold = sold[:, :, MAIN_IDX].sum(axis=-1)
    guests = mains_sold / ctx.mains_per_guest
    prod_rev = sold * prices                                                           # n x H x P
    cogs = sold * ctx.unit_cost
    revenue = prod_rev.sum(axis=-1) + ctx.other_rev_per_main * mains_sold
    card = CARD_FEE_RATE * revenue
    other_food = ctx.other_food_per_main * mains_sold
    packaging = PACKAGING_PER_GUEST * guests
    variable = cogs.sum(axis=-1) + other_food + packaging + card
    mkt_daily = ctx.marketing_monthly * 12 / 365.25 * sc.marketing_change_pct / 100
    fixed = ctx.base_fixed()[:, :H] + mkt_daily
    profit = revenue - variable - fixed
    # Cash: 15% same day, 85% card next day; ingredients paid net-7; the rest as incurred.
    card_in = np.concatenate([np.full((n, 1), ctx.last_card_revenue), 0.85 * revenue[:, :-1]], axis=1)
    paid = np.zeros((n, H))
    pend = ctx.pending_payables if ctx.pending_payables is not None else np.zeros(7)
    for j in range(min(7, H)):
        paid[:, j] += pend[j]
    if H > 7:
        paid[:, 7:] += purchases[:, :H - 7]
    cash = 0.15 * revenue + card_in - card - paid - other_food - packaging - fixed
    end_value = stock @ ctx.ing_cost
    assumptions = [asdict(ELASTICITY[sc.product_id]) | {"note": ELASTICITY_NOTE},
                   asdict(SUBSTITUTION), asdict(MARKETING_RESPONSE)]
    return SimResult(sc, H, dates, D, sold, lost, makeable, revenue, variable, fixed, profit,
                     cash, purchases, prod_rev.sum(axis=1), (prod_rev - cogs).sum(axis=1), stock,
                     end_value, prices, assumptions)


def _makeable(stock: np.ndarray, demand: np.ndarray, i: int, uses: np.ndarray) -> np.ndarray:
    """Units of product i the stock allows when shared ingredients are split in proportion to
    expected usage at the given menu mix: min_k stock_k * share_ik / BOM_ik."""
    need = demand @ BOM                                                   # sims x K
    share = np.where(need > 1e-12, demand[:, [i]] * BOM[i] / np.maximum(need, 1e-12), 1.0)
    return np.min(stock[:, uses] * share[:, uses] / BOM[i, uses], axis=1)


def baseline(ctx: ProductContext, horizon: int, product_id: str = "tacos") -> SimResult:
    key = ("baseline", horizon, product_id)
    if key not in ctx._cache:
        ctx._cache[key] = simulate(ctx, ProductScenario(product_id, horizon_days=horizon))
    return ctx._cache[key]


def _q(x: np.ndarray, lo=5, hi=95) -> list[float]:
    return [float(np.percentile(x, lo)), float(np.percentile(x, hi))]


STOCKOUT_SHARE = 0.01


def stockout_risk(r: SimResult, i: int) -> float:
    """Share of simulated paths in which stock limits leave at least 1% of the product's demand
    over the horizon (and at least one unit) unmet."""
    lost = r.lost[:, :, i].sum(axis=1)
    need = np.maximum(1.0, STOCKOUT_SHARE * r.demand[:, :, i].sum(axis=1))
    return float(np.mean(lost >= need))


def compare(ctx: ProductContext, scen: SimResult, base: SimResult) -> dict:
    """Scenario minus baseline on the same paths, with 90% simulation ranges."""
    sc = scen.scenario
    i = PRODUCT_IDS.index(sc.product_id)

    def block(a, b):
        d = a - b
        return {"baseline": float(b.mean()), "scenario": float(a.mean()), "change": float(d.mean()),
                "change_pct": float(d.mean() / b.mean() * 100) if abs(b.mean()) > 1e-9 else None,
                "change_90pct_range": _q(d)}

    rev = block(scen.revenue.sum(1), base.revenue.sum(1))
    prof = block(scen.profit.sum(1), base.profit.sum(1))
    cash = block(scen.cash.sum(1), base.cash.sum(1))
    dem = block(scen.demand[:, :, i].sum(1), base.demand[:, :, i].sum(1))
    units = block(scen.sold[:, :, i].sum(1), base.sold[:, :, i].sum(1))
    gm_b = (base.revenue.sum(1) - base.variable.sum(1)) / base.revenue.sum(1)
    gm_s = (scen.revenue.sum(1) - scen.variable.sum(1)) / scen.revenue.sum(1)
    inv = block(scen.end_inventory_value, base.end_inventory_value)
    over = sc.price_change_pct / 100 > (ctx.max_price_increase or np.inf) + 1e-9
    per_product = []
    for j, p in enumerate(PRODUCTS):
        per_product.append({
            "product_id": p.product_id, "product": p.name,
            "revenue_change": float((scen.product_revenue[:, j] - base.product_revenue[:, j]).mean()),
            "gross_profit_change": float((scen.product_gross_profit[:, j] - base.product_gross_profit[:, j]).mean()),
            "units_sold_change": float((scen.sold[:, :, j].sum(1) - base.sold[:, :, j].sum(1)).mean()),
            "stockout_risk_baseline": stockout_risk(base, j),
            "stockout_risk_scenario": stockout_risk(scen, j)})
    return {
        "scenario": asdict(sc), "scenario_text": sc.describe(), "horizon_days": sc.horizon_days,
        "forecast_start": str(scen.dates[0].date()), "forecast_end": str(scen.dates[-1].date()),
        "selected_product": sc.product_id, "new_price": float(scen.prices[i]),
        "current_price": float(ctx.prices[i]),
        "revenue": rev, "profit": prof, "cash_flow": cash,
        "demand_units": dem, "units_sold": units,
        "gross_margin_pct": {"baseline": float(gm_b.mean() * 100), "scenario": float(gm_s.mean() * 100),
                             "change_points": float((gm_s - gm_b).mean() * 100)},
        "ending_inventory_value": inv,
        "ending_makeable_units_selected": float(np.mean(_makeable(
            scen.end_stock, scen.demand[:, -1, :], i, BOM[i] > 0))),
        "stockout_risk": {"baseline": stockout_risk(base, i), "scenario": stockout_risk(scen, i)},
        "lost_units": {"baseline": float(base.lost[:, :, i].sum(1).mean()),
                       "scenario": float(scen.lost[:, :, i].sum(1).mean())},
        "probability_profit_improves": float(np.mean(scen.profit.sum(1) - base.profit.sum(1) > 0)),
        "products": per_product,
        "within_policy": not over,
        "policy_violations": ([(f"price increase {sc.price_change_pct:g}% exceeds the owner's approved "
                               f"limit of {ctx.max_price_increase * 100:g}%")] if over else []),
        "n_simulations": ctx.n_sims, "seed": SEED,
        "interval_meaning": ("90% simulation range: 5th to 95th percentile across simulated futures. "
                             "Each future replays one whole historical out-of-sample forecast-error "
                             "window and draws the assumptions from their ranges. It is not a "
                             "guarantee and not a calibrated real-world interval."),
        "range_validation": _lab_validation(ctx, sc.horizon_days),
        "assumptions": scen.assumptions,
        "data_label": ctx.data_label,
        "effect_type": "counterfactual simulation under stated assumptions; NOT an estimated causal effect",
    }


def _lab_validation(ctx: ProductContext, horizon: int) -> dict:
    from ..calibration import lab_validation
    return lab_validation(ctx, horizon)


def sensitivity(ctx: ProductContext, sc: ProductScenario) -> list[dict]:
    """Mean profit change with each uncertain assumption pinned at its bounds."""
    base = baseline(ctx, sc.horizon_days, sc.product_id)
    rows = []
    checks = [("elasticity", ELASTICITY[sc.product_id], sc.price_change_pct != 0),
              ("substitution", SUBSTITUTION, True),
              ("marketing", MARKETING_RESPONSE, sc.marketing_change_pct != 0)]
    for key, a, relevant in checks:
        if not relevant:
            continue
        for level in ("low", "high"):
            r = simulate(ctx, sc, fixed_levels={key: level})
            b = simulate(ctx, replace(sc, price_change_pct=0, supply_change_pct=0, extra_units=0,
                                      demand_shock_pct=0, marketing_change_pct=0),
                         fixed_levels={key: level}) if key == "substitution" else base
            rows.append({"assumption": a.name, "level": level,
                         "value": a.low if level == "low" else a.high,
                         "profit_change": float((r.profit.sum(1) - b.profit.sum(1)).mean())})
    return rows


def candidate_actions(ctx: ProductContext, sc: ProductScenario) -> list[ProductScenario]:
    h, p = sc.horizon_days, sc.product_id
    cap = (ctx.max_price_increase or 0.05) * 100
    cands = [ProductScenario(p, price_change_pct=x, horizon_days=h) for x in (-5.0, 3.0, cap)]
    base = baseline(ctx, h, p)
    if stockout_risk(base, PRODUCT_IDS.index(p)) > 0.10:
        cands.append(ProductScenario(p, supply_change_pct=25.0, horizon_days=h))
    if not sc.is_baseline:
        cands.append(sc)
    uniq = []
    for c in cands:
        if c not in uniq:
            uniq.append(c)
    return uniq


def recommend(ctx: ProductContext, sc: ProductScenario) -> dict:
    """Rank a small set of product actions (plus the user's scenario) against the current plan.
    Deterministic; every figure comes from ``simulate``. Requires human approval."""
    base = baseline(ctx, sc.horizon_days, sc.product_id)
    rows = []
    for c in candidate_actions(ctx, sc):
        cmp_ = compare(ctx, simulate(ctx, c), base)
        rows.append({"action": c.describe(), "scenario": asdict(c),
                     "is_user_scenario": c == sc, "within_policy": cmp_["within_policy"],
                     "profit_change": cmp_["profit"]["change"],
                     "profit_change_90pct_range": cmp_["profit"]["change_90pct_range"],
                     "probability_profit_improves": cmp_["probability_profit_improves"],
                     "units_sold_change": cmp_["units_sold"]["change"],
                     "stockout_risk": cmp_["stockout_risk"]["scenario"],
                     "policy_violations": cmp_["policy_violations"]})
    feasible = sorted([r for r in rows if r["within_policy"]], key=lambda r: -r["profit_change"])
    best = feasible[0] if feasible and feasible[0]["profit_change"] > 0 else None
    sens = sensitivity(ctx, ProductScenario(**best["scenario"])) if best else []
    flips = [s for s in sens if best and np.sign(s["profit_change"]) != np.sign(best["profit_change"])]
    if best is None:
        text = (f"None of the tested { {x.product_id: x.name for x in PRODUCTS}[sc.product_id].lower()} "
                f"actions improves expected {sc.horizon_days}-day profit; keeping the current plan is "
                "the best tested option.")
    else:
        lo, hi = best["profit_change_90pct_range"]
        name = {x.product_id: x.name for x in PRODUCTS}[sc.product_id].lower()
        text = (f"Best tested {name} action: {best['action']} is projected to change "
                f"{sc.horizon_days}-day operating profit by {_sm(best['profit_change'])} versus the "
                f"current plan (90% simulation range {_sm(lo)} to {_sm(hi)}; better in "
                f"{best['probability_profit_improves']:.0%} of simulated futures).")
        if best["units_sold_change"] < -0.5:
            text += f" It may reduce units sold of this product by about {-best['units_sold_change']:,.0f}."
        text += (" The result depends on the assumed price sensitivity and substitution; "
                 + (f"it reverses if {flips[0]['assumption']} is at its {flips[0]['level']} bound."
                    if flips else "it keeps its sign at every tested assumption bound."))
    return {"recommended": best, "ranked": feasible,
            "outside_policy": [r for r in rows if not r["within_policy"]],
            "sensitivity": sens, "assumption_flips": flips, "text": text,
            "requires_human_approval": True,
            "method": "same simulated paths for every option (common random numbers); ranked by "
                      "mean profit change among options within the owner's limits"}


def _sm(x: float) -> str:
    """Signed money: +$1,234 / -$1,234."""
    return ("-" if round(x) < 0 else "+") + f"${abs(x):,.0f}"


def run_id() -> str:
    return uuid.uuid4().hex[:10]
