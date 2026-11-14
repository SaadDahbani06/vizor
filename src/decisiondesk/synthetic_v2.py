"""Realistic SYNTHETIC U.S. small businesses for the end-to-end demo (v2).

Everything here is generated; nothing is a real business. The v1 generator (`synthetic.py`) is
kept unchanged so Milestone 1-3 results stay reproducible.

What makes v2 realistic (and not trivially predictable):
  * Demand: negative-binomial daily counts (overdispersed) with weekday and annual seasonality,
    a slow trend, an AR(1) latent shock (weather / local events), U.S. holidays and closures,
    local events, and a demand response to real price changes / promotions.
  * Prices: menu price steps (restaurant) and promotions + annual list-price changes (retail).
  * Costs: supplier price index as a drifting random walk with a commodity shock; card fees;
    recurring bills on real schedules (rent on the 1st, biweekly payroll, monthly utilities with
    seasonal swings, quarterly insurance, loan, software/marketing).
  * Inventory: periodic deliveries with variable lead times; retail sales are capped by stock on
    hand (lost sales are unobserved, stockout days are flagged).
  * Cash: a ledger from a DECLARED opening balance; card sales settle next day; supplier invoices
    are paid on terms (net-7 restaurant, net-30 retail).
Accrual columns (revenue, variable_costs, fixed_costs) feed the forecasting/decision engine;
cash columns (cash_in, cash_out, cash_balance) feed cash views. Generator "truths" such as the
price elasticity are documented in metadata but are NOT available to the models.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

END_DATE = "2026-11-13"   # fixed for reproducibility (the day before this build)
DAYS = 1096               # three years


@dataclass
class BusinessDataset:
    key: str
    daily: pd.DataFrame
    bills: pd.DataFrame
    metadata: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- calendar
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> pd.Timestamp:
    first = pd.Timestamp(year, month, 1)
    offset = (weekday - first.day_of_week) % 7
    return first + pd.Timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> pd.Timestamp:
    last = pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)
    return last - pd.Timedelta(days=(last.day_of_week - weekday) % 7)


def _easter(year: int) -> pd.Timestamp:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return pd.Timestamp(year, month, day)


def us_calendar(years) -> dict[pd.Timestamp, str]:
    out = {}
    for y in years:
        thanks = _nth_weekday(y, 11, 3, 4)
        out.update({
            pd.Timestamp(y, 1, 1): "new_years_day", pd.Timestamp(y, 2, 14): "valentines",
            _nth_weekday(y, 2, 6, 2): "super_bowl_sunday", pd.Timestamp(y, 5, 5): "cinco_de_mayo",
            _nth_weekday(y, 5, 6, 2): "mothers_day", _last_weekday(y, 5, 0): "memorial_day",
            pd.Timestamp(y, 7, 4): "independence_day", _nth_weekday(y, 9, 0, 1): "labor_day",
            thanks: "thanksgiving", thanks + pd.Timedelta(days=1): "black_friday",
            pd.Timestamp(y, 12, 24): "christmas_eve", pd.Timestamp(y, 12, 25): "christmas",
            pd.Timestamp(y, 12, 31): "new_years_eve", _easter(y): "easter",
        })
    return out


def _ar1(rng, n, phi, sd):
    e = rng.normal(0, sd, n)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    return x


def _negbin(rng, mu, k):
    """Overdispersed counts: var = mu + mu^2 / k."""
    lam = rng.gamma(shape=k, scale=np.maximum(mu, 1e-9) / k)
    return rng.poisson(lam)


def _bill_schedule(dates, rules):
    """rules: list of (payee, category, amount_fn(date)->float, is_due(date)->bool, recurring)."""
    rows = []
    for d in dates:
        for payee, category, amount_fn, is_due, recurring in rules:
            if is_due(d):
                rows.append({"date": d, "payee": payee, "category": category,
                             "amount": round(float(amount_fn(d)), 2), "recurring": recurring})
    return pd.DataFrame(rows, columns=["date", "payee", "category", "amount", "recurring"])


def _payroll_days(dates, first_friday):
    return {d for d in dates if d.day_of_week == 4 and ((d - first_friday).days // 7) % 2 == 0}


# ----------------------------------------------------------------------------- restaurant
def make_restaurant(seed: int = 2026, end: str = END_DATE, days: int = DAYS) -> BusinessDataset:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=end, periods=days, freq="D")
    n = len(dates)
    years = (dates - dates[0]).days / 365.25
    doy = dates.day_of_year.to_numpy()
    cal = us_calendar(range(dates[0].year, dates[-1].year + 1))
    holiday = np.array([cal.get(d, "") for d in dates], dtype=object)

    weekday = np.array([0.72, 0.80, 0.88, 0.98, 1.32, 1.42, 1.10])[dates.day_of_week]
    season = 1 + 0.07 * np.sin(2 * np.pi * (doy - 60) / 365.25) + 0.04 * np.cos(4 * np.pi * doy / 365.25)
    holiday_mult = {"thanksgiving": 0.0, "christmas": 0.0, "christmas_eve": 0.6,
                    "new_years_day": 0.7, "new_years_eve": 1.15, "independence_day": 0.75,
                    "mothers_day": 1.45, "valentines": 1.25, "super_bowl_sunday": 1.3,
                    "cinco_de_mayo": 1.6, "easter": 0.85}
    hmult = np.array([holiday_mult.get(h, 1.0) for h in holiday])
    sxsw = np.array([(d.month == 3 and 8 <= d.day <= 17) for d in dates])  # Austin festival weeks
    event = np.where(sxsw, 1.22, 1.0)

    # Menu price steps; demand responds with a true (hidden) elasticity of -0.7.
    price_steps = [("2024-03-01", 0.040), ("2025-02-01", 0.035), ("2026-03-01", 0.030)]
    price_index = np.ones(n)
    for when, pct in price_steps:
        price_index[dates >= pd.Timestamp(when)] *= 1 + pct
    price_effect = price_index ** -0.7

    shock = _ar1(rng, n, 0.55, 0.07)
    mu = 122 * (1 + 0.03 * years) * weekday * season * hmult * event * price_effect * np.exp(shock)
    covers = np.where(hmult == 0, 0, _negbin(rng, mu, 40))
    closed = hmult == 0

    weekend = np.isin(dates.day_of_week, [4, 5])
    ticket = 21.0 * price_index * np.where(weekend, 1.06, 1.0) * rng.lognormal(0, 0.035, n)
    revenue = np.round(covers * ticket, 2)

    # Supplier price index: weekly random walk (+3.2 %/yr drift) and a beef shock in June 2025.
    weekly = rng.normal(0.032 / 52, 0.006, n // 7 + 2)
    supplier_index = np.exp(np.repeat(np.cumsum(weekly), 7)[:n])
    supplier_index[dates >= pd.Timestamp("2025-06-15")] *= 1.05
    food = covers * 6.90 * supplier_index * rng.lognormal(0, 0.04, n)
    packaging = covers * 0.55
    card_fees = revenue * 0.85 * 0.027
    variable = np.round(food + packaging + card_fees, 2)

    month_days = dates.days_in_month.to_numpy()
    summer = dates.month.isin([6, 7, 8, 9])
    first_friday = next(d for d in dates if d.day_of_week == 4)
    pay_days = _payroll_days(dates, first_friday)
    seas_level = pd.Series(season, index=dates)

    def payroll(d):
        return 13400 * (1 + 0.15 * (seas_level[d] - 1)) * rng.normal(1, 0.03)

    rules = [
        ("Fictional Properties LLC", "rent", lambda d: 6600.0, lambda d: d.day == 1, True),
        ("Austin Energy (simulated)", "utilities",
         lambda d: 1900 * (1.35 if d.month in (6, 7, 8, 9) else 1.0) * rng.normal(1, 0.05),
         lambda d: d.day == 20, True),
        ("Payroll", "payroll", payroll, lambda d: d in pay_days, True),
        ("Fictional Mutual Insurance", "insurance", lambda d: 3150.0,
         lambda d: d.day == 10 and d.month in (1, 4, 7, 10), True),
        ("Equipment loan (simulated)", "loan", lambda d: 1450.0, lambda d: d.day == 15, True),
        ("Marketing & software", "discretionary", lambda d: 1050.0, lambda d: d.day == 5, True),
        ("Supplies, repairs & services", "other_operating",
         lambda d: 1700 * rng.normal(1, 0.08), lambda d: d.day_of_week == 0, True),
        ("Owner distribution", "owner_draw", lambda d: 6000.0, lambda d: d.day == 28, False),
    ]
    bills = _bill_schedule(dates, rules)
    # Accrual: spread each recurring obligation over the period it covers.
    acc = np.zeros(n)
    by_cat = bills.groupby("category")["amount"].mean().to_dict()
    acc += 6600 / month_days + by_cat.get("loan", 1450) / month_days + 1050 / month_days
    acc += np.where(summer, 1900 * 1.35, 1900) / month_days + 1700 / 7
    acc += 13400 * (1 + 0.15 * (season - 1)) / 14 + 3150 / 91.25  # owner draws excluded
    fixed = np.round(acc, 2)

    # Food inventory in cover-portions: Mon/Thu deliveries restore 4 days of expected covers.
    inv = np.zeros(n)
    stock = 4 * mu[0]
    purchases = np.zeros(n)
    for t in range(n):
        if dates[t].day_of_week in (0, 3):
            target = 4.5 * float(np.mean(mu[max(0, t - 14):t + 1]))
            delivered = max(0.0, target - stock)
            purchases[t] = delivered * 6.90 * supplier_index[t]
            stock += delivered
        stock = max(0.0, stock - covers[t] * rng.uniform(0.97, 1.03))
        inv[t] = round(stock)

    # Cash ledger from a declared opening balance.
    opening = 38000.0
    card = revenue * 0.85
    cash_in = revenue * 0.15 + np.concatenate([[0.0], card[:-1]]) - card_fees
    supplier_paid = np.concatenate([np.zeros(7), purchases[:-7]])  # net-7
    bills_paid = bills.groupby("date")["amount"].sum().reindex(dates, fill_value=0.0).to_numpy()
    cash_out = supplier_paid + packaging + bills_paid
    cash_in, cash_out = np.round(cash_in, 2), np.round(cash_out, 2)  # post cents, then sum
    balance = opening + np.cumsum(cash_in - cash_out)

    daily = pd.DataFrame({
        "date": dates, "revenue": revenue, "variable_costs": variable, "fixed_costs": fixed,
        "units_sold": covers, "inventory_on_hand": inv, "avg_ticket": np.round(
            np.divide(revenue, covers, out=np.zeros(n), where=covers > 0), 2),
        "menu_price_index": np.round(price_index, 4), "supplier_cost_index": np.round(supplier_index, 4),
        "purchase_dollars": np.round(purchases, 2), "cash_in": np.round(cash_in, 2),
        "cash_out": np.round(cash_out, 2), "cash_balance": np.round(balance, 2),
        "closed": closed, "holiday": holiday})
    meta = {
        "key": "restaurant", "business_name": "Lone Star Taqueria (fictional)",
        "industry": "restaurant", "city": "Austin", "state": "TX", "currency": "USD",
        "data_origin": "SYNTHETIC", "generator": "decisiondesk.synthetic_v2.make_restaurant",
        "seed": seed, "start": str(dates[0].date()), "end": str(dates[-1].date()),
        "opening_cash": {"amount": opening, "as_of": str(dates[0].date()),
                         "source": "declared in the synthetic scenario (not a bank balance)"},
        "objective": "max_expected_profit",
        "constraints": {"max_price_increase": 0.05, "implementation_budget_usd": 1000.0,
                        "source": "declared in the synthetic business profile"},
        "units_sold_meaning": "covers (guests served)",
        "inventory_meaning": "food inventory in cover-portions (Mon/Thu deliveries)",
        "assumptions": [
            "About 122 covers/day before seasonality; average ticket $21.00 before menu price changes (+6% Fri/Sat)",
            "Menu price steps +4.0% (2024-03-01), +3.5% (2025-02-01), +3.0% (2026-03-01)",
            "Generator demand elasticity -0.7 (hidden from the models and decision engine)",
            "Food cost $6.90 per cover x supplier index (+3.2%/yr drift, +5% beef shock 2025-06-15)",
            "Card fees 2.7% on 85% card share; card sales settle next day",
            ("Rent $6,600 (1st); utilities ~$1,900 (+35% Jun-Sep, 20th); biweekly payroll ~$13,400; "
            "insurance $3,150 quarterly; equipment loan $1,450 (15th); marketing/software $1,050 (5th); "
            "supplies/repairs/services ~$1,700 weekly"),
            "Owner distribution $6,000 on the 28th: a cash outflow, not an operating expense",
            "Closed Thanksgiving and Christmas; SXSW weeks +22%; Cinco de Mayo +60%",
            "Supplier invoices paid net-7; opening cash $38,000"]}
    return BusinessDataset("restaurant", daily, bills, meta)


# ----------------------------------------------------------------------------- retail
def make_retail(seed: int = 2027, end: str = END_DATE, days: int = DAYS) -> BusinessDataset:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=end, periods=days, freq="D")
    n = len(dates)
    years = (dates - dates[0]).days / 365.25
    cal = us_calendar(range(dates[0].year, dates[-1].year + 1))
    holiday = np.array([cal.get(d, "") for d in dates], dtype=object)

    weekday = np.array([0.85, 0.88, 0.90, 0.95, 1.10, 1.45, 0.87])[dates.day_of_week]
    month_season = {1: 0.85, 2: 0.88, 3: 1.0, 4: 1.22, 5: 1.28, 6: 1.18, 7: 1.05, 8: 1.0,
                    9: 0.98, 10: 1.0, 11: 1.08, 12: 1.15}
    season = np.array([month_season[m] for m in dates.month])
    hmult_map = {"thanksgiving": 0.0, "christmas": 0.0, "easter": 0.0, "new_years_day": 0.0,
                 "black_friday": 1.6, "memorial_day": 1.3, "labor_day": 1.3,
                 "independence_day": 0.8, "christmas_eve": 0.7}
    hmult = np.array([hmult_map.get(h, 1.0) for h in holiday])

    # List prices rise each Jan 15; monthly 3-day promotions cut prices 10%.
    list_index = np.ones(n)
    for y in range(dates[0].year + 1, dates[-1].year + 1):
        list_index[dates >= pd.Timestamp(y, 1, 15)] *= 1.025
    promo = np.zeros(n, dtype=bool)
    for period in pd.period_range(dates[0], dates[-1], freq="M"):
        start = pd.Timestamp(period.start_time) + pd.Timedelta(days=int(rng.integers(3, 24)))
        promo |= (dates >= start) & (dates < start + pd.Timedelta(days=3))
    price_index = list_index * np.where(promo, 0.90, 1.0)
    price_effect = price_index ** -1.1  # hidden generator truth

    shock = _ar1(rng, n, 0.5, 0.06)
    mu = 135 * (1 + 0.02 * years) * weekday * season * hmult * price_effect * np.exp(shock)
    demand = np.where(hmult == 0, 0, _negbin(rng, mu, 30))

    wholesale = np.exp(np.repeat(np.cumsum(rng.normal(0.028 / 52, 0.005, n // 7 + 2)), 7)[:n])
    unit_cost = 21.40 * 0.54 * wholesale
    # Weekly review (Mondays) up to 2 weeks of recent sales x 1.15; lead 7 days (10% -> 10 days).
    stock, pipeline = 2.2 * 135 * 7, {}
    sold, inv, stockout = np.zeros(n), np.zeros(n), np.zeros(n, dtype=bool)
    purchases_units, purchases_usd = np.zeros(n), np.zeros(n)
    for t in range(n):
        stock += pipeline.pop(t, 0.0)
        if dates[t].day_of_week == 0:
            recent = sold[max(0, t - 28):t].mean() if t > 0 else mu[0]
            target = 14 * recent * 1.15
            position = stock + sum(pipeline.values())
            q = round(max(0.0, target - position))
            if q > 0:
                lead = 10 if rng.random() < 0.10 else 7
                pipeline[t + lead] = pipeline.get(t + lead, 0.0) + q
                purchases_units[t] = q
                purchases_usd[t] = q * unit_cost[t]
        sold[t] = min(stock, demand[t])
        stockout[t] = demand[t] > 0 and sold[t] < demand[t]
        stock -= sold[t]
        inv[t] = round(stock)

    price = 21.40 * price_index * rng.lognormal(0, 0.03, n)
    revenue = np.round(sold * price, 2)
    card_fees = revenue * 0.9 * 0.025
    variable = np.round(sold * unit_cost + card_fees, 2)

    first_friday = next(d for d in dates if d.day_of_week == 4)
    pay_days = _payroll_days(dates, first_friday)
    winter = dates.month.isin([12, 1, 2])
    rules = [
        ("Fictional Plaza Partners", "rent", lambda d: 7200.0, lambda d: d.day == 1, True),
        ("AEP Ohio (simulated)", "utilities",
         lambda d: 1150 * (1.3 if d.month in (12, 1, 2) else 1.0) * rng.normal(1, 0.05),
         lambda d: d.day == 18, True),
        ("Payroll", "payroll", lambda d: 9000 * rng.normal(1, 0.025), lambda d: d in pay_days,
         True),
        ("Fictional Mutual Insurance", "insurance", lambda d: 2700.0,
         lambda d: d.day == 12 and d.month in (1, 4, 7, 10), True),
        ("POS & software", "discretionary", lambda d: 420.0, lambda d: d.day == 3, True),
        ("Supplies, repairs & services", "other_operating",
         lambda d: 600 * rng.normal(1, 0.08), lambda d: d.day_of_week == 0, True),
        ("Owner distribution", "owner_draw", lambda d: 4500.0, lambda d: d.day == 28, False),
    ]
    bills = _bill_schedule(dates, rules)
    md = dates.days_in_month.to_numpy()
    fixed = np.round(7200 / md + np.where(winter, 1150 * 1.3, 1150) / md + 9000 / 14
                     + 2700 / 91.25 + 420 / md + 600 / 7, 2)  # owner draws excluded

    opening = 55000.0
    card = revenue * 0.9
    cash_in = revenue * 0.1 + np.concatenate([[0.0], card[:-1]]) - card_fees
    supplier_paid = np.concatenate([np.zeros(30), purchases_usd[:-30]])  # net-30
    bills_paid = bills.groupby("date")["amount"].sum().reindex(dates, fill_value=0.0).to_numpy()
    cash_out = supplier_paid + bills_paid
    cash_in, cash_out = np.round(cash_in, 2), np.round(cash_out, 2)  # post cents, then sum
    balance = opening + np.cumsum(cash_in - cash_out)

    daily = pd.DataFrame({
        "date": dates, "revenue": revenue, "variable_costs": variable, "fixed_costs": fixed,
        "units_sold": sold.astype(int), "inventory_on_hand": inv, "stockout_day": stockout,
        "promo": promo, "price_index": np.round(price_index, 4),
        "unit_cost": np.round(unit_cost, 2), "purchase_units": purchases_units,
        "purchase_dollars": np.round(purchases_usd, 2), "cash_in": np.round(cash_in, 2),
        "cash_out": np.round(cash_out, 2), "cash_balance": np.round(balance, 2),
        "closed": hmult == 0, "holiday": holiday})
    meta = {
        "key": "retail", "business_name": "Maple Street Hardware (fictional)",
        "industry": "retail", "city": "Columbus", "state": "OH", "currency": "USD",
        "data_origin": "SYNTHETIC", "generator": "decisiondesk.synthetic_v2.make_retail",
        "seed": seed, "start": str(dates[0].date()), "end": str(dates[-1].date()),
        "opening_cash": {"amount": opening, "as_of": str(dates[0].date()),
                         "source": "declared in the synthetic scenario (not a bank balance)"},
        "objective": "max_expected_profit",
        "constraints": {"storage_capacity_units": 2500.0, "max_stockout_after_arrival": 0.25,
                        "purchase_budget_28d_usd": None,
                        "source": "declared in the synthetic business profile; budget to be "
                                  "stated by the user"},
        "units_sold_meaning": "items sold (capped by stock on hand; lost sales unobserved)",
        "inventory_meaning": "items on hand at end of day",
        "assumptions": [
            "Average item price $21.40; list prices +2.5% each Jan 15; monthly 3-day promotions -10%",
            "Generator demand elasticity -1.1 (hidden from the models and decision engine)",
            "Unit cost 54% of base price x wholesale index (+2.8%/yr drift)",
            "Weekly Monday review up to 2 weeks of recent sales x 1.15; lead time 7 days (10% chance 10)",
            "Closed Thanksgiving, Christmas, Easter, New Year's Day; Black Friday +60%",
            ("Rent $7,200 (1st); utilities ~$1,150 (+30% Dec-Feb, 18th); biweekly payroll ~$9,000; "
            "insurance $2,700 quarterly; POS/software $420 (3rd); supplies/repairs ~$600 weekly"),
            "Owner distribution $4,500 on the 28th: a cash outflow, not an operating expense",
            "Card fees 2.5% on 90% card share; supplier invoices paid net-30; opening cash $55,000"]}
    return BusinessDataset("retail", daily, bills, meta)


DATASETS = {"restaurant": make_restaurant, "retail": make_retail}


def load_business(key: str) -> BusinessDataset:
    if key not in DATASETS:
        raise ValueError(f"unknown demo business {key!r}; choose from {sorted(DATASETS)}")
    return DATASETS[key]()


def export_examples(out_dir: str | Path = "data/examples") -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for key in DATASETS:
        ds = load_business(key)
        stem = f"{key}_v2_SYNTHETIC"
        ds.daily.to_csv(out / f"{stem}.csv", index=False)
        ds.bills.to_csv(out / f"{stem}_bills.csv", index=False)
        (out / f"{stem}_metadata.json").write_text(json.dumps(ds.metadata, indent=2))
        written += [out / f"{stem}.csv", out / f"{stem}_bills.csv", out / f"{stem}_metadata.json"]
    return written


if __name__ == "__main__":
    for p in export_examples():
        print("wrote", p)
