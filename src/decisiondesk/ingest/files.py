"""Transaction-level CSV / Excel import (bank, accounting or point-of-sale exports).

Two shapes are supported:
  * one signed amount column (money in positive, money out negative), or
  * separate money-in / money-out columns (e.g. "Credit" / "Debit").
The owner maps columns, then confirms how each category (or payee) counts: revenue, variable cost,
fixed cost, or excluded (transfers, loans, owner draws, taxes). Suggestions are only suggestions;
nothing is classified silently as revenue except what the owner confirms.

Errors name the column and the first bad rows. Re-uploading an overlapping export does not double
count: identical rows get the same entry id (date, amount, description, account and how many
identical rows came before it in the file).
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import ledger as L

MAX_ROWS = 200_000
SUGGEST = {
    "date": r"^(date|posted|posting date|transaction date|txn date|trans date|day)$|date",
    "amount": r"^(amount|net amount|total|value|net)$|amount",
    "money_in": r"credit|deposit|money in|inflow|paid in",
    "money_out": r"debit|withdrawal|money out|outflow|paid out|payment",
    "description": r"description|memo|payee|name|details|narrative|merchant",
    "category": r"category|account|type|class",
    "currency": r"currency|ccy",
}
CATEGORY_HINTS = [  # (pattern, category) - first match wins; the owner confirms every suggestion
    (r"transfer|loan|owner|draw|distribution|capital|tax payment|irs|sales tax|refund from|credit card payment", "excluded"),
    ((r"payroll|salary|wage|rent|lease|insurance|utilit|electric|internet|phone|software|subscription|"
     r"accounting|legal|license"), "fixed_cost"),
    ((r"food|produce|ingredient|supplier|sysco|us foods|restaurant depot|inventory|cogs|cost of goods|packaging|"
     r"merchant fee|card fee|processing"), "variable_cost"),
    (r"sales|deposit|square|stripe|toast|clover|shopify|revenue|income|payout", "revenue"),
]


@dataclass
class FileMapping:
    date: str
    amount: str | None = None
    money_in: str | None = None
    money_out: str | None = None
    description: str | None = None
    category: str | None = None
    currency: str | None = None
    default_currency: str = "USD"
    date_format: str | None = None      # e.g. "%m/%d/%Y"; None = infer, with day-first off

    def validate(self, columns) -> None:
        if not self.date:
            raise ValueError("Choose the column that holds the transaction date.")
        if not self.amount and not (self.money_in or self.money_out):
            raise ValueError("Choose an amount column, or the money-in and money-out columns.")
        for c in (self.date, self.amount, self.money_in, self.money_out, self.description, self.category,
                  self.currency):
            if c and c not in columns:
                raise ValueError(f"Column '{c}' is not in the file.")


@dataclass
class FileImport:
    ledger: pd.DataFrame
    report: L.LedgerReport
    category_values: list[str] = field(default_factory=list)


def read_table(data: bytes | str | Path, filename: str) -> pd.DataFrame:
    name = filename.lower()
    buf = io.BytesIO(data) if isinstance(data, bytes) else data
    try:
        if name.endswith((".xlsx", ".xlsm", ".xls")):
            df = pd.read_excel(buf)
        elif name.endswith((".csv", ".txt")):
            df = pd.read_csv(buf, sep=None, engine="python")
        else:
            raise ValueError("Upload a .csv or .xlsx file.")
    except (UnicodeDecodeError, pd.errors.ParserError) as exc:
        raise ValueError(f"The file could not be read as a table ({type(exc).__name__}). "
                         "Export it again as CSV (comma separated) or Excel.") from None
    df.columns = [str(c).strip() for c in df.columns]
    if df.empty:
        raise ValueError("The file has no rows.")
    if len(df) > MAX_ROWS:
        raise ValueError(f"The file has {len(df):,} rows; the limit is {MAX_ROWS:,}. Split it by year.")
    return df


def suggest_mapping(columns) -> dict[str, str | None]:
    out, used = {}, set()
    for role, rx in SUGGEST.items():
        match = next((c for c in columns if c not in used and re.search(rx, c.lower())), None)
        out[role] = match
        if match:
            used.add(match)
    if out.get("amount") and (out.get("money_in") or out.get("money_out")):
        out["money_in"] = out["money_out"] = None
    return out


def suggest_category(label: str, direction: str) -> str:
    """Suggested treatment for a category/payee label; ``direction`` is 'in' or 'out'."""
    text = str(label).lower()
    for rx, cat in CATEGORY_HINTS:
        if re.search(rx, text):
            if cat == "revenue" and direction == "out":
                continue
            return cat
    return "revenue" if direction == "in" else "uncategorized"


def _parse_money(series: pd.Series, column: str) -> pd.Series:
    def one(x):
        if pd.isna(x) or (isinstance(x, str) and not x.strip()):
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "")
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        try:
            v = float(s)
        except ValueError:
            return "bad"
        return -v if neg else v
    vals = series.map(one)
    bad = vals[vals == "bad"]
    if len(bad):
        rows = ", ".join(str(i + 2) for i in bad.index[:5])
        raise ValueError(f"Column '{column}' has values that are not numbers (file rows {rows}"
                         f"{' …' if len(bad) > 5 else ''}).")
    return pd.to_numeric(vals, errors="coerce")


def _parse_dates(series: pd.Series, column: str, fmt: str | None) -> pd.Series:
    d = pd.to_datetime(series, format=fmt, errors="coerce")
    bad = d.isna() & series.notna()
    if bad.any():
        rows = ", ".join(str(i + 2) for i in series.index[bad][:5])
        raise ValueError(f"Column '{column}' has dates that could not be read (file rows {rows}). "
                         "Choose the date format explicitly.")
    if getattr(d.dt, "tz", None) is not None:
        d = d.dt.tz_localize(None)
    return d.dt.normalize()


def signed_amounts(df: pd.DataFrame, m: FileMapping) -> pd.Series:
    """Money in positive, money out negative."""
    if m.amount:
        return _parse_money(df[m.amount], m.amount)
    inflow = _parse_money(df[m.money_in], m.money_in).fillna(0) if m.money_in else 0
    outflow = _parse_money(df[m.money_out], m.money_out).fillna(0) if m.money_out else 0
    return inflow - (outflow.abs() if hasattr(outflow, "abs") else outflow)


def category_labels(df: pd.DataFrame, m: FileMapping) -> pd.Series:
    if m.category:
        return df[m.category].fillna("(blank)").astype(str).str.strip()
    if m.description:
        return df[m.description].fillna("(blank)").astype(str).str.strip()
    return pd.Series("(all rows)", index=df.index)


def import_transactions(df: pd.DataFrame, m: FileMapping, treatment: dict[str, str], *, filename: str,
                        existing: pd.DataFrame | None = None) -> FileImport:
    """Rows -> ledger entries. ``treatment`` maps each category label (direction-aware key
    "<label>|in" or "<label>|out") to revenue / variable_cost / fixed_cost / excluded / uncategorized."""
    m.validate(df.columns)
    report = L.LedgerReport(source=f"file:{Path(filename).name}")
    dates = _parse_dates(df[m.date], m.date, m.date_format)
    amounts = signed_amounts(df, m)
    labels = category_labels(df, m)
    currency = (df[m.currency].fillna(m.default_currency).astype(str).str.upper().str.strip()
                if m.currency else pd.Series(m.default_currency, index=df.index))
    seen: dict[str, int] = {}
    entries = []
    for i in df.index:
        report.n_records_read += 1
        if pd.isna(dates[i]) or pd.isna(amounts[i]):
            report.skip("row without a date or amount")
            continue
        amt = float(amounts[i])
        if amt == 0:
            report.skip("zero amount")
            continue
        direction = "in" if amt > 0 else "out"
        cat = treatment.get(f"{labels[i]}|{direction}", "uncategorized")
        desc = str(df.at[i, m.description]) if m.description and pd.notna(df.at[i, m.description]) else None
        key = f"{dates[i].date()}|{amt:.2f}|{desc}|{labels[i]}"
        n = seen.get(key, 0)
        seen[key] = n + 1
        rid = hashlib.sha1(f"{key}|{n}".encode()).hexdigest()[:16]
        value = amt if cat == "revenue" else -amt  # revenue: money in; costs: money out as positive
        entries.append(L.entry(report.source.split(":", 1)[0], "row", rid, 0, txn_date=dates[i], amount=value,
                               currency=currency[i], category=cat, account_name=labels[i] if m.category else None,
                               description=desc, data_origin=f"uploaded file {Path(filename).name}"))
    led = L.merge(existing, L.to_frame(entries), report)
    report.n_entries = len(led)
    return FileImport(led, report, sorted(labels.unique().tolist()))


def label_directions(df: pd.DataFrame, m: FileMapping) -> pd.DataFrame:
    """One row per (label, direction) with row count and total, for the owner to classify."""
    amounts = signed_amounts(df, m)
    labels = category_labels(df, m)
    t = pd.DataFrame({"label": labels, "direction": amounts.map(lambda a: "in" if a > 0 else "out"),
                      "amount": amounts.abs()}).dropna()
    g = t.groupby(["label", "direction"]).agg(rows=("amount", "size"), total=("amount", "sum")).reset_index()
    g["suggested"] = [suggest_category(lb, d) for lb, d in zip(g["label"], g["direction"])]
    return g.sort_values("total", ascending=False).reset_index(drop=True)
