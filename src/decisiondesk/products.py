"""Product-level layer for the SYNTHETIC Lone Star Taqueria (Milestone 5).

Five tracked product groups, a recipe (bill of materials, BOM) over five ingredient groups and an
ingredient stock ledger, generated deterministically FROM the existing daily dataset
(``synthetic_v2.make_restaurant``), which is left byte-for-byte unchanged. Nothing here is a real
business.

Generation (seed ``PRODUCT_SEED``; same output on every run):
  1. Guests (``units_sold`` = covers) order main dishes and drinks. Main dishes per guest, the
     product mix (weekday, season, trend, events such as Taco Tuesday or Super Bowl nachos) and
     drinks per guest are drawn around documented rates. These are the ATTEMPTED orders, i.e.
     customer demand.
  2. Ingredients arrive Monday and Thursday mornings under an order-up-to rule (``plan_order``):
     enough for the days until the next delivery at recent same-weekday usage x 1.35. About 5% of
     deliveries are short-shipped for one ingredient; the missing quantity arrives the next
     morning (emergency restock).
  3. Each day, if an ingredient cannot cover the attempted orders, every product that needs it is
     rationed to the share the stock allows: ``sold <= demand`` and stock never goes negative.
     The POS records the time the item sold out; demand that day is NOT recorded.
  4. Guests who hit a sold-out item ordered from the rest of the menu, so the day's revenue (from
     the existing dataset) is unchanged and the substitution shows up in "other items".

Reconciliation identities (tested in tests/test_products.py):
  revenue_t     = sum_i price_i,t * sold_i,t + other_items_revenue_t,         other >= 0
  food_cost_t   = variable_costs_t - packaging_t - card_fees_t
                = sum_i unit_cost_i,t * sold_i,t + other_food_cost_t,         other >= 0
  closing_k,t   = opening_k,t + delivered_k,t - used_k,t >= 0,   used_k,t = sum_i BOM_ik sold_i,t

Observed vs latent. ``demand`` (attempted orders) is generator truth and is NOT observable in a
real POS; it is kept to show censoring. Forecasts use ``demand_est``: sales on normal days and, on
sell-out days, sales divided by the share of daily demand that normally arrives before the
recorded sell-out time (``INTRADAY`` curve, an assumption). It uses only that day's observable
records, so it adds no look-ahead to forecasts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
import pandas as pd

from .synthetic_v2 import BusinessDataset, load_business, restaurant_cost_components

PRODUCT_SEED = 5150
SAFETY = 1.35
SHORT_SHIP_PROB = 0.05


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    base_price: float        # menu price before the dataset's menu price steps
    description: str


@dataclass(frozen=True)
class Ingredient:
    ingredient_id: str
    name: str
    unit: str
    base_cost: float         # USD per unit before the dataset's supplier cost index


PRODUCTS: tuple[Product, ...] = (
    Product("tacos", "Tacos", 11.50, "Plate of three street tacos"),
    Product("burritos", "Burritos", 11.95, "Burrito with rice and beans"),
    Product("quesadillas", "Quesadillas", 10.40, "Quesadilla with salsa"),
    Product("nachos", "Nachos", 9.70, "Loaded nachos (chips made in-house from tortillas)"),
    Product("drinks", "Drinks", 3.15, "Aguas frescas and sodas"),
)
INGREDIENTS: tuple[Ingredient, ...] = (
    Ingredient("tortillas", "Tortillas", "each", 0.18),
    Ingredient("protein", "Meat & protein", "lb", 6.80),
    Ingredient("cheese", "Cheese", "lb", 5.90),
    Ingredient("produce", "Produce, rice & beans", "lb", 2.60),
    Ingredient("beverages", "Beverage servings", "serving", 1.05),
)
PRODUCT_IDS = tuple(p.product_id for p in PRODUCTS)
INGREDIENT_IDS = tuple(k.ingredient_id for k in INGREDIENTS)
MAINS = ("tacos", "burritos", "quesadillas", "nachos")

# Bill of materials: ingredient units per product unit (rows: PRODUCTS, columns: INGREDIENTS).
BOM = np.array([
    #  tortillas protein cheese produce beverages
    [3.0, 0.30, 0.04, 0.15, 0.0],   # tacos (3)
    [1.0, 0.32, 0.08, 0.40, 0.0],   # burritos
    [2.0, 0.14, 0.22, 0.08, 0.0],   # quesadillas
    [4.0, 0.16, 0.18, 0.22, 0.0],   # nachos (chips = 4 tortillas)
    [0.0, 0.00, 0.00, 0.00, 1.0],   # drinks
])

MAINS_PER_GUEST = 1.30
DRINKS_PER_GUEST = 1.10
BASE_SHARE = {"tacos": 0.36, "burritos": 0.30, "quesadillas": 0.18, "nachos": 0.16}
PACKAGING_PER_GUEST = 0.55          # from synthetic_v2.make_restaurant
CARD_FEE_RATE = 0.85 * 0.027        # card share x fee, from synthetic_v2.make_restaurant
DELIVERY_WEEKDAYS = (0, 3)          # Monday, Thursday (as in the existing dataset)

# Share of a day's demand that arrives by each 15-minute mark, 11:00-22:00 (lunch peak 12:30,
# dinner peak 19:00). An ASSUMPTION used to estimate demand on sell-out days.
_GRID = np.arange(11 * 60, 22 * 60 + 1, 15)
_DENS = (0.42 * np.exp(-0.5 * ((_GRID - 12.5 * 60) / 50) ** 2)
         + 0.58 * np.exp(-0.5 * ((_GRID - 19 * 60) / 70) ** 2))
INTRADAY = np.concatenate([[0.0], np.cumsum(_DENS[1:])]) / np.sum(_DENS[1:])


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def unit_costs(supplier_index: np.ndarray | float) -> np.ndarray:
    """USD cost per product unit (ingredients only), products x len(index)."""
    base = BOM @ np.array([k.base_cost for k in INGREDIENTS])
    return np.multiply.outer(base, np.atleast_1d(np.asarray(supplier_index, dtype=float)))


def menu_prices(price_index: np.ndarray | float) -> np.ndarray:
    """Menu prices rounded to the quarter dollar, products x len(index)."""
    base = np.array([p.base_price for p in PRODUCTS])
    return np.round(np.multiply.outer(base, np.atleast_1d(price_index)) * 4) / 4


def plan_order(stock: np.ndarray, expected_usage: np.ndarray) -> np.ndarray:
    """Order-up-to rule shared by the history generator and forward scenarios.

    ``expected_usage``: ingredient usage expected on each day until the next delivery
    (days x ingredients). Target = SAFETY x that usage; order the shortfall."""
    target = SAFETY * np.asarray(expected_usage).sum(axis=0)
    return np.maximum(0.0, target - stock)


def days_until_next_delivery(weekday: int) -> int:
    """Days covered by a delivery made on ``weekday`` (Mon -> Mon..Wed, Thu -> Thu..Sun)."""
    nxt = min((d - weekday) % 7 or 7 for d in DELIVERY_WEEKDAYS)
    return nxt


def ration(demand: np.ndarray, available: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fill rates when shared ingredients run short.

    ``demand``: (..., products) attempted units; ``available``: (..., ingredients).
    Each ingredient covers the share ``f_k = min(1, available_k / required_k)``; a product gets the
    smallest share among the ingredients it needs. Returns (fill per product, f per ingredient).
    Guarantees ``(demand * fill) @ BOM <= available``."""
    required = demand @ BOM
    f = np.where(required > 1e-12, np.minimum(1.0, available / np.maximum(required, 1e-12)), 1.0)
    uses = BOM > 0  # products x ingredients
    fill = np.min(np.where(uses, f[..., None, :], 1.0), axis=-1)
    return fill, f


@dataclass
class ProductHistory:
    sales: pd.DataFrame          # long: date x product
    ledger: pd.DataFrame         # long: date x ingredient
    recon: pd.DataFrame          # one row per day: revenue and food-cost reconciliation
    metadata: dict = field(default_factory=dict)

    def wide(self, column: str) -> pd.DataFrame:
        """date x product frame of a sales column (e.g. 'units_sold', 'demand_est')."""
        w = self.sales.pivot(index="date", columns="product_id", values=column)
        return w[list(PRODUCT_IDS)].reset_index()

    def closing_stock(self) -> pd.Series:
        last = self.ledger[self.ledger["date"] == self.ledger["date"].max()]
        return last.set_index("ingredient_id")["closing"].reindex(list(INGREDIENT_IDS))

    def forecast_frame(self) -> pd.DataFrame:
        """History for the product forecasters: date + demand_est_<product> columns."""
        w = self.wide("demand_est")
        return w.rename(columns={p: f"demand_{p}" for p in PRODUCT_IDS})


def _mix(dates: pd.DatetimeIndex, holiday: np.ndarray, rng) -> np.ndarray:
    """Daily main-dish shares (n x 4), summing to 1."""
    n = len(dates)
    dow = dates.day_of_week.to_numpy()
    month = dates.month.to_numpy()
    years = ((dates - dates[0]).days / 365.25).to_numpy()
    weekend = np.isin(dow, (4, 5, 6))
    football = np.isin(month, (9, 10, 11, 12, 1)) & np.isin(dow, (5, 6))
    mult = {
        "tacos": np.where(dow == 1, 1.25, 1.0) * np.where(holiday == "cinco_de_mayo", 1.30, 1.0),
        "burritos": np.where(dow <= 3, 1.08, 0.92) * np.where(np.isin(month, (11, 12, 1, 2)), 1.06, 1.0)
        * (1.06 ** years),
        "quesadillas": np.where(dow == 6, 1.08, 1.0) * (0.96 ** years),
        "nachos": np.where(dow == 4, 1.20, np.where(weekend, 1.12, 0.92))
        * np.where(football, 1.15, 1.0) * np.where(holiday == "super_bowl_sunday", 2.0, 1.0),
    }
    raw = np.stack([BASE_SHARE[p] * mult[p] * np.exp(_ar1(rng, n, 0.6, 0.03)) for p in MAINS], 1)
    return raw / raw.sum(axis=1, keepdims=True)


def _ar1(rng, n, phi, sd):
    e = rng.normal(0, sd, n)
    x = np.zeros(n)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    return x


def build_product_history(ds: BusinessDataset, seed: int = PRODUCT_SEED) -> ProductHistory:
    if ds.metadata.get("key") != "restaurant":
        raise ValueError("the product layer is defined for the demo restaurant only")
    rng = np.random.default_rng(seed)
    d = ds.daily.reset_index(drop=True)
    dates = pd.DatetimeIndex(d["date"])
    n, P, K = len(d), len(PRODUCTS), len(INGREDIENTS)
    covers = d["units_sold"].to_numpy(int)
    closed = d["closed"].to_numpy(bool)
    holiday = d["holiday"].to_numpy(object)
    dow = dates.day_of_week.to_numpy()
    doy = dates.day_of_year.to_numpy()
    price = menu_prices(d["menu_price_index"].to_numpy())            # P x n
    ucost = unit_costs(d["supplier_cost_index"].to_numpy())          # P x n
    comp = restaurant_cost_components(ds)
    food = comp["food"].to_numpy()

    # 1. Attempted orders (customer demand).
    mains_rate = MAINS_PER_GUEST * np.where(np.isin(dow, (4, 5)), 1.03, 1.0) * rng.lognormal(0, 0.015, n)
    total_mains = np.round(covers * mains_rate).astype(int)
    shares = _mix(dates, holiday, rng)
    demand = np.zeros((n, P), dtype=int)
    for t in range(n):
        if total_mains[t] > 0:
            demand[t, :4] = rng.multinomial(total_mains[t], shares[t])
    drink_season = 1 + 0.12 * np.sin(2 * np.pi * (doy - 105) / 365.25)
    drinks = covers * DRINKS_PER_GUEST * drink_season * np.where(holiday == "cinco_de_mayo", 1.25, 1.0)
    demand[:, 4] = np.round(drinks * rng.lognormal(0, 0.02, n)).astype(int)
    demand[closed] = 0
    # Keep tracked products within 97% of the day's revenue and food cost (the rest of the menu
    # always exists). Counted and reported; expected to be zero with these rates.
    adjustments = 0
    for t in range(n):
        for _ in range(50):
            rev = float(price[:, t] @ demand[t])
            cost = float(ucost[:, t] @ demand[t])
            if rev <= 0.97 * d["revenue"].iat[t] + 1e-9 and cost <= 0.97 * food[t] + 1e-9:
                break
            demand[t, int(np.argmax(demand[t]))] -= 1
            adjustments += 1

    # 2-3. Ingredient ledger with Mon/Thu deliveries and rationing.
    unit_cost_k = np.array([k.base_cost for k in INGREDIENTS])
    idx = d["supplier_cost_index"].to_numpy()
    opening = np.zeros((n, K))
    delivered = np.zeros((n, K))
    used = np.zeros((n, K))
    closing = np.zeros((n, K))
    short = np.zeros((n, K), dtype=bool)
    sold = np.zeros((n, P), dtype=int)
    demand_est = np.zeros((n, P))
    sellout = np.full((n, P), "", dtype=object)
    usage_hist = demand[:28].mean(axis=0) @ BOM  # warm start: average daily usage
    stock = usage_hist * 4.0
    emergency = np.zeros(K)
    for t in range(n):
        opening[t] = stock
        if emergency.any():  # yesterday's short shipment is made up this morning
            delivered[t] += np.round(emergency, 2)
            stock = stock + np.round(emergency, 2)
            emergency = np.zeros(K)
        if dow[t] in DELIVERY_WEEKDAYS:
            # The owner plans from ESTIMATED demand (sales, corrected on sell-out days), never from
            # censored sales alone, which would shrink orders after every shortage.
            span = days_until_next_delivery(int(dow[t]))
            exp_use = []
            for j in range(span):
                same = list(range(t + j - 7, -1, -7))[:4]  # same weekday, strictly past days
                exp_use.append((demand_est[same].mean(axis=0) @ BOM) if same else usage_hist)
            q = plan_order(stock, np.array(exp_use))
            if rng.random() < SHORT_SHIP_PROB:
                k = int(rng.integers(0, K))
                ordered = q[k]
                q[k] *= rng.uniform(0.35, 0.70)
                emergency[k] = ordered - q[k]
                short[t, k] = True
            delivered[t] += np.round(q, 2)
            stock = stock + np.round(q, 2)
        fill, _ = ration(demand[t].astype(float), stock)
        sold[t] = np.floor(demand[t] * fill + 1e-9).astype(int)
        used[t] = sold[t] @ BOM
        stock = np.maximum(stock - used[t], 0.0)
        closing[t] = stock
        # Observable demand estimate for the day (sell-out time recorded by the POS).
        demand_est[t] = sold[t]
        for i in np.nonzero(sold[t] < demand[t])[0]:
            g = min(int(np.searchsorted(INTRADAY, sold[t, i] / demand[t, i])), len(_GRID) - 1)
            sellout[t, i] = _clock(int(_GRID[g]))
            if INTRADAY[g] > 0:
                demand_est[t, i] = sold[t, i] / INTRADAY[g]
            else:  # sold out at opening: fall back to past same-weekday estimates (causal)
                past = list(range(t - 7, -1, -7))[:4]
                demand_est[t, i] = float(np.mean(demand_est[past, i])) if past else sold[t, i]

    lost = demand - sold
    stockout = lost > 0
    demand_est = np.round(demand_est, 1)

    # 4. Money.
    revenue_p = price.T * sold                     # n x P
    cogs_p = ucost.T * sold
    recon = pd.DataFrame({
        "date": dates, "covers": covers, "revenue": d["revenue"].to_numpy(),
        "tracked_revenue": revenue_p.sum(axis=1).round(2),
        "food_cost": food.round(2), "tracked_cogs": cogs_p.sum(axis=1).round(2),
        "packaging": comp["packaging"].to_numpy(), "card_fees": comp["card_fees"].to_numpy(),
        "mains_sold": sold[:, :4].sum(axis=1), "closed": closed})
    recon["other_items_revenue"] = (recon["revenue"] - recon["tracked_revenue"]).round(2)
    recon["other_food_cost"] = (recon["food_cost"] - recon["tracked_cogs"]).round(2)

    rows = []
    for i, p in enumerate(PRODUCTS):
        rows.append(pd.DataFrame({
            "date": dates, "product_id": p.product_id, "product": p.name,
            "price": price[i], "unit_cost": np.round(ucost[i], 4), "demand": demand[:, i],
            "units_sold": sold[:, i], "lost_units": lost[:, i], "stockout": stockout[:, i],
            "sellout_time": sellout[:, i], "demand_est": demand_est[:, i],
            "revenue": np.round(revenue_p[:, i], 2), "cogs": np.round(cogs_p[:, i], 2)}))
    sales = pd.concat(rows, ignore_index=True)
    sales["gross_profit"] = (sales["revenue"] - sales["cogs"]).round(2)

    lrows = []
    for k, ing in enumerate(INGREDIENTS):
        lrows.append(pd.DataFrame({
            "date": dates, "ingredient_id": ing.ingredient_id, "ingredient": ing.name,
            "unit": ing.unit, "unit_cost": np.round(unit_cost_k[k] * idx, 4),
            "opening": opening[:, k].round(3), "delivered": delivered[:, k].round(3),
            "used": used[:, k].round(3), "closing": closing[:, k].round(3),
            "short_shipment": short[:, k],
            "purchase_usd": np.round(delivered[:, k] * unit_cost_k[k] * idx, 2)}))
    ledger = pd.concat(lrows, ignore_index=True)

    meta = {
        "data_origin": "SYNTHETIC", "seed": seed,
        "generator": "decisiondesk.products.build_product_history",
        "derived_from": ds.metadata.get("generator"),
        "mix_adjustments": adjustments,
        "stockout_product_days": int(stockout.sum()),
        "assumptions": [
            (f"{MAINS_PER_GUEST} main dishes and {DRINKS_PER_GUEST} drinks per guest on average "
            "(main dishes +3% Fri/Sat; drinks +/-12% summer seasonality)"),
            ("Main-dish mix: tacos 36%, burritos 30%, quesadillas 18%, nachos 16% before weekday, "
            "season, trend and event effects (Taco Tuesday +25%, Super Bowl nachos x2, burritos "
            "+6%/yr, quesadillas -4%/yr)"),
            "Menu prices follow the dataset's menu price index, rounded to $0.25",
            "Ingredient costs follow the dataset's supplier cost index",
            ("Recipes (BOM) per unit: tacos 3 tortillas, 0.30 lb protein, 0.04 lb cheese, 0.15 lb "
            "produce; burrito 1 / 0.32 / 0.08 / 0.40; quesadilla 2 / 0.14 / 0.22 / 0.08; nachos "
            "4 / 0.16 / 0.18 / 0.22; drink 1 beverage serving"),
            ("Deliveries Mon/Thu, order-up-to 1.35 x recent same-weekday estimated usage until "
            "the next delivery; 5% of deliveries short-shipped (35-70%) for one ingredient, "
            "made up by an emergency restock the next morning"),
            ("No spoilage is modeled; ingredient purchases for the five products are a planning "
            "ledger and are not posted separately to the cash ledger (the dataset's total food "
            "purchases are)"),
            "Guests who hit a sold-out item ordered other menu items (daily revenue unchanged)",
            ("Sell-out demand estimate uses an assumed intraday demand curve (lunch 12:30, dinner "
            "19:00)")],
    }
    return ProductHistory(sales, ledger, recon, meta)


@lru_cache(maxsize=2)
def restaurant_products(seed: int = PRODUCT_SEED) -> ProductHistory:
    """The demo restaurant's product layer (cached; deterministic)."""
    return build_product_history(load_business("restaurant"), seed)
