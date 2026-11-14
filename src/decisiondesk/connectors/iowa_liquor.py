"""Iowa Liquor Sales (Iowa Data Hub dataset 1051): REAL store-level PURCHASE records.

Verified 2026-11-14 at https://data.iowa.gov/catalog/dataset/1051:
  * Title "Iowa Liquor Sales, January 2012 - Current"; publisher Iowa Department of Revenue,
    Alcohol & Tax Operations Division; licence CC BY 4.0; updated monthly; 35,287,731 rows;
    coverage 2012-01-03 .. 2026-10-30 (record date column ``ordered_on``).
  * Each row is one invoice line: a Class E licensee (grocery, liquor or convenience store)
    BUYING spirits from the state. ``sales_dollars`` is the amount the store paid, i.e. the
    store's procurement spend, NOT its customer sales. There is no inventory or demand column.
  * Access: the Data Hub offers whole-dataset downloads (CSV/JSON, possibly multiple files) through
    its Download dialog; the legacy Socrata query API (resource/m3tr-qhgy) returns 404. There is
    no documented filtered query API, so this module reads the official export from disk.
  * Columns (from the dataset's published transform SQL): invoice_id, ordered_on, store_no,
    store_name, store_address, store_city, store_zip_code, county_fips_code, county_name,
    category_code, category_name, vendor_number, vendor_name, item_no, im_desc, pack,
    bottle_volume_ml, state_bottle_cost, state_bottle_retail, sales_bottles, sales_dollars,
    sales_liters, sales_gallons.
Attribution: "Iowa Department of Revenue, Alcohol & Tax Operations Division. Iowa Liquor Sales,
January 2012 - Current. Iowa Data Hub dataset 1051. CC BY 4.0."
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from .base import DataProvenance

COLUMNS = ("invoice_id", "ordered_on", "store_no", "store_name", "store_address", "store_city",
           "store_zip_code", "county_fips_code", "county_name", "category_code", "category_name",
           "vendor_number", "vendor_name", "item_no", "im_desc", "pack", "bottle_volume_ml",
           "state_bottle_cost", "state_bottle_retail", "sales_bottles", "sales_dollars",
           "sales_liters", "sales_gallons")
NEEDED = ("invoice_id", "ordered_on", "store_no", "store_name", "store_address", "store_city",
          "sales_bottles", "sales_dollars", "sales_liters")
ATTRIBUTION = ("Iowa Department of Revenue, Alcohol & Tax Operations Division. Iowa Liquor Sales, "
               "January 2012 - Current. Iowa Data Hub dataset 1051. CC BY 4.0.")
DEFAULT_DIR = Path("data/private/iowa_liquor")


def export_files(directory: str | Path = DEFAULT_DIR) -> list[Path]:
    d = Path(directory)
    return sorted([*d.glob("*.csv"), *d.glob("*.csv.gz")]) if d.exists() else []


def _chunks(files: Iterable[Path], chunksize: int):
    for f in files:
        header = pd.read_csv(f, nrows=0).columns
        missing = [c for c in NEEDED if c not in header]
        if missing:
            raise ValueError(f"{f.name} is not the official dataset-1051 export: missing {missing}")
        yield from pd.read_csv(f, usecols=list(NEEDED), chunksize=chunksize,
                               dtype={"store_no": str, "invoice_id": str})


def scan_stores(files: list[Path], chunksize: int = 1_000_000) -> pd.DataFrame:
    """Per-store summary used to choose a suitable store (history length, regularity, identity)."""
    parts = []
    for chunk in _chunks(files, chunksize):
        chunk["ordered_on"] = pd.to_datetime(chunk["ordered_on"])
        g = chunk.groupby("store_no")
        parts.append(pd.DataFrame({
            "first": g["ordered_on"].min(), "last": g["ordered_on"].max(),
            "invoice_days": g["ordered_on"].nunique(), "dollars": g["sales_dollars"].sum(),
            "name": g["store_name"].last(), "city": g["store_city"].last()}))
    if not parts:
        raise ValueError("no export files found")
    allp = pd.concat(parts)
    summary = allp.groupby(level=0).agg(first=("first", "min"), last=("last", "max"),
                                        invoice_days=("invoice_days", "sum"),
                                        dollars=("dollars", "sum"), name=("name", "last"),
                                        city=("city", "last"))
    summary["span_days"] = (summary["last"] - summary["first"]).dt.days + 1
    summary["invoice_day_share"] = summary["invoice_days"] / summary["span_days"]
    return summary.sort_values(["span_days", "invoice_days"], ascending=False)


@dataclass
class StoreReport:
    store_no: str
    names: list[str]
    addresses: list[str]
    cities: list[str]
    first_date: str
    last_date: str
    span_days: int
    invoice_days: int
    zero_purchase_days: int
    longest_zero_run_days: int
    total_purchase_dollars: float
    dataset_last_date: str
    suitable: bool
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def store_daily(files: list[Path], store_no: str, *, chunksize: int = 1_000_000,
                min_years: float = 3.0, max_zero_run: int = 35
                ) -> tuple[pd.DataFrame, StoreReport, DataProvenance]:
    """Daily purchase series for one store with an identity and suitability report.

    Days without any invoice are true zero-purchase days for a store that is active (the dataset
    lists every purchase); they are filled with 0 and flagged ``no_invoice``.
    """
    rows, dataset_last = [], None
    for chunk in _chunks(files, chunksize):
        chunk["ordered_on"] = pd.to_datetime(chunk["ordered_on"])
        last = chunk["ordered_on"].max()
        dataset_last = last if dataset_last is None else max(dataset_last, last)
        rows.append(chunk[chunk["store_no"] == str(store_no)])
    data = pd.concat(rows) if rows else pd.DataFrame(columns=list(NEEDED))
    if data.empty:
        raise ValueError(f"store {store_no} not found in the export")
    if data["invoice_id"].duplicated().any():
        data = data.drop_duplicates("invoice_id")  # invoice_id is the dataset's unique key
    daily = data.groupby(data["ordered_on"].dt.normalize()).agg(
        purchase_dollars=("sales_dollars", "sum"), purchase_bottles=("sales_bottles", "sum"),
        purchase_liters=("sales_liters", "sum"), invoice_lines=("invoice_id", "count"))
    full = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    daily = daily.reindex(full)
    daily["no_invoice"] = daily["purchase_dollars"].isna()
    daily = daily.fillna({"purchase_dollars": 0.0, "purchase_bottles": 0, "purchase_liters": 0.0,
                          "invoice_lines": 0}).rename_axis("date").reset_index()
    runs = daily["no_invoice"].astype(int).groupby((~daily["no_invoice"]).cumsum()).sum()
    longest = int(runs.max()) if len(runs) else 0
    span = len(daily)
    names = sorted(data["store_name"].dropna().unique().tolist())
    addresses = sorted(data["store_address"].dropna().astype(str).unique().tolist())
    cities = sorted(data["store_city"].dropna().astype(str).unique().tolist())
    reasons = []
    if span < min_years * 365:
        reasons.append(f"history {span} days < {min_years:g} years")
    if longest > max_zero_run:
        reasons.append(f"longest run without purchases {longest} days > {max_zero_run} "
                       "(possible closure or licence gap)")
    if len(addresses) > 1 or len(cities) > 1:
        reasons.append(f"identity changed: {len(addresses)} addresses / {len(cities)} cities")
    if dataset_last is not None and daily["date"].max() < dataset_last - pd.Timedelta(days=60):
        reasons.append("store has no purchases in the last 60 days of the dataset (inactive)")
    report = StoreReport(
        str(store_no), names, addresses, cities, str(daily["date"].min().date()),
        str(daily["date"].max().date()), span, int((~daily["no_invoice"]).sum()),
        int(daily["no_invoice"].sum()), longest, float(daily["purchase_dollars"].sum()),
        str(dataset_last.date()) if dataset_last is not None else "", not reasons, reasons)
    prov = DataProvenance(
        provider="Iowa Data Hub (Iowa Department of Revenue)", dataset_id=f"1051:store:{store_no}",
        source_url="https://data.iowa.gov/catalog/dataset/1051", units="USD paid to the state",
        frequency="daily (aggregated from invoice lines)", retrieved_at_utc="local export",
        publication_date_column=None,
        notes="Store PURCHASES (procurement), not customer sales; no inventory or demand data.",
        vintage_mode="latest", restrictions=ATTRIBUTION,
        data_origin="REAL_INDIVIDUAL_STORE_PURCHASES")
    return daily, report, prov
