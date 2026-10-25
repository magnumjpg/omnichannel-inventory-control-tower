"""Schema and referential-integrity tests for the ERP ingestion layer."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from src.data.ingestion import REQUIRED_COLUMNS, load_raw_feeds, validate_feeds


def test_all_required_columns_and_snapshots(project):
    for name, required in REQUIRED_COLUMNS.items():
        frame = project.feeds[name]
        assert set(required) <= set(frame.columns), name
        assert not frame.columns.duplicated().any(), name

    fba = project.feeds["amazon_fba_inventory"]
    tpl = project.feeds["warehouse_3pl_stock"]
    assert fba["snapshot_date"].nunique() == 1
    assert tpl["snapshot_date"].nunique() == 1
    assert fba["snapshot_date"].iloc[0] == "2026-10-25"
    assert tpl["snapshot_date"].iloc[0] == "2026-10-25"
    assert len(fba) == 160 and fba["sku_id"].is_unique
    assert len(tpl) == 160 and tpl["sku_id"].is_unique


def test_open_purchase_orders_are_carton_exact(project):
    catalog = project.feeds["catalog_master"].set_index("sku_id")
    pos = project.feeds["open_purchase_orders"]
    carton_qty = pos["sku_id"].map(catalog["master_carton_qty"])
    assert (pos["ordered_units"] % carton_qty == 0).all()
    assert (pos["ordered_units"] >= pos["open_units"]).all()
    assert (pos["open_units"] >= 0).all()
    assert pos["expected_3pl_eta"].notna().all()


def test_shopify_orders_reconcile_to_dtc_history(project):
    shopify = project.feeds["shopify_sales_report"]
    assert (shopify["quantity"] > 0).all()
    assert (shopify["gross_price"] >= 0).all()
    assert shopify["fulfillment_status"].isin(["fulfilled", "unfulfilled"]).all()

    daily = project.feeds["daily_sales_history"]
    dtc = daily[daily["channel"] == "SHOPIFY_DTC"]
    # Every observed DTC unit must be represented in the order-line feed.
    assert int(shopify["quantity"].sum()) == int(dtc["observed_sales"].sum())


def test_inventory_identity_holds_per_sku_day(project):
    daily = project.feeds["daily_sales_history"]
    delta = daily["opening_inv"] - daily["observed_sales"] - daily["closing_inv"]
    # Inventory is only consumed by sales in this simulation; inbound receipts
    # are modelled by the next day's opening balance.
    assert delta.min() >= -1e-9
    zero_stock = daily[daily["in_stock_flag"] == 0]
    # A day that opens with no inventory has zero exposure.  A same-day
    # depletion retains a positive partial-day weight tau in (0, 1].
    assert (zero_stock.loc[zero_stock["opening_inv"] <= 0,
                           "effective_active_day_weight"] == 0).all()
    assert zero_stock["effective_active_day_weight"].between(0.0, 1.0).all()


def test_validation_rejects_orphan_sku(project):
    feeds = {name: frame.copy() for name, frame in project.feeds.items()}
    feeds["amazon_fba_inventory"].loc[0, "sku_id"] = "P99-XXX-99-XXX-XX"
    with pytest.raises(ValueError, match="orphan sku_id"):
        validate_feeds(feeds)


def test_validation_rejects_negative_inventory_and_bad_tau(project):
    feeds = {name: frame.copy() for name, frame in project.feeds.items()}
    feeds["amazon_fba_inventory"].loc[0, "fba_available"] = -1
    with pytest.raises(ValueError, match="negative fba_available"):
        validate_feeds(feeds)

    feeds = {name: frame.copy() for name, frame in project.feeds.items()}
    feeds["daily_sales_history"].loc[0, "effective_active_day_weight"] = 1.5
    with pytest.raises(ValueError, match="outside"):
        validate_feeds(feeds)


def test_loader_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_raw_feeds(tmp_path)
