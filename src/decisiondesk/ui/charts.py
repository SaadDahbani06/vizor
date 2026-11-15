"""Altair chart builders for the dashboard. Every chart has hover tooltips; line charts have a
crosshair; series are labelled by a legend (and baseline series are dashed and grey)."""
from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd

from .theme import BAD, BASELINE, CARD, COBALT, GOOD, GRID, INK, MUTED, ORANGE, PRODUCT_COLORS, WARN

FONT = "Inter"


def finish(chart, height: int = 280):
    return (chart.properties(height=height, background=CARD)
            .configure_view(stroke=None)
            .configure_axis(labelFont=FONT, titleFont=FONT, labelColor=MUTED, titleColor=MUTED,
                            gridColor=GRID, domainColor="#141414", tickColor="#141414", labelFontSize=11,
                            titleFontSize=11, titleFontWeight=600)
            .configure_legend(labelFont=FONT, titleFont=FONT, orient="top", title=None,
                              labelFontSize=12, symbolStrokeWidth=3)
            .configure_title(font="Inter", fontSize=14, anchor="start", color=INK,
                             fontWeight=600))


def _money_axis(title=None):
    return alt.Axis(format="$,.0f", title=title)


def line_hover(wide: pd.DataFrame, series: dict[str, str], *, colors: dict[str, str],
               dashed: tuple[str, ...] = (), y_format: str = "$,.0f", y_title: str | None = None,
               height: int = 280, title: str | None = None, zero_rule: bool = False):
    """Multi-series line chart with a crosshair: ``series`` maps legend label -> column."""
    wide = wide.copy()
    long = wide.melt(id_vars=["date"], value_vars=list(series.values()), var_name="col", value_name="value")
    inv = {v: k for k, v in series.items()}
    long["series"] = long["col"].map(inv)
    domain = list(series)
    color = alt.Color("series:N", scale=alt.Scale(domain=domain, range=[colors[s] for s in domain]),
                      legend=alt.Legend(title=None))
    dash = alt.StrokeDash("series:N", scale=alt.Scale(domain=domain, range=[[6, 4] if s in dashed else [1, 0] for s in domain]),
                          legend=None)
    base = alt.Chart(long).encode(x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %d")))
    lines = base.mark_line(strokeWidth=2.2).encode(
        y=alt.Y("value:Q", title=y_title, axis=alt.Axis(format=y_format)), color=color, strokeDash=dash)
    near = alt.selection_point(nearest=True, on="pointerover", fields=["date"], empty=False)
    tips = [alt.Tooltip("date:T", title="Date", format="%a %b %d, %Y")] + [
        alt.Tooltip(f"{c}:Q", title=lab, format=y_format) for lab, c in series.items()]
    sel = alt.Chart(wide).mark_rule(color=INK, strokeWidth=1).encode(
        x="date:T", opacity=alt.condition(near, alt.value(0.45), alt.value(0)), tooltip=tips
    ).add_params(near)
    pts = lines.mark_point(filled=True, size=55, stroke=CARD, strokeWidth=1.5).encode(
        opacity=alt.condition(near, alt.value(1), alt.value(0)))
    layers = [lines, sel, pts]
    if zero_rule:
        layers.insert(0, alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=INK, strokeWidth=1).encode(y="y:Q"))
    ch = alt.layer(*layers)
    if title:
        ch = ch.properties(title=title)
    return finish(ch, height)


def sparkline(df: pd.DataFrame, col: str, color: str = COBALT, background: str = CARD):
    ch = alt.Chart(df).mark_area(line={"color": color, "strokeWidth": 2}, color=color, opacity=0.12).encode(
        x=alt.X("date:T", axis=None), y=alt.Y(f"{col}:Q", axis=None, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("date:T", format="%b %d"), alt.Tooltip(f"{col}:Q", format="$,.0f")])
    return ch.properties(height=48, background=background).configure_view(stroke=None)


def product_revenue(df: pd.DataFrame, height: int = 280):
    """Bars = revenue; label = units sold."""
    order = df.sort_values("revenue", ascending=False)["product"].tolist()
    base = alt.Chart(df).encode(y=alt.Y("product:N", sort=order, title=None,
                                        axis=alt.Axis(labelFontWeight=600, labelColor=INK)))
    bars = base.mark_bar(cornerRadiusEnd=3, height=22).encode(
        x=alt.X("revenue:Q", axis=_money_axis(), title=None,
                scale=alt.Scale(domain=[0, float(df["revenue"].max()) * 1.38])),
        color=alt.Color("product:N", scale=alt.Scale(domain=list(PRODUCT_COLORS),
                                                     range=list(PRODUCT_COLORS.values())), legend=None),
        tooltip=[alt.Tooltip("product:N", title="Product"), alt.Tooltip("revenue:Q", format="$,.0f", title="Revenue"),
                 alt.Tooltip("units_sold:Q", format=",", title="Units sold"),
                 alt.Tooltip("share_pct:Q", format=".1f", title="Share of revenue %")])
    text = base.mark_text(align="left", dx=6, font=FONT, fontSize=12, color=INK).encode(
        x="revenue:Q", text=alt.Text("label:N"))
    return finish(alt.layer(bars, text), height)


def expense_bars(df: pd.DataFrame, height: int = 320):
    order = df.sort_values("amount", ascending=False)["category"].tolist()
    base = alt.Chart(df).encode(y=alt.Y("category:N", sort=order, title=None,
                                        axis=alt.Axis(labelLimit=230, labelColor=INK)))
    bars = base.mark_bar(cornerRadiusEnd=3, height=16).encode(
        x=alt.X("amount:Q", axis=_money_axis(), title=None),
        color=alt.Color("type:N", scale=alt.Scale(domain=["variable", "fixed"], range=[COBALT, ORANGE]),
                        legend=alt.Legend(title=None, labelExpr="datum.label == 'variable' ? 'Variable' : 'Fixed'")),
        tooltip=[alt.Tooltip("category:N", title="Category"), alt.Tooltip("amount:Q", format="$,.0f", title="Amount"),
                 alt.Tooltip("share_pct:Q", format=".1f", title="Share %"), alt.Tooltip("type:N", title="Type")])
    text = base.mark_text(align="left", dx=5, font=FONT, fontSize=11, color=MUTED).encode(
        x="amount:Q", text=alt.Text("share_pct:Q", format=".0f"))
    return finish(alt.layer(bars, text.encode(text=alt.Text("pct_label:N"))), height)


def inventory_health(df: pd.DataFrame, height: int = 280):
    """Bars: sellable units from stock on hand (physical). Black tick: forecast demand over the
    same days (predicted). Bar colour = 7-day stock-out risk status."""
    order = df["product"].tolist()
    base = alt.Chart(df).encode(y=alt.Y("product:N", sort=order, title=None,
                                        axis=alt.Axis(labelFontWeight=600, labelColor=INK)))
    bars = base.mark_bar(height=20, cornerRadiusEnd=3).encode(
        x=alt.X("sellable_units:Q", title="units", axis=alt.Axis(format=",.0f")),
        color=alt.Color("status:N", scale=alt.Scale(domain=["OK", "Watch", "At risk"], range=[GOOD, WARN, BAD]),
                        legend=alt.Legend(title="7-day stock-out risk")),
        tooltip=[alt.Tooltip("product:N"), alt.Tooltip("sellable_units:Q", format=",.0f", title="Sellable from stock (units)"),
                 alt.Tooltip("demand_until_delivery:Q", format=",.0f", title="Forecast demand until next delivery"),
                 alt.Tooltip("days_of_supply:Q", format=".1f", title="Days of supply"),
                 alt.Tooltip("risk_pct:Q", format=".0f", title="7-day stock-out risk %")])
    tick = base.mark_tick(color=INK, thickness=3, size=30).encode(
        x="demand_until_delivery:Q",
        tooltip=[alt.Tooltip("demand_until_delivery:Q", format=",.0f", title="Forecast demand until next delivery")])
    return finish(alt.layer(bars, tick), height)


def band_compare(df: pd.DataFrame, *, title: str, height: int = 260, y_format: str = "$,.0f"):
    """Baseline vs scenario daily means with 80% simulation bands.
    Columns: date, base, base_lo, base_hi, scen, scen_lo, scen_hi."""
    rows = []
    for name, m, lo, hi in (("Current plan (baseline)", "base", "base_lo", "base_hi"),
                            ("Scenario", "scen", "scen_lo", "scen_hi")):
        rows.append(pd.DataFrame({"date": df["date"], "series": name, "mean": df[m], "lo": df[lo], "hi": df[hi]}))
    long = pd.concat(rows)
    dom = ["Current plan (baseline)", "Scenario"]
    color = alt.Color("series:N", scale=alt.Scale(domain=dom, range=[BASELINE, COBALT]), legend=alt.Legend(title=None))
    x = alt.X("date:T", title=None, axis=alt.Axis(format="%b %d"))
    band = alt.Chart(long).mark_area(opacity=0.14).encode(x=x, y=alt.Y("lo:Q", axis=alt.Axis(format=y_format), title=None),
                                                          y2="hi:Q", color=color)
    line = alt.Chart(long).mark_line(strokeWidth=2.2).encode(
        x=x, y="mean:Q", color=color,
        strokeDash=alt.StrokeDash("series:N", scale=alt.Scale(domain=dom, range=[[6, 4], [1, 0]]), legend=None))
    near = alt.selection_point(nearest=True, on="pointerover", fields=["date"], empty=False)
    rule = alt.Chart(df).mark_rule(color=INK).encode(
        x="date:T", opacity=alt.condition(near, alt.value(0.45), alt.value(0)),
        tooltip=[alt.Tooltip("date:T", format="%a %b %d"),
                 alt.Tooltip("base:Q", format=y_format, title="Baseline (mean)"),
                 alt.Tooltip("scen:Q", format=y_format, title="Scenario (mean)"),
                 alt.Tooltip("scen_lo:Q", format=y_format, title="Scenario 10th pct"),
                 alt.Tooltip("scen_hi:Q", format=y_format, title="Scenario 90th pct")]).add_params(near)
    return finish(alt.layer(band, line, rule).properties(title=title), height)


def demand_supply(df: pd.DataFrame, *, title: str, height: int = 300):
    """Columns: date, demand, demand_lo, demand_hi, sold, makeable, short (bool)."""
    x = alt.X("date:T", title=None, axis=alt.Axis(format="%b %d"))
    long = pd.concat([
        pd.DataFrame({"date": df["date"], "series": "Demand (forecast)", "v": df["demand"]}),
        pd.DataFrame({"date": df["date"], "series": "Sales (expected)", "v": df["sold"]}),
        pd.DataFrame({"date": df["date"], "series": "Stock allows", "v": df["makeable"]})])
    dom = ["Demand (forecast)", "Sales (expected)", "Stock allows"]
    color = alt.Color("series:N", scale=alt.Scale(domain=dom, range=[COBALT, INK, ORANGE]), legend=alt.Legend(title=None))
    band = alt.Chart(df).mark_area(color=COBALT, opacity=0.12).encode(x=x, y=alt.Y("demand_lo:Q", title="units per day"), y2="demand_hi:Q")
    lines = alt.Chart(long).mark_line(strokeWidth=2.2).encode(
        x=x, y="v:Q", color=color,
        strokeDash=alt.StrokeDash("series:N", scale=alt.Scale(domain=dom, range=[[1, 0], [4, 3], [1, 0]]), legend=None))
    shortages = alt.Chart(df[df["short"]]).mark_point(shape="triangle-down", size=110, filled=True, color=BAD).encode(
        x=x, y="demand:Q", tooltip=[alt.Tooltip("date:T", format="%a %b %d"),
                                    alt.Tooltip("short_share:Q", format=".0%", title="Paths with unmet demand")])
    near = alt.selection_point(nearest=True, on="pointerover", fields=["date"], empty=False)
    rule = alt.Chart(df).mark_rule(color=INK).encode(
        x="date:T", opacity=alt.condition(near, alt.value(0.45), alt.value(0)),
        tooltip=[alt.Tooltip("date:T", format="%a %b %d"), alt.Tooltip("demand:Q", format=",.1f", title="Demand (mean)"),
                 alt.Tooltip("demand_lo:Q", format=",.1f", title="Demand 10th pct"),
                 alt.Tooltip("demand_hi:Q", format=",.1f", title="Demand 90th pct"),
                 alt.Tooltip("sold:Q", format=",.1f", title="Units sold (mean)"),
                 alt.Tooltip("makeable:Q", format=",.0f", title="Stock allows (units)"),
                 alt.Tooltip("short_share:Q", format=".0%", title="Paths with unmet demand")]).add_params(near)
    return finish(alt.layer(band, lines, shortages, rule).properties(title=title), height)


def impact_bars(df: pd.DataFrame, height: int = 260):
    """Columns: product, metric, value (change vs baseline)."""
    dom = ["Revenue change", "Gross profit change"]
    ch = alt.Chart(df).mark_bar(height=11, cornerRadiusEnd=2).encode(
        y=alt.Y("product:N", title=None, sort=list(PRODUCT_COLORS), axis=alt.Axis(labelFontWeight=600, labelColor=INK)),
        yOffset=alt.YOffset("metric:N", sort=dom),
        x=alt.X("value:Q", axis=_money_axis(), title="change vs current plan"),
        color=alt.Color("metric:N", scale=alt.Scale(domain=dom, range=[COBALT, ORANGE]), legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip("product:N"), alt.Tooltip("metric:N"), alt.Tooltip("value:Q", format="$,.0f"),
                 alt.Tooltip("units:Q", format="+,.0f", title="Units sold change")])
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=INK).encode(x="x:Q")
    return finish(alt.layer(ch, zero), height)


def delta_histogram(deltas: np.ndarray, lo: float, hi: float, height: int = 240):
    df = pd.DataFrame({"delta": deltas})
    bars = alt.Chart(df).mark_bar(color=COBALT, opacity=0.85).encode(
        x=alt.X("delta:Q", bin=alt.Bin(maxbins=40), title="profit change vs current plan (USD)", axis=alt.Axis(format="$,.0f")),
        y=alt.Y("count():Q", title="simulated futures"),
        tooltip=[alt.Tooltip("delta:Q", bin=alt.Bin(maxbins=40), format="$,.0f", title="Profit change"),
                 alt.Tooltip("count():Q", title="Simulated futures")])
    marks = pd.DataFrame({"x": [0.0, lo, hi], "what": ["No change", "5th percentile", "95th percentile"]})
    rules = alt.Chart(marks).mark_rule(strokeWidth=2).encode(
        x="x:Q", color=alt.Color("what:N", scale=alt.Scale(domain=["No change", "5th percentile", "95th percentile"],
                                                            range=[INK, ORANGE, ORANGE]), legend=alt.Legend(title=None)),
        strokeDash=alt.StrokeDash("what:N", scale=alt.Scale(domain=["No change", "5th percentile", "95th percentile"],
                                                             range=[[1, 0], [5, 3], [5, 3]]), legend=None),
        tooltip=[alt.Tooltip("what:N"), alt.Tooltip("x:Q", format="$,.0f", title="Value")])
    return finish(alt.layer(bars, rules), height)


def stacked_area(df: pd.DataFrame, height: int = 280):
    """Columns: date, product, revenue (weekly)."""
    dom = [p for p in PRODUCT_COLORS if p in set(df["product"])]
    ch = alt.Chart(df).mark_area(opacity=0.9, line={"color": CARD, "strokeWidth": 0.8}).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %d")),
        y=alt.Y("revenue:Q", stack=True, axis=_money_axis("weekly revenue")),
        color=alt.Color("product:N", scale=alt.Scale(domain=dom, range=[PRODUCT_COLORS[p] for p in dom]),
                        legend=alt.Legend(title=None), sort=dom),
        order=alt.Order("order:Q"),
        tooltip=[alt.Tooltip("date:T", title="Week ending", format="%b %d, %Y"), alt.Tooltip("product:N"),
                 alt.Tooltip("revenue:Q", format="$,.0f", title="Revenue")])
    return finish(ch, height)


def sales_demand_history(df: pd.DataFrame, height: int = 260):
    """Columns: date, units_sold, demand_est, stockout."""
    wide = df.rename(columns={"units_sold": "Units sold (recorded)", "demand_est": "Estimated demand"})
    ch = line_hover(wide[["date", "Units sold (recorded)", "Estimated demand"]],
                    {"Units sold (recorded)": "Units sold (recorded)", "Estimated demand": "Estimated demand"},
                    colors={"Units sold (recorded)": INK, "Estimated demand": COBALT},
                    dashed=("Estimated demand",), y_format=",.0f", height=height)
    return ch


def price_history(df: pd.DataFrame, height: int = 180):
    ch = alt.Chart(df).mark_line(interpolate="step-after", color=INK, strokeWidth=2.2).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %Y")),
        y=alt.Y("price:Q", axis=alt.Axis(format="$.2f"), title=None, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("date:T", format="%b %d, %Y"), alt.Tooltip("price:Q", format="$.2f", title="Menu price"),
                 alt.Tooltip("unit_cost:Q", format="$.2f", title="Ingredient cost")])
    cost = alt.Chart(df).mark_line(color=ORANGE, strokeWidth=2, strokeDash=[5, 3]).encode(
        x="date:T", y="unit_cost:Q", tooltip=[alt.Tooltip("date:T", format="%b %d, %Y"),
                                              alt.Tooltip("unit_cost:Q", format="$.2f", title="Ingredient cost")])
    return finish(alt.layer(ch, cost), height)


def driver_bars(df: pd.DataFrame, height: int = 260):
    """Columns: driver, profit_effect."""
    df = df.assign(direction=np.where(df["profit_effect"] >= 0, "Raised profit", "Lowered profit"))
    ch = alt.Chart(df).mark_bar(height=16, cornerRadiusEnd=2).encode(
        y=alt.Y("driver:N", sort=alt.EncodingSortField("abs_effect", order="descending"), title=None,
                axis=alt.Axis(labelLimit=240, labelColor=INK)),
        x=alt.X("profit_effect:Q", axis=_money_axis(), title="effect on operating profit"),
        color=alt.Color("direction:N", scale=alt.Scale(domain=["Raised profit", "Lowered profit"], range=[GOOD, BAD]),
                        legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip("driver:N"), alt.Tooltip("previous:Q", format="$,.0f", title="Previous period"),
                 alt.Tooltip("current:Q", format="$,.0f", title="This period"),
                 alt.Tooltip("profit_effect:Q", format="$,.0f", title="Effect on profit")])
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=INK).encode(x="x:Q")
    return finish(alt.layer(ch, zero), height)


def ingredient_history(df: pd.DataFrame, height: int = 260):
    """Columns: date, ingredient, days_of_supply."""
    ch = alt.Chart(df).mark_line(strokeWidth=1.8, interpolate="step-after").encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %d")),
        y=alt.Y("days_of_supply:Q", title="days of supply at end of day"),
        color=alt.Color("ingredient:N", legend=alt.Legend(title=None),
                        scale=alt.Scale(range=["#2346D8", "#F26A1B", "#0F8F7A", "#8B3FC7", "#A87800"])),
        tooltip=[alt.Tooltip("date:T", format="%a %b %d"), alt.Tooltip("ingredient:N"),
                 alt.Tooltip("days_of_supply:Q", format=".1f", title="Days of supply"),
                 alt.Tooltip("closing:Q", format=",.1f", title="On hand")])
    return finish(ch, height)


def price_position(df: pd.DataFrame, our_price: float, median: float | None, *, label: str,
                   height: int = 240):
    """Dot plot of verified comparable prices (for our serving) with our price and the peer median.
    Columns: restaurant, comparable_price, concept, verified_date, item, listed_price."""
    order = df.sort_values("comparable_price")["restaurant"].tolist()
    base = alt.Chart(df).encode(y=alt.Y("restaurant:N", sort=order, title=None,
                                        axis=alt.Axis(labelFontWeight=600, labelColor=INK, labelLimit=200)))
    dots = base.mark_circle(size=170, opacity=1, stroke=CARD, strokeWidth=1.5).encode(
        x=alt.X("comparable_price:Q", title=f"price for {label}", axis=alt.Axis(format="$.2f"),
                scale=alt.Scale(zero=False, padding=20)),
        color=alt.Color("concept:N", legend=alt.Legend(title=None),
                        scale=alt.Scale(range=[COBALT, ORANGE, "#0F8F7A", "#8B3FC7"])),
        tooltip=[alt.Tooltip("restaurant:N"), alt.Tooltip("item:N"),
                 alt.Tooltip("listed_price:Q", format="$.2f", title="Listed price"),
                 alt.Tooltip("comparable_price:Q", format="$.2f", title="For your serving"),
                 alt.Tooltip("match_level:N", title="Match"),
                 alt.Tooltip("verified_date:N", title="Verified")])
    marks = [{"x": our_price, "what": "Your price"}] + ([{"x": median, "what": "Peer median"}] if median else [])
    rules = alt.Chart(pd.DataFrame(marks)).mark_rule(strokeWidth=2.5).encode(
        x="x:Q", color=alt.Color("what:N", scale=alt.Scale(domain=["Your price", "Peer median"],
                                                         range=[INK, BASELINE]), legend=alt.Legend(title=None)),
        strokeDash=alt.StrokeDash("what:N", scale=alt.Scale(domain=["Your price", "Peer median"],
                                                             range=[[1, 0], [6, 4]]), legend=None),
        tooltip=[alt.Tooltip("what:N"), alt.Tooltip("x:Q", format="$.2f", title="Price")])
    return finish(alt.layer(dots, rules).resolve_scale(color="independent"), height)


def trend(frame: pd.DataFrame, *, weekly: bool = True, height: int = 280):
    """Sales as a soft area and profit as a line (weekly sums by default), with a hover rule that
    shows both values. ``frame``: date, revenue, profit (daily)."""
    d = frame[["date", "revenue", "profit"]].copy()
    d["date"] = pd.to_datetime(d["date"])
    if weekly:  # 7-day blocks ending on the last data day, so the latest week is complete
        last = d["date"].max()
        d["block"] = ((last - d["date"]).dt.days // 7)
        d = (d.groupby("block").agg(date=("date", "max"), revenue=("revenue", "sum"), profit=("profit", "sum"),
                                    n=("date", "size")).reset_index(drop=True))
        d = d[d["n"] == 7].sort_values("date").drop(columns="n")
    d["label"] = d["date"].dt.strftime("Week to %b %d" if weekly else "%a %b %d")
    base = alt.Chart(d).encode(x=alt.X("date:T", title=None, axis=alt.Axis(format="%b %d", tickCount=6,
                                                                              grid=False, domain=False, ticks=False)))
    area = base.mark_area(line={"color": COBALT, "strokeWidth": 2.5}, interpolate="monotone",
                          color=alt.Gradient(gradient="linear", x1=1, x2=1, y1=1, y2=0,
                                             stops=[alt.GradientStop(color="rgba(35,70,216,0.02)", offset=0),
                                                    alt.GradientStop(color="rgba(35,70,216,0.22)", offset=1)])
                          ).encode(y=alt.Y("revenue:Q", title=None, axis=alt.Axis(format="$,.0f", tickCount=4)))
    prof = base.mark_line(color=ORANGE, strokeWidth=2.5, interpolate="monotone",
                          point=alt.OverlayMarkDef(color=ORANGE, size=28, filled=True)).encode(y="profit:Q")
    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False, clear="pointerout")
    rule = base.mark_rule(color="#C9CBD1", strokeWidth=1).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("label:N", title="Period"), alt.Tooltip("revenue:Q", title="Sales", format="$,.0f"),
                 alt.Tooltip("profit:Q", title="Profit", format="$,.0f")]).add_params(hover)
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#D5D4CF", strokeDash=[3, 3]).encode(y="y:Q")
    return finish(alt.layer(area, prof, zero, rule), height)
