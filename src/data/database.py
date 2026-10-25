"""DuckDB persistence layer: 3NF tables plus analytical views."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from src import config
from src.data.ingestion import load_raw_feeds

TABLES = {
    "catalog_master": "dim_catalog_master",
    "amazon_fba_inventory": "fact_fba_inventory_snapshot",
    "shopify_sales_report": "fact_shopify_orders",
    "warehouse_3pl_stock": "fact_warehouse_3pl_stock",
    "daily_sales_history": "fact_daily_channel_sales",
    "open_purchase_orders": "fact_open_purchase_orders",
}


def build_database(
    db_path: Path | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Materialise the six feeds in DuckDB and create audit views."""

    path = Path(db_path) if db_path else config.DATA_PROCESSED_DIR / "control_tower.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    feeds = load_raw_feeds(data_dir)
    con = duckdb.connect(str(path))
    try:
        for name, table in TABLES.items():
            df = feeds[name]
            con.register("_staging", df)
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _staging")
            con.unregister("_staging")

        con.execute(
            """
            CREATE OR REPLACE VIEW vw_fba_fee_risk AS
            SELECT
                sku_id,
                fba_available,
                weekly_units_sold_7d,
                low_inv_fee_exempt_flag,
                GREATEST(short_term_hdos_30d, long_term_hdos_90d) AS effective_hdos,
                inv_age_181_270,
                inv_age_271_365,
                inv_age_365_plus,
                CASE
                    WHEN low_inv_fee_exempt_flag
                      OR GREATEST(short_term_hdos_30d, long_term_hdos_90d) >= 28
                      OR weekly_units_sold_7d < 20
                      THEN 'EXEMPT_OR_HEALTHY'
                    WHEN GREATEST(short_term_hdos_30d, long_term_hdos_90d) >= 21 THEN 'TIER_1_0.32'
                    WHEN GREATEST(short_term_hdos_30d, long_term_hdos_90d) >= 14 THEN 'TIER_2_0.63'
                    ELSE 'TIER_3_0.89'
                END AS fee_band
            FROM fact_fba_inventory_snapshot
            """
        )
        con.execute(
            """
            CREATE OR REPLACE VIEW vw_daily_omnichannel AS
            SELECT activity_date, sku_id,
                   SUM(observed_sales) AS units_sold,
                   SUM(opening_inv) AS opening_units,
                   SUM(closing_inv) AS closing_units,
                   MIN(in_stock_flag) AS both_channels_in_stock
            FROM fact_daily_channel_sales
            GROUP BY 1, 2
            """
        )
    finally:
        con.close()
    return path


def query(db_path: Path, sql: str, params: list | None = None) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()
