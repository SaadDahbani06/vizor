"""Analytical tools the agent (and the UI) call. Every number a user sees comes from here.

Design rules:
  * Tools call the existing engines (leaderboard, decision engine, FRED connector, risk); they
    never compute recommendations themselves.
  * Outputs are compact aggregates (no raw daily rows) so business data is not sent wholesale to
    an LLM, and every output carries a ``run_id`` and data-origin labels.
  * Changed assumptions are stored on the session and trigger a fresh calculation (new run_id).
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..connectors.base import ConnectorError
from ..connectors.fred import FREDConnector
from ..data import validate_daily_frame
from ..decision.baseline import ForecastBundle, build_forecast_bundle, make_draws
from ..decision.context import stressed
from ..decision.restaurant import ELASTICITY, RestaurantDecisionConfig
from ..decision.retail import RetailDecisionConfig
from ..decision.runner import DecisionRun, run_restaurant, run_retail
from ..decision.types import (
    OBJECTIVES,
    Assumption,
    Constraint,
    Objective,
    OrganizationContext,
    StartingCash,
)
from ..leaderboard import Leaderboard, build_leaderboard_cached
from ..macro import DEFAULT_SERIES, _latest_comparable
from ..profile import BusinessProfile, ColumnMapping, apply_mapping, capabilities, read_upload
from ..risk import backtested_cash_risk
from ..synthetic_v2 import load_business

FORECAST_TARGETS = ("revenue", "variable_costs", "units_sold", "net_cash_flow")


def _rid() -> str:
    return uuid.uuid4().hex[:10]


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


@dataclass
class ScenarioState:
    """Assumptions the user can change; persisted so follow-ups rerun with them."""

    food_cost_change_pct: float = 0.0
    elasticity_mode: float = ELASTICITY.value
    elasticity_low: float = ELASTICITY.low
    elasticity_high: float = ELASTICITY.high
    elasticity_source: str = ELASTICITY.source
    max_price_increase: float | None = None
    starting_cash: float | None = None
    purchase_budget_usd: float | None = None
    storage_units: float | None = None
    max_stockout_probability: float | None = None
    product_scenario: dict | None = None  # last Scenario Lab settings (ProductScenario fields)


class ProductLayerUnavailable(ValueError):
    """Product-level analytics need product records (only the demo restaurant has them)."""


@dataclass
class BusinessSession:
    history: pd.DataFrame
    metadata: dict
    bills: pd.DataFrame | None = None
    dataset: str = "business"
    data_label: str = "SYNTHETIC"
    models: tuple[str, ...] | None = None
    n_sims: int = 2000
    fred_cache_dir: str = "data/cache/fred"
    state: ScenarioState = field(default_factory=ScenarioState)
    boards: dict[str, Leaderboard] = field(default_factory=dict)
    bundles: dict[str, ForecastBundle] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)
    fred_session: object = None  # injectable HTTP session (tests)
    monitor_path: str = "data/cache/monitor_state.json"
    leaderboard_cache_dir: str | None = None  # None -> data/cache/leaderboards
    nessie_client: object = None  # injectable Nessie client (tests); read-only use
    product_ctx: object = None    # lazily built Scenario Lab context (shared read-only)

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_demo(cls, key: str, **kw) -> BusinessSession:
        ds = load_business(key)
        hist, _ = validate_daily_frame(ds.daily, source=f"synthetic_v2:{key}")
        s = cls(hist, ds.metadata, ds.bills, dataset=f"{key}_v2_SYNTHETIC", **kw)
        s.state = s.default_state()
        return s

    def default_state(self) -> ScenarioState:
        """The scenario with no user changes: only the business's declared constraints."""
        cons = self.metadata.get("constraints", {})
        return ScenarioState(max_price_increase=cons.get("max_price_increase"),
                             storage_units=cons.get("storage_capacity_units"),
                             max_stockout_probability=cons.get("max_stockout_after_arrival"),
                             purchase_budget_usd=cons.get("purchase_budget_28d_usd"))

    @property
    def has_products(self) -> bool:
        return self.metadata.get("key") == "restaurant" and self.data_label == "SYNTHETIC"

    def product_context(self):
        """Scenario Lab context: product forecasts, ingredient stock and cost rates."""
        if not self.has_products:
            raise ProductLayerUnavailable(
                "Product-level analytics need product records (price, unit cost, units and "
                "stock per product). This dataset has daily totals only.")
        if self.product_ctx is None:
            from ..decision.product_scenario import build_product_context
            from ..products import restaurant_products
            self.product_ctx = build_product_context(
                restaurant_products(), self.bundle(("revenue", "variable_costs", "fixed_costs")),
                dataset=self.dataset, data_label=self.data_label, daily=self.history,
                bills=self.bills, max_price_increase=self.state.max_price_increase,
                models=self.models, n_sims=min(self.n_sims, 1000),
                cache_dir=self.leaderboard_cache_dir)
        self.product_ctx.max_price_increase = self.state.max_price_increase
        return self.product_ctx

    @classmethod
    def from_upload(cls, path: str | Path, mapping: ColumnMapping, profile: BusinessProfile,
                    opening_cash: float | None = None, **kw) -> BusinessSession:
        profile.require_confirmation()
        frame, meta = apply_mapping(read_upload(path), mapping)
        hist, report = validate_daily_frame(frame, source=Path(path).name)
        metadata = {"business_name": profile.business_name, "industry": profile.industry,
                    "state": profile.state, "currency": profile.currency,
                    "objective": profile.objective, "constraints": dict(profile.constraints),
                    "data_origin": "UPLOADED USER DATA", "upload": {"file": Path(path).name,
                                                                   "sha256": report.sha256,
                                                                   "identifiers": meta},
                    "opening_cash": None if opening_cash is None else {
                        "amount": opening_cash, "source": "declared by the user at upload"}}
        s = cls(hist, metadata, dataset=Path(path).stem, data_label="UPLOADED USER DATA", **kw)
        s.state.max_price_increase = profile.constraints.get("max_price_increase")
        s.state.purchase_budget_usd = profile.constraints.get("purchase_budget_28d_usd")
        s.state.storage_units = profile.constraints.get("storage_capacity_units")
        s.state.max_stockout_probability = profile.constraints.get("max_stockout_after_arrival")
        return s

    @property
    def industry(self) -> str:
        return self.metadata.get("industry", "other")

    @property
    def origin(self) -> pd.Timestamp:
        return pd.Timestamp(self.history["date"].iloc[-1])

    def profile(self) -> BusinessProfile:
        return BusinessProfile(self.metadata.get("business_name", self.dataset),
                               self.industry if self.industry in ("restaurant", "retail") else "other",
                               self.metadata.get("state", "TX"),
                               self.metadata.get("objective", "max_expected_profit"),
                               {k: v for k, v in self.metadata.get("constraints", {}).items()
                                if k != "source"}, confirmed_by_user=True)

    def _stamp(self, name: str, inputs: dict, out: dict) -> dict:
        out = {"tool": name, "run_id": _rid(), "data_label": self.data_label,
               "business": self.metadata.get("business_name", self.dataset),
               "as_of": str(self.origin.date()), **out}
        self.log.append({"tool": name, "inputs": inputs, "run_id": out["run_id"],
                         "at": datetime.now(UTC).isoformat(timespec="seconds")})
        return out

    # ------------------------------------------------------------------ helpers
    def board(self, target: str) -> Leaderboard:
        if target not in self.boards:
            self.boards[target] = build_leaderboard_cached(
                self.history, target, dataset=self.dataset, data_label=self.data_label,
                models=self.models, cache_dir=self.leaderboard_cache_dir)
        return self.boards[target]

    def bundle(self, targets: tuple[str, ...]) -> ForecastBundle:
        key = "|".join(targets)
        if key not in self.bundles:
            self.bundles[key] = build_forecast_bundle(
                self.history, targets, dataset=self.dataset, data_label=self.data_label,
                models=self.models, boards={t: self.board(t) for t in targets})
        return self.bundles[key]

    def _cash_now(self) -> tuple[float | None, str]:
        if self.state.starting_cash is not None:
            return self.state.starting_cash, "declared by the user"
        if "cash_balance" in self.history.columns and self.metadata.get("opening_cash"):
            return (float(self.history["cash_balance"].iloc[-1]),
                    (f"{self.data_label} ledger: declared opening balance "
                    f"{self.metadata['opening_cash']['amount']:,.0f} + recorded cash flows "
                    "(not a verified bank balance)"))
        return None, "no starting cash balance available; please provide one"


# ====================================================================== tools
def get_business_overview(s: BusinessSession, period_days: int = 28) -> dict:
    h = s.history
    period_days = int(max(7, min(period_days, len(h))))
    cur, prev = h.tail(period_days), h.iloc[-2 * period_days:-period_days]

    def summ(x):
        rev = float(x["revenue"].sum()) if "revenue" in x else None
        var = float(x["variable_costs"].sum()) if "variable_costs" in x else None
        fix = float(x["fixed_costs"].sum()) if "fixed_costs" in x else None
        prof = rev - var - fix if None not in (rev, var, fix) else None
        return {"revenue": _r(rev, 0), "variable_costs": _r(var, 0), "fixed_costs": _r(fix, 0),
                "operating_profit": _r(prof, 0),
                "operating_margin_pct": _r(100 * prof / rev, 1) if prof is not None and rev else None,
                "units_sold": _r(x["units_sold"].sum(), 0) if "units_sold" in x else None}

    out = {"period_days": period_days,
           "period": [str(cur["date"].iloc[0].date()), str(cur["date"].iloc[-1].date())],
           "current": summ(cur), "previous_period": summ(prev) if len(prev) else None,
           "industry": s.industry, "location": f"{s.metadata.get('city', '')} {s.metadata.get('state', '')}".strip(),
           "history_days": len(h), "first_date": str(h["date"].iloc[0].date())}
    cash, src = s._cash_now()
    out["cash"] = {"balance": _r(cash, 0), "source": src}
    if "inventory_on_hand" in h:
        out["inventory_on_hand"] = _r(h["inventory_on_hand"].iloc[-1], 0)
    if s.bills is not None and len(s.bills):
        recent = s.bills[s.bills["date"] > s.origin - pd.Timedelta(days=period_days)]
        out["bills_paid_in_period"] = {c: _r(v, 0) for c, v in
                                       recent.groupby("category")["amount"].sum().items()}
    out["capabilities"] = capabilities(s.history, s.profile()).supported
    return s._stamp("get_business_overview", {"period_days": period_days}, out)


def get_economic_indicators(s: BusinessSession, refresh: bool = False) -> dict:
    """Point-in-time FRED readings as of the business's last data date."""
    industry = s.industry if s.industry in DEFAULT_SERIES else "restaurant"
    specs = DEFAULT_SERIES[industry]
    obs, notes, mode = [], [], "cache"
    conn = FREDConnector(cache_dir=s.fred_cache_dir, offline=not refresh, session=s.fred_session)
    if refresh and not conn.api_key:
        conn, mode = FREDConnector(cache_dir=s.fred_cache_dir, offline=True), "cache"
        notes.append("FRED_API_KEY not configured: served from the local cache instead of live.")
    elif refresh:
        mode = "live_api"
    for spec in specs:
        try:
            v, prov = conn.fetch_vintages(spec.series_id, "2021-01-01")
        except ConnectorError as exc:
            notes.append(f"{spec.series_id}: unavailable ({exc})")
            continue
        live = v[(v.realtime_start <= s.origin) & (v.realtime_end >= s.origin)]
        snap = live.set_index("observed_at")["value"].sort_index()
        if snap.empty:
            continue
        latest = snap.index.max()
        comp = _latest_comparable(snap, pd.DateOffset(years=1))
        change, unit = None, "%"
        if comp is not None:
            prior = snap[comp - pd.DateOffset(years=1)]
            if spec.transform == "level_chg3":
                change, unit = float(snap[comp] - prior), "percentage points"
            else:
                change = 100 * (float(snap[comp]) / float(prior) - 1)
        obs.append({
            "category": "observed_economic_data", "source": "FRED/ALFRED", "series_id": spec.series_id,
            "title": prov.notes.split(";")[0], "units": prov.units, "geography": "United States",
            "why_relevant": spec.why, "observed_at": str(latest.date())[:7], "value": _r(snap[latest], 3),
            "published_at": str(v.loc[v.observed_at == latest, "published_at"].min().date()),
            "change_12m": _r(change, 2), "change_12m_unit": unit, "change_period_months": 12,
            "change_12m_period": None if comp is None else str(comp.date())[:7],
            "retrieved_at_utc": str(v["retrieved_at_utc"].iloc[0]), "retrieval": mode,
            "source_url": prov.source_url})
    return s._stamp("get_economic_indicators", {"refresh": refresh}, {
        "as_of_rule": "values as published on or before the business's last data date",
        "observations": obs, "notes": notes,
        "not_used_in_forecasts": "FRED features failed validation (Milestones 2/3.5); used as "
                                 "context and for hypothetical stress tests only"})


def forecast_business(s: BusinessSession, target: str = "revenue") -> dict:
    if target not in FORECAST_TARGETS:
        raise ValueError(f"target must be one of {FORECAST_TARGETS}")
    if target != "net_cash_flow" and target not in s.history.columns:
        raise ValueError(f"the uploaded data has no '{target}' column")
    b = s.board(target)
    sel = next(m for m in b.models if m.name == b.selected)
    bundle = s.bundle((target,))
    sims = bundle.simulate(make_draws(bundle, 4000, seed=11))[target]
    total = sims.sum(axis=1)
    tf = bundle.targets[target]
    weekly = [float(tf.point[i:i + 7].sum()) for i in range(0, bundle.horizon, 7)]
    return s._stamp("forecast_business", {"target": target}, {
        "target": target, "horizon_days": bundle.horizon,
        "forecast_start": str(tf.dates[0].date()), "forecast_end": str(tf.dates[-1].date()),
        "selected_model": b.selected, "selection_reason": b.selection_reason,
        "point_total": _r(tf.point.sum(), 0), "interval_80_total": [_r(np.quantile(total, .1), 0),
                                                                   _r(np.quantile(total, .9), 0)],
        "interval_method": "whole-window Monte Carlo: each simulated path replays one historical "
                           "out-of-sample 28-day forecast-error window",
        "interval_80_total_validation": _validation_dict(b),
        "weekly_point_totals": [_r(w, 0) for w in weekly],
        "validation": {"wape": _r(sel.validation["wape"], 3), "mae": _r(sel.validation["mae"], 1),
                       "period": b.meta["validation_period"], "folds": b.meta["n_validation_folds"]},
        "final_test": {"wape": _r(sel.holdout["wape"], 3), "mae": _r(sel.holdout["mae"], 1),
                       "interval_80_coverage": _r(sel.holdout_interval_coverage, 2),
                       "interval_80_coverage_meaning": "daily coverage of per-day ranges built from "
                                                       "validation errors only (not the 28-day "
                                                       "total range above)",
                       "period": b.meta["holdout_period"], "folds": b.meta["n_holdout_folds"]},
        "leaderboard": [{"model": m.name, "validation_wape": _r(m.validation["wape"], 3),
                         "test_wape": _r(m.holdout["wape"], 3)} for m in b.models if m.available],
        "caveat": f"Accuracy measured on {s.data_label} data only; not validated on a real "
                  "small business. 28-day horizon is the only horizon evaluated."})


def _validation_dict(board) -> dict:
    from ..calibration import board_validation
    v = board_validation(board, 28).as_dict()
    return {k: (_r(x, 3) if isinstance(x, float) else x) for k, x in v.items()}


def _decision_payload(run: DecisionRun, scenario: dict) -> dict:
    rec = run.recommendation
    spec = OBJECTIVES[rec.objective]
    base = next(o for o in run.outcomes if o.action.is_baseline)
    actions = []
    for r in rec.ranking:
        o = next(x for x in run.outcomes if x.action.action_id == r.action_id)
        row = {"action_id": r.action_id, "label": r.label, "rank": r.rank, "feasible": r.feasible,
               "violations": r.violations,
               f"expected_{spec.metric}": _r(r.objective_mean, 4 if spec.unit == "probability" else 0),
               "change_vs_no_action": _r(r.delta_mean, 4 if spec.unit == "probability" else 0),
               "change_90pct_range": [_r(r.delta_p05, 0), _r(r.delta_p95, 0)],
               "simulated_share_better_than_no_action": _r(r.prob_better_than_no_action, 3),
               "implementation_cost": _r(o.action.implementation_cost, 0)}
        for m in ("profit", "revenue", "operating_cost", "stockout_after_arrival", "lost_units",
                  "cash_outlay", "cash_shortage"):
            if m in o.metrics and m != spec.metric:
                row[f"expected_{m}"] = _r(o.metrics[m].mean, 4 if o.metrics[m].unit == "probability" else 0)
        actions.append(row)
    assumptions = rec.evidence.get("assumptions", [])
    return {
        "objective": rec.objective.value, "metric": spec.metric, "unit": spec.unit,
        "horizon_days": rec.horizon_days, "scenario": scenario,
        "baseline_expected_profit": _r(base.metrics["profit"].mean, 0) if "profit" in base.metrics else None,
        "actions": actions, "recommended_action": rec.recommended_action_id,
        "strength": rec.strength, "rationale": rec.rationale, "limitations": rec.limitations,
        "sensitivity": [{k: (_r(v, 3) if isinstance(v, float) else v) for k, v in x.items()}
                        for x in rec.sensitivity],
        "assumptions": assumptions, "constraints": rec.evidence.get("constraints", []),
        "forecast_models": {t: f["selected_model"] for t, f in rec.evidence["forecasts"].items()},
        "stress_test": run.stress, "effect_type": rec.effect_type,
        "requires_human_approval": True,
        "note_on_probabilities": "Shares are frequencies across simulated scenarios built from "
                                 "the stated assumptions; they are not real-world guarantees.",
    }


def evaluate_restaurant_actions(s: BusinessSession, food_cost_change_pct: float | None = None,
                                elasticity_mode: float | None = None,
                                elasticity_low: float | None = None,
                                elasticity_high: float | None = None,
                                max_price_increase: float | None = None,
                                starting_cash: float | None = None,
                                objective: str = "max_expected_profit") -> dict:
    if s.industry != "restaurant":
        raise ValueError("restaurant actions apply to restaurant businesses only")
    st = s.state
    if food_cost_change_pct is not None:
        st.food_cost_change_pct = float(food_cost_change_pct)
    if any(v is not None for v in (elasticity_mode, elasticity_low, elasticity_high)):
        st.elasticity_mode = float(elasticity_mode if elasticity_mode is not None else st.elasticity_mode)
        st.elasticity_low = float(elasticity_low if elasticity_low is not None else min(st.elasticity_low, st.elasticity_mode))
        st.elasticity_high = float(elasticity_high if elasticity_high is not None else max(st.elasticity_high, st.elasticity_mode))
        st.elasticity_low = min(st.elasticity_low, st.elasticity_mode)
        st.elasticity_high = max(st.elasticity_high, st.elasticity_mode)
        st.elasticity_source = "user_declared"
    if max_price_increase is not None:
        st.max_price_increase = float(max_price_increase)
    if starting_cash is not None:
        st.starting_cash = float(starting_cash)
    obj = Objective(objective)
    elasticity = Assumption("price_elasticity", st.elasticity_mode, "ratio", st.elasticity_source,
                            st.elasticity_low, st.elasticity_high, "triangular", ELASTICITY.note
                            if st.elasticity_source != "user_declared" else
                            "Changed by the user in this conversation.")
    base_cfg = RestaurantDecisionConfig()
    cons = tuple(Constraint(c.name, c.target, c.kind,
                            st.max_price_increase if c.target == "param:price_increase"
                            and st.max_price_increase is not None else c.limit, c.unit,
                            "user_declared" if c.target == "param:price_increase"
                            and st.max_price_increase is not None else c.source)
                 for c in base_cfg.constraints)
    cfg = replace(base_cfg, elasticity=elasticity, constraints=cons)
    bundle = s.bundle(("revenue", "variable_costs", "fixed_costs"))
    if st.food_cost_change_pct:
        bundle = stressed(bundle, {"variable_costs": 1 + st.food_cost_change_pct / 100})
    cash, cash_src = s._cash_now()
    ctx = OrganizationContext(s.metadata.get("business_name", s.dataset), "restaurant",
                              s.data_label, starting_cash=None if cash is None else
                              StartingCash(cash, cash_src, True))
    run = run_restaurant(s.history, ctx, obj, dataset=s.dataset, cfg=cfg, bundle=bundle,
                         n_sims=s.n_sims)
    scenario = {"food_cost_change_pct": st.food_cost_change_pct,
                "food_cost_change_type": "hypothetical (user scenario), applied to forecast "
                                         "variable costs" if st.food_cost_change_pct else "none",
                "price_elasticity": {"mode": st.elasticity_mode, "low": st.elasticity_low,
                                     "high": st.elasticity_high, "source": st.elasticity_source},
                "max_price_increase": cons[0].limit, "starting_cash": _r(cash, 0),
                "starting_cash_source": cash_src}
    return s._stamp("evaluate_restaurant_actions", {
        "food_cost_change_pct": food_cost_change_pct, "elasticity_mode": elasticity_mode,
        "elasticity_low": elasticity_low, "elasticity_high": elasticity_high,
        "max_price_increase": max_price_increase, "starting_cash": starting_cash,
        "objective": objective}, _decision_payload(run, scenario))


def evaluate_inventory_policies(s: BusinessSession, purchase_budget_usd: float | None = None,
                                storage_units: float | None = None,
                                max_stockout_probability: float | None = None,
                                objective: str = "max_expected_profit") -> dict:
    st = s.state
    if purchase_budget_usd is not None:
        st.purchase_budget_usd = float(purchase_budget_usd)
    if storage_units is not None:
        st.storage_units = float(storage_units)
    if max_stockout_probability is not None:
        st.max_stockout_probability = float(max_stockout_probability)
    cons = []
    if st.purchase_budget_usd is not None:
        cons.append(Constraint("28-day purchase budget", "metric:cash_outlay:mean", "max",
                               st.purchase_budget_usd, "USD", "user_declared"))
    if st.storage_units is not None:
        cons.append(Constraint("storage capacity", "metric:peak_inventory:p95", "max",
                               st.storage_units, "units", "declared in business profile"))
    if st.max_stockout_probability is not None:
        cons.append(Constraint("stockout tolerance after arrival",
                               "metric:stockout_after_arrival:mean", "max",
                               st.max_stockout_probability, "probability",
                               "declared in business profile"))
    cfg = RetailDecisionConfig(constraints=tuple(cons))
    ctx = OrganizationContext(s.metadata.get("business_name", s.dataset), "retail", s.data_label)
    missing = [c for c in ("units_sold", "inventory_on_hand") if c not in s.history.columns]
    if missing:
        return s._stamp("evaluate_inventory_policies", {"purchase_budget_usd": purchase_budget_usd},
                        {"supported": False, "missing_columns": missing,
                         "reason": "Inventory decisions need daily units sold and inventory on "
                                   "hand; nothing was calculated."})
    run = run_retail(s.history, ctx, Objective(objective), dataset=s.dataset, cfg=cfg,
                     bundle=s.bundle(("units_sold",)), n_sims=s.n_sims)
    if run.unsupported:
        return s._stamp("evaluate_inventory_policies", {}, {"supported": False,
                                                           **asdict(run.unsupported)})
    payload = _decision_payload(run, {
        "purchase_budget_usd": st.purchase_budget_usd, "storage_units": st.storage_units,
        "max_stockout_probability": st.max_stockout_probability,
        "budget_note": None if st.purchase_budget_usd is not None else
        "No purchase budget declared: purchases are unconstrained by cash."})
    payload["policy_levels"] = {k: _r(v, 0) for k, v in run.extra.items()}
    payload["supported"] = True
    return s._stamp("evaluate_inventory_policies", {
        "purchase_budget_usd": purchase_budget_usd, "storage_units": storage_units,
        "max_stockout_probability": max_stockout_probability, "objective": objective}, payload)


def assess_cash_risk(s: BusinessSession, starting_cash: float | None = None) -> dict:
    if starting_cash is not None:
        s.state.starting_cash = float(starting_cash)
    cash, src = s._cash_now()
    if cash is None:
        return s._stamp("assess_cash_risk", {"starting_cash": starting_cash},
                        {"available": False, "reason": src})
    b = s.board("net_cash_flow")
    bundle = s.bundle(("net_cash_flow",))
    r = backtested_cash_risk(bundle.targets["net_cash_flow"].point, b.val_residuals,
                             b.holdout_actual - b.holdout_pred, b.holdout_pred, b.holdout_actual,
                             starting_cash=cash, n=4000)
    from ..calibration import cash_validation
    try:
        cv = cash_validation(s.history, b)
    except ValueError as exc:
        cv = {"summary": f"not validated ({exc})", "assessable": False}
    return s._stamp("assess_cash_risk", {"starting_cash": starting_cash}, {
        "available": True, "starting_cash": _r(cash, 0), "starting_cash_source": src,
        "horizon_days": r["horizon_days"], "net_cash_model": b.selected,
        "simulated_shortfall_frequency": _r(r["simulated_shortfall_frequency"], 4),
        "median_ending_cash": _r(r["median_ending_cash"], 0),
        "ending_cash_80pct_interval": [_r(x, 0) for x in r["ending_cash_80%_interval"]],
        "calibration": r["holdout_calibration_check"]["verdict"],
        "range_validation": {k: (_r(x, 3) if isinstance(x, float) else x) for k, x in cv.items()},
        "warning": ("The shortfall frequency is a simulation frequency, NOT a validated "
                    "probability: it has not been checked against real shortfalls. "
                    + cv["summary"] + "."),
        "label": r["label"] + " Accrual-basis net cash flow (operating), not bank cash timing."})


def get_assumptions(s: BusinessSession) -> dict:
    return s._stamp("get_assumptions", {}, {
        "scenario_state": asdict(s.state),
        "business_assumptions": [a for a in s.metadata.get("assumptions", [])
                                 if "hidden from the models" not in a],
        "declared_constraints": s.metadata.get("constraints", {}),
        "opening_cash": s.metadata.get("opening_cash")})


def _round_tree(x, nd=2):
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _round_tree(v, nd) for k, v in x.items()}
    if isinstance(x, list):
        return [_round_tree(v, nd) for v in x]
    return x


def _product_unavailable(s: BusinessSession, name: str, inputs: dict, exc: Exception) -> dict:
    return s._stamp(name, inputs, {"available": False, "reason": str(exc),
                                   "what_is_needed": "daily records per product: units sold, "
                                                     "price, unit cost, and stock or ingredient "
                                                     "inventory"})


def get_product_performance(s: BusinessSession, period_days: int = 28) -> dict:
    """Five tracked products: observed sales and margins, forecast demand, stock and risk."""
    from .. import dashboard
    from ..decision.product_scenario import ELASTICITY as PELAST
    period = {7: "28d", 28: "28d", 90: "90d", 365: "365d"}.get(int(period_days), "28d")
    try:
        t = dashboard.product_table(s, period)
    except ProductLayerUnavailable as exc:
        return _product_unavailable(s, "get_product_performance", {"period_days": period_days}, exc)
    ctx = s.product_context()
    rows = []
    for r in t.to_dict(orient="records"):
        rows.append({"product": r["product"], "product_id": r["product_id"],
                     "menu_price": r["price"], "ingredient_cost_per_unit": round(r["unit_cost"], 2),
                     "units_sold": r["units_sold"], "revenue": round(r["revenue"]),
                     "gross_profit": round(r["gross_profit"]),
                     "gross_margin_pct": round(r["gross_margin_pct"], 1),
                     "stockout_days": r["stockout_days"],
                     "forecast_demand_next_7_days": round(r["forecast_demand_7d"]),
                     "stock_days_of_supply": round(r["days_of_supply"], 1),
                     "stockout_risk_next_7_days_pct": round(r["stockout_risk_7d"] * 100),
                     "forecast_model": ctx.boards[r["product_id"]]["selected_model"],
                     "forecast_test_mae_units_per_day": round(ctx.boards[r["product_id"]]["test_mae"], 1),
                     "assumed_price_elasticity": PELAST[r["product_id"]].value})
    by = {k: max(rows, key=lambda r: r[k])["product"] for k in ("units_sold", "gross_profit", "revenue")}
    low = min(rows, key=lambda r: r["gross_margin_pct"])["product"]
    risk = max(rows, key=lambda r: (r["stockout_risk_next_7_days_pct"], -r["stock_days_of_supply"]))
    w = dashboard.window(s.history, period)
    return s._stamp("get_product_performance", {"period_days": period_days}, {
        "available": True, "period": w.label, "products": rows,
        "best_seller_by_revenue": by["revenue"], "most_units_sold": by["units_sold"],
        "most_profitable_by_gross_profit": by["gross_profit"],
        "lowest_gross_margin": low, "highest_stockout_risk": risk["product"],
        "definitions": {"gross_profit": "revenue minus ingredient cost of the units sold",
                        "stockout_risk": "share of simulated next-7-day paths leaving at least 1% "
                                         "of demand unmet under the current ordering plan",
                        "days_of_supply": "days until the first ingredient the product needs runs "
                                          "out at forecast usage, before the next delivery"},
        "origins": {"sales_and_margins": "observed (SYNTHETIC records)",
                    "demand_forecast": "model forecast (validated on synthetic history only)",
                    "stockout_risk": "simulation under the ordering plan and assumptions"}})


def simulate_product_scenario(s: BusinessSession, product: str | None = None,
                              price_change_pct: float | None = None,
                              supply_change_pct: float | None = None,
                              extra_units: float | None = None,
                              demand_shock_pct: float | None = None,
                              demand_shock_scope: str | None = None,
                              marketing_change_pct: float | None = None,
                              horizon_days: int | None = None,
                              ingredient_cost_change_pct: float | None = None) -> dict:
    """Simulate one product scenario against the current plan; nulls keep the Scenario Lab's
    current setting (or no change)."""
    from ..decision.product_scenario import ProductScenario, baseline, compare, recommend, simulate
    inputs = {"product": product, "price_change_pct": price_change_pct,
              "supply_change_pct": supply_change_pct, "extra_units": extra_units,
              "demand_shock_pct": demand_shock_pct, "demand_shock_scope": demand_shock_scope,
              "marketing_change_pct": marketing_change_pct, "horizon_days": horizon_days,
              "ingredient_cost_change_pct": ingredient_cost_change_pct}
    try:
        ctx = s.product_context()
    except ProductLayerUnavailable as exc:
        return _product_unavailable(s, "simulate_product_scenario", inputs, exc)
    cur = dict(s.state.product_scenario or {})
    pid = (product or cur.get("product_id") or "tacos").strip().lower()
    pid = {"taco": "tacos", "burrito": "burritos", "quesadilla": "quesadillas", "nacho": "nachos",
           "drink": "drinks"}.get(pid, pid)
    same = pid == cur.get("product_id")

    def pick(name, given, default=0.0):
        if given is not None:
            return given
        return cur.get(name, default) if same else default

    sc = ProductScenario(
        pid, float(pick("price_change_pct", price_change_pct)),
        float(pick("supply_change_pct", supply_change_pct)), float(pick("extra_units", extra_units)),
        float(pick("demand_shock_pct", demand_shock_pct)),
        str(pick("demand_shock_scope", demand_shock_scope, "product")),
        float(pick("marketing_change_pct", marketing_change_pct)),
        int(horizon_days or cur.get("horizon_days") or 28),
        float(pick("ingredient_cost_change_pct", ingredient_cost_change_pct)))
    s.state.product_scenario = asdict(sc)
    c = compare(ctx, simulate(ctx, sc), baseline(ctx, sc.horizon_days, sc.product_id))
    rec = recommend(ctx, sc)

    def money(b):
        return {"baseline": round(b["baseline"]), "scenario": round(b["scenario"]),
                "change": round(b["change"]),
                "change_pct": None if b["change_pct"] is None else round(b["change_pct"], 1),
                "change_90pct_range": [round(v) for v in b["change_90pct_range"]]}

    out = {
        "available": True, "scenario": c["scenario"], "scenario_text": c["scenario_text"],
        "horizon_days": c["horizon_days"], "forecast_window": [c["forecast_start"], c["forecast_end"]],
        "selected_product_current_price": c["current_price"],
        "selected_product_new_price": c["new_price"],
        "revenue": money(c["revenue"]), "operating_profit": money(c["profit"]),
        "operating_cash_flow": money(c["cash_flow"]),
        "selected_product_demand_units": money(c["demand_units"]),
        "selected_product_units_sold": money(c["units_sold"]),
        "gross_margin_pct": {k: round(v, 1) for k, v in c["gross_margin_pct"].items()},
        "ending_ingredient_inventory_value": money(c["ending_inventory_value"]),
        "selected_product_stockout_risk_pct": {k: round(v * 100) for k, v in c["stockout_risk"].items()},
        "probability_profit_improves_pct": round(c["probability_profit_improves"] * 100),
        "product_impacts": [{"product": p["product"], "revenue_change": round(p["revenue_change"]),
                             "gross_profit_change": round(p["gross_profit_change"]),
                             "units_sold_change": round(p["units_sold_change"])}
                            for p in c["products"]],
        "within_owner_policy": c["within_policy"], "policy_violations": c["policy_violations"],
        "recommendation": rec["text"],
        "recommended_action": rec["recommended"]["action"] if rec["recommended"] else None,
        "alternatives": [{"action": r["action"], "profit_change": round(r["profit_change"]),
                          "profit_change_90pct_range": [round(v) for v in r["profit_change_90pct_range"]]}
                         for r in rec["ranked"][:4]],
        "sensitivity": [{"assumption": x["assumption"], "level": x["level"], "value": x["value"],
                         "profit_change": round(x["profit_change"])} for x in rec["sensitivity"]],
        "assumptions": [{k: a[k] for k in ("name", "value", "low", "high", "source")}
                        for a in c["assumptions"]],
        "interval_meaning": c["interval_meaning"], "effect_type": c["effect_type"],
        "range_check": {"revenue_total": c["range_validation"]["revenue_total"]["summary"],
                        "profit_total": c["range_validation"]["profit_total"]["summary"],
                        "not_validated": c["range_validation"]["note"]},
        "n_simulations": c["n_simulations"], "requires_human_approval": True}
    return s._stamp("simulate_product_scenario", inputs, out)


def explain_profit_change(s: BusinessSession, period_days: int = 28) -> dict:
    from .. import dashboard
    out = dashboard.profit_change(s, int(period_days))
    out["drivers"] = [{k: (round(v) if isinstance(v, float) else v) for k, v in d.items()}
                      for d in out["drivers"][:8]]
    out["profit"] = {k: round(v) for k, v in out["profit"].items()}
    out["products"] = [{k: (round(v) if isinstance(v, float) else v) for k, v in p.items()}
                       for p in out["products"]]
    return s._stamp("explain_profit_change", {"period_days": period_days}, out)


def get_local_market_prices(s: BusinessSession, product: str | None = None) -> dict:
    """Our menu price vs verified comparable local menus (public data, kept separate)."""
    from .. import market
    inputs = {"product": product}
    try:
        ctx = s.product_context()
    except ProductLayerUnavailable as exc:
        return _product_unavailable(s, "get_local_market_prices", inputs, exc)
    from ..products import PRODUCT_IDS
    pid = (product or "tacos").strip().lower()
    pid = {"taco": "tacos", "burrito": "burritos", "quesadilla": "quesadillas", "nacho": "nachos",
           "drink": "drinks"}.get(pid, pid)
    if pid not in PRODUCT_IDS:
        raise ValueError(f"product must be one of {PRODUCT_IDS}")
    c = market.compare(pid, float(ctx.prices[PRODUCT_IDS.index(pid)]))
    out = {k: c[k] for k in ("product_id", "market", "our_serving", "our_price", "peer_count",
                             "sufficient", "warnings", "not_a_recommendation", "data_origin")}
    for k in ("peer_min", "peer_median", "peer_max", "difference_vs_median",
              "difference_vs_median_pct", "position", "peers_cheaper", "peers_more_expensive"):
        if k in c:
            out[k] = c[k]
    out["comparables"] = [{"restaurant": x["restaurant"], "type": x["concept"], "item": x["item"],
                           "listed_price": x["listed_price"],
                           "price_for_our_serving": x["comparable_price"], "match": x["match_level"],
                           "verified_on": x["verified_date"], "source": x["source_url"]}
                          for x in c["comparables"]]
    out["excluded_count"] = len(c["excluded"])
    out["available"] = True
    return s._stamp("get_local_market_prices", inputs, out)


def get_daily_briefing(s: BusinessSession) -> dict:
    """Yesterday vs a typical same weekday, what needs attention, and one decision to consider."""
    from ..briefing import daily_briefing
    b = daily_briefing(s)
    return s._stamp("get_daily_briefing", {}, {k: v for k, v in b.items()})


def create_alert_rule(s: BusinessSession, kind: str, series_id: str | None = None,
                      threshold: float | None = None) -> dict:
    from ..monitor import MonitorStore
    store = MonitorStore(s.monitor_path)
    rule = store.add_rule(s.metadata.get("key", s.dataset), kind, series_id, threshold, False)
    return s._stamp("create_alert_rule", {"kind": kind, "series_id": series_id,
                                          "threshold": threshold},
                    {"rule": asdict(rule), "delivery": "in-app (Telegram requires explicit opt-in "
                                                       "in the Alerts tab)"})


def list_alerts(s: BusinessSession) -> dict:
    from ..monitor import MonitorStore
    store = MonitorStore(s.monitor_path)
    return s._stamp("list_alerts", {}, {
        "rules": [asdict(r) for r in store.rules], "last_run": store.last_run,
        "alerts": [{"title": a.title, "message": a.message, "created_at": a.created_at,
                    "category": a.category} for a in store.alerts[-10:]]})


def get_banking_summary(s: BusinessSession) -> dict:
    from ..banking import import_banking
    try:
        b = import_banking(s.nessie_client)
    except (ConnectorError, ValueError, KeyError) as exc:
        return s._stamp("get_banking_summary", {}, {"available": False,
                                                   "reason": f"{type(exc).__name__}: {exc}"})
    return s._stamp("get_banking_summary", {}, {
        "available": True, "source": b.source, "accounts": b.accounts,
        "transactions": len(b.transactions), "duplicates_dropped": b.duplicates_dropped,
        "cash_by_category": b.by_category.round(2).to_dict(orient="records"),
        "missing_bills": b.missing_bills, "forecast_eligible": b.forecast_eligible,
        "eligibility_reasons": b.eligibility_reasons, "notes": b.notes})


TOOL_FUNCS = {
    "create_alert_rule": create_alert_rule,
    "list_alerts": list_alerts,
    "get_banking_summary": get_banking_summary,
    "get_business_overview": get_business_overview,
    "get_economic_indicators": get_economic_indicators,
    "forecast_business": forecast_business,
    "evaluate_restaurant_actions": evaluate_restaurant_actions,
    "evaluate_inventory_policies": evaluate_inventory_policies,
    "assess_cash_risk": assess_cash_risk,
    "get_assumptions": get_assumptions,
    "get_product_performance": get_product_performance,
    "simulate_product_scenario": simulate_product_scenario,
    "explain_profit_change": explain_profit_change,
    "get_local_market_prices": get_local_market_prices,
    "get_daily_briefing": get_daily_briefing,
}


def _nullable(t: str, desc: str) -> dict:
    return {"type": [t, "null"], "description": desc}


TOOL_SCHEMAS = [
    {"name": "get_business_overview",
     "description": "Summarize the business's recent revenue, costs, operating profit, cash "
                    "balance (with its source), inventory and bills for the last N days, compared "
                    "with the previous N days. Use for 'how is my business doing' questions.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["period_days"],
                      "properties": {"period_days": {"type": "integer",
                                                     "description": "Days to summarize (7-365)."}}}},
    {"name": "forecast_business",
     "description": "Forecast the next 28 days of a business metric with the model selected by "
                    "chronological validation, including an 80% interval and the measured "
                    "validation and final-test accuracy.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["target"],
                      "properties": {"target": {"type": "string", "enum": list(FORECAST_TARGETS)}}}},
    {"name": "evaluate_restaurant_actions",
     "description": "Simulate and rank restaurant actions (menu price increases, supplier "
                    "negotiation, discretionary cost cuts) against doing nothing over 28 days, "
                    "under optional hypothetical food-cost changes and price-sensitivity "
                    "assumptions. Pass null to keep the session's current value. Changed values "
                    "persist for follow-up questions.",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["food_cost_change_pct", "elasticity_mode", "elasticity_low",
                                   "elasticity_high", "max_price_increase", "starting_cash",
                                   "objective"],
                      "properties": {
                          "food_cost_change_pct": _nullable("number", "Hypothetical % change in "
                                                            "food/variable costs, e.g. 15."),
                          "elasticity_mode": _nullable("number", "Most likely price elasticity "
                                                       "(negative; more negative = more sensitive)."),
                          "elasticity_low": _nullable("number", "Most price-sensitive bound."),
                          "elasticity_high": _nullable("number", "Least price-sensitive bound."),
                          "max_price_increase": _nullable("number", "Largest allowed price "
                                                          "increase as a fraction, e.g. 0.05."),
                          "starting_cash": _nullable("number", "Starting cash in USD."),
                          "objective": {"type": "string", "enum": [o.value for o in Objective]}}}},
    {"name": "evaluate_inventory_policies",
     "description": "Simulate and rank weekly inventory policies (reorder to a service level, "
                    "maintain stock, reduce purchasing, no orders) over 28 days with lead time, "
                    "holding cost and lost sales, checking a purchase budget, storage capacity "
                    "and stockout tolerance. Pass null to keep the current value.",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["purchase_budget_usd", "storage_units",
                                   "max_stockout_probability", "objective"],
                      "properties": {
                          "purchase_budget_usd": _nullable("number", "28-day purchase budget."),
                          "storage_units": _nullable("number", "Storage capacity in units."),
                          "max_stockout_probability": _nullable("number", "Tolerated probability "
                                                                "of any stockout after the "
                                                                "first delivery (0-1)."),
                          "objective": {"type": "string", "enum": [o.value for o in Objective]}}}},
    {"name": "assess_cash_risk",
     "description": "Simulate 28-day cash shortfall frequency from backtested forecast errors. "
                    "Needs a starting cash balance (uses the dataset ledger if available).",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["starting_cash"],
                      "properties": {"starting_cash": _nullable("number", "Starting cash in USD.")}}},
    {"name": "create_alert_rule",
     "description": "Save an in-app monitoring rule: 'cash_shortfall' alerts when the simulated "
                    "28-day cash shortfall frequency reaches the threshold (0-1).",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["kind", "series_id", "threshold"],
                      "properties": {"kind": {"type": "string", "enum": ["cash_shortfall"]},
                                     "series_id": _nullable("string", "Leave null."),
                                     "threshold": _nullable("number", "Threshold.")}}},
    {"name": "list_alerts",
     "description": "List saved monitoring rules and the most recent alerts.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": [],
                      "properties": {}}},
    {"name": "get_banking_summary",
     "description": "Import SIMULATED banking records from the Capital One Nessie sandbox "
                    "(read-only): accounts, categorized inflows/outflows, missing bills, and "
                    "whether the history is fit for forecasting.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": [],
                      "properties": {}}},
    {"name": "get_assumptions",
     "description": "List the current scenario assumptions, declared constraints and data "
                    "assumptions with their sources.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": [],
                      "properties": {}}},
    {"name": "get_product_performance",
     "description": "Per-product table for the five tracked products (tacos, burritos, "
                    "quesadillas, nachos, drinks): price, ingredient cost, units, revenue, gross "
                    "profit and margin, forecast demand for the next 7 days, days of ingredient "
                    "supply and simulated stock-out risk. Use for 'which product is most "
                    "profitable / best selling / at risk' questions.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["period_days"],
                      "properties": {"period_days": {"type": "integer", "enum": [28, 90, 365],
                                                     "description": "Lookback for observed sales."}}}},
    {"name": "simulate_product_scenario",
     "description": "Decision Lab: simulate changing ONE product's price, its ingredient orders, "
                    "a one-time extra ingredient purchase, a supplier ingredient-cost change (applies "
                    "to all ingredients), a demand shock, or marketing spend, "
                    "against the current plan over 7, 14 or 28 days. Returns revenue, operating "
                    "profit, cash flow, demand, units sold, stock-out risk, product impacts, the "
                    "owner's price policy check, a ranked recommendation and sensitivity. Pass "
                    "null to keep the Decision Lab's current value (or no change).",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["product", "price_change_pct", "supply_change_pct",
                                   "extra_units", "demand_shock_pct", "demand_shock_scope",
                                   "marketing_change_pct", "horizon_days",
                                   "ingredient_cost_change_pct"],
                      "properties": {
                          "product": _nullable("string", "tacos, burritos, quesadillas, nachos "
                                                         "or drinks."),
                          "price_change_pct": _nullable("number", "Price change in %, e.g. 5."),
                          "supply_change_pct": _nullable("number", "% change in this product's "
                                                                   "share of planned ingredient orders."),
                          "extra_units": _nullable("number", "Buy ingredients now for this many "
                                                             "extra units of the product."),
                          "demand_shock_pct": _nullable("number", "Hypothetical demand change in %."),
                          "demand_shock_scope": _nullable("string", "'product' (only this "
                                                                    "product) or 'all'."),
                          "marketing_change_pct": _nullable("number", "% change in marketing & "
                                                                      "software spend."),
                          "horizon_days": _nullable("integer", "7, 14 or 28 days."),
                          "ingredient_cost_change_pct": _nullable("number", "Hypothetical % change "
                                                                  "in supplier ingredient prices, e.g. 8.")}}},
    {"name": "explain_profit_change",
     "description": "Explain why operating profit changed between the last N days and the "
                    "previous N days: revenue, each expense category and each product's revenue "
                    "change (an accounting decomposition, not a causal claim).",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["period_days"],
                      "properties": {"period_days": {"type": "integer", "enum": [28, 90, 365]}}}},
    {"name": "get_local_market_prices",
     "description": "Compare one of the owner's menu prices with verified, comparable menu prices "
                    "of nearby independent restaurants (public official menus, dated and "
                    "sourced; servings normalized, e.g. 3 single tacos = a plate of three). Returns "
                    "the peer range, median, sample size and warnings. It does NOT say how customers "
                    "would react and is never by itself a reason to change a price.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["product"],
                      "properties": {"product": _nullable("string", "tacos, burritos, quesadillas, "
                                                                    "nachos or drinks.")}}},
    {"name": "get_daily_briefing",
     "description": "The owner's daily briefing: yesterday's revenue, profit and guests vs a typical "
                    "same weekday, products that fell more than traffic, stock to watch, bills due "
                    "in 7 days, and one decision to consider with impact, risks and what to verify.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": [],
                      "properties": {}}},
]


def run_tool(s: BusinessSession, name: str, args: dict) -> dict:
    if name not in TOOL_FUNCS:
        raise ValueError(f"unknown tool {name!r}")
    return TOOL_FUNCS[name](s, **{k: v for k, v in (args or {}).items()})
