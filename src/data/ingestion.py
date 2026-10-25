"""Strict typed loaders and schema validation for the raw ERP feeds."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import config

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "catalog_master": [
        "sku_id", "parent_sku_id", "asin", "fnsku", "shopify_variant_id",
        "material", "size_width", "color", "hardware", "abc_xyz_tier",
        "z_score", "master_carton_qty", "carton_volume_cbm", "carton_weight_kg",
        "sku_moq_units", "po_aggregate_moq_units", "fob_unit_cost",
        "landed_cost", "retail_price", "parent_stationary_share",
    ],
    "amazon_fba_inventory": [
        "snapshot_date", "sku_id", "fnsku", "fba_available", "fba_fc_transfer",
        "fba_inbound_shipped", "fba_inbound_receiving", "short_term_hdos_30d",
        "long_term_hdos_90d", "inv_age_0_90", "inv_age_91_180",
        "inv_age_181_270", "inv_age_271_365", "inv_age_365_plus",
    ],
    "shopify_sales_report": [
        "order_line_id", "timestamp", "sku_id", "quantity", "gross_price",
        "discount", "fulfillment_status",
    ],
    "warehouse_3pl_stock": [
        "snapshot_date", "sku_id", "on_hand_units", "sealed_master_cartons",
        "loose_pick_units", "allocated_dtc_units",
    ],
    "daily_sales_history": [
        "activity_date", "sku_id", "channel", "opening_inv", "closing_inv",
        "observed_sales", "in_stock_flag", "effective_active_day_weight",
    ],
    "open_purchase_orders": [
        "po_line_id", "po_number", "sku_id", "ordered_cartons", "ordered_units",
        "open_units", "status", "freight_mode", "expected_3pl_eta",
    ],
}


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing required feed: {path}")
    return pd.read_csv(path)


def load_raw_feeds(data_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Load all six feeds and enforce schema / referential integrity."""

    root = Path(data_dir) if data_dir else config.DATA_RAW_DIR
    feeds = {name: _read(root / f"{name}.csv") for name in REQUIRED_COLUMNS}
    validate_feeds(feeds)
    return feeds


def validate_feeds(feeds: dict[str, pd.DataFrame]) -> None:
    for name, required in REQUIRED_COLUMNS.items():
        df = feeds[name]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"{name}: missing columns {missing}")
        if df.empty:
            raise ValueError(f"{name}: empty feed")

    catalog = feeds["catalog_master"]
    if catalog["sku_id"].duplicated().any():
        raise ValueError("catalog_master: duplicate sku_id")
    if catalog["asin"].duplicated().any():
        raise ValueError("catalog_master: duplicate asin")
    if catalog["fnsku"].duplicated().any():
        raise ValueError("catalog_master: duplicate fnsku")
    if catalog["shopify_variant_id"].duplicated().any():
        raise ValueError("catalog_master: duplicate shopify_variant_id")

    valid_skus = set(catalog["sku_id"])
    for name in ("amazon_fba_inventory", "warehouse_3pl_stock", "daily_sales_history",
                 "shopify_sales_report", "open_purchase_orders"):
        orphans = set(feeds[name]["sku_id"]) - valid_skus
        if orphans:
            raise ValueError(f"{name}: orphan sku_id values {sorted(orphans)[:5]}")

    tier_ok = catalog["abc_xyz_tier"].isin(config.Z_SCORES)
    if not tier_ok.all():
        raise ValueError("catalog_master: invalid abc_xyz_tier")

    fba = feeds["amazon_fba_inventory"]
    for col in ("fba_available", "fba_fc_transfer", "fba_inbound_shipped",
                "fba_inbound_receiving", "inv_age_0_90", "inv_age_91_180",
                "inv_age_181_270", "inv_age_271_365", "inv_age_365_plus"):
        if (fba[col] < 0).any():
            raise ValueError(f"amazon_fba_inventory: negative {col}")

    daily = feeds["daily_sales_history"]
    if not daily["in_stock_flag"].isin([0, 1]).all():
        raise ValueError("daily_sales_history: in_stock_flag must be 0/1")
    tau = daily["effective_active_day_weight"]
    if ((tau < 0) | (tau > 1)).any():
        raise ValueError("daily_sales_history: effective_active_day_weight outside [0,1]")
    if (daily["observed_sales"] < 0).any():
        raise ValueError("daily_sales_history: negative observed_sales")

    pos = feeds["open_purchase_orders"]
    if (pos["ordered_units"] <= 0).any() or (pos["open_units"] < 0).any():
        raise ValueError("open_purchase_orders: invalid units")
    if not (pos["ordered_units"] >= pos["open_units"]).all():
        raise ValueError("open_purchase_orders: open_units exceeds ordered_units")

    # Master-carton integrity for factory orders.
    qty = catalog.set_index("sku_id")["master_carton_qty"]
    mapped = pos["sku_id"].map(qty)
    if (pos["ordered_units"] % mapped != 0).any():
        raise ValueError("open_purchase_orders: units are not master-carton multiples")
