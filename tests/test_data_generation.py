"""Contracts for the deterministic synthetic ERP feeds and latent truth table."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import config
from src.data import generator

EXPECTED_FAMILIES = {
    "P01-PCK-CUBE": 36,
    "P02-CMP-BAG": 36,
    "P03-TLT-KIT": 36,
    "P04-TCH-ORG": 24,
    "P05-SHO-BAG": 16,
    "P06-LAU-BAG": 12,
}


def test_six_feeds_and_truth_exist(project):
    for name in (
        "catalog_master",
        "amazon_fba_inventory",
        "shopify_sales_report",
        "warehouse_3pl_stock",
        "daily_sales_history",
        "open_purchase_orders",
    ):
        assert project.files[name].exists(), name
        assert project.feeds[name].shape[0] > 0, name
    assert project.files["generation_truth"].exists()


def test_catalog_is_complete_relational_and_typed(project):
    catalog = project.feeds["catalog_master"]
    assert len(catalog) == config.N_SKUS == 160
    assert catalog["sku_id"].is_unique
    assert catalog["asin"].is_unique
    assert catalog["fnsku"].is_unique
    assert catalog["shopify_variant_id"].is_unique
    assert catalog["abc_xyz_tier"].isin(config.Z_SCORES).all()
    assert catalog["sku_id"].str.match(
        r"^P0[1-6]-[A-Z]{3}-[A-Z0-9]{1,3}-[A-Z]{3}-[A-Z]{2,3}$"
    ).all()
    assert (catalog["master_carton_qty"] > 0).all()
    assert catalog["master_carton_qty"].isin([50, 100]).all()
    assert catalog["sku_moq_units"].isin([300, 500]).all()
    assert (catalog["carton_volume_cbm"] > 0).all()
    assert (catalog["landed_cost"] < catalog["retail_price"]).all()

    counts = catalog.groupby("parent_sku_id").size().to_dict()
    assert counts == EXPECTED_FAMILIES
    assert catalog["fba_size_tier"].eq("Small Standard").all()
    assert catalog["unit_volume_cuft"].between(0.001, 1.0).all()


def test_four_dimensional_matrix_and_parent_shares(project):
    catalog = project.feeds["catalog_master"]
    for parent in EXPECTED_FAMILIES:
        family = catalog[catalog["parent_sku_id"] == parent]
        assert family["parent_stationary_share"].sum() == pytest.approx(1.0, abs=2e-6)
        assert family["master_carton_qty"].nunique() == 1

    # The three packing families share the exact S/M/L and colour/hardware
    # distributions mandated by the brief.
    heroes = catalog[catalog["parent_sku_id"].isin(
        ["P01-PCK-CUBE", "P02-CMP-BAG", "P03-TLT-KIT"]
    )]
    for parent, family in heroes.groupby("parent_sku_id"):
        by_size = family.groupby("size_width")["parent_stationary_share"].sum()
        by_size = by_size / by_size.sum()
        assert by_size["S"] == pytest.approx(0.12, abs=1e-6)
        assert by_size["M"] == pytest.approx(0.52, abs=1e-6)
        assert by_size["L"] == pytest.approx(0.36, abs=1e-6)

        by_color = family.groupby("color")["parent_stationary_share"].sum()
        by_color = by_color / by_color.sum()
        assert by_color["Black"] == pytest.approx(0.38, abs=1e-6)
        assert by_color["Navy"] == pytest.approx(0.24, abs=1e-6)
        assert by_color["Olive"] == pytest.approx(0.14, abs=1e-6)
        assert by_color["Slate"] == pytest.approx(0.11, abs=1e-6)
        assert by_color["Sand"] == pytest.approx(0.08, abs=1e-6)
        assert by_color["Terracotta"] == pytest.approx(0.05, abs=1e-6)

        by_hw = family.groupby("hardware")["parent_stationary_share"].sum()
        by_hw = by_hw / by_hw.sum()
        assert by_hw["Standard Zip"] == pytest.approx(0.68, abs=1e-6)
        assert by_hw["Water-Resistant Zip"] == pytest.approx(0.32, abs=1e-6)
        assert len(family) == 3 * 6 * 2


def test_pareto_and_tier_velocity_envelope(project):
    truth = project.truth
    top30 = truth.nlargest(30, "true_daily_mean_global")
    assert top30["true_daily_mean_global"].sum() / truth[
        "true_daily_mean_global"
    ].sum() == pytest.approx(0.735, abs=1e-9)

    ax_ay = truth[truth["abc_xyz_tier"].isin(["AX", "AY"])]
    assert len(ax_ay) == 30
    assert ax_ay["true_daily_mean_global"].between(14.0, 48.0).all()
    assert ax_ay["target_cv"].between(0.22, 0.45).all()
    assert truth["true_daily_mean_global"].gt(0).all()

    # Tier mass is exact by construction.
    masses = truth.groupby("abc_xyz_tier")["true_daily_mean_global"].sum().to_dict()
    assert masses["AX"] == pytest.approx(648.0)
    assert masses["AY"] == pytest.approx(234.0)
    assert masses["BX"] == pytest.approx(157.2)
    assert masses["BY"] == pytest.approx(74.4)
    assert masses["CZ"] == pytest.approx(86.4)


def test_scenario_injection_is_exactly_three_four_two(project):
    truth = project.truth
    counts = truth["scenario_kind"].value_counts().to_dict()
    assert counts.get("critical") == 3
    assert counts.get("penalty") == 4
    assert counts.get("aged") == 2

    fba = project.feeds["amazon_fba_inventory"].set_index("sku_id")
    critical = truth[truth["scenario_kind"] == "critical"]
    penalty = truth[truth["scenario_kind"] == "penalty"]
    aged = truth[truth["scenario_kind"] == "aged"]

    for sku, row in critical.iterrows():
        assert fba.loc[row["sku_id"], "short_term_hdos_30d"] < 14.0
    for sku, row in penalty.iterrows():
        snapshot = fba.loc[row["sku_id"]]
        assert 14.0 <= snapshot["short_term_hdos_30d"] < 28.0
        assert snapshot["long_term_hdos_90d"] < 28.0
    for sku, row in aged.iterrows():
        snapshot = fba.loc[row["sku_id"]]
        assert snapshot["inv_age_271_365"] + snapshot["inv_age_365_plus"] > 0
        assert snapshot["inv_age_271_365"] > 0


def test_daily_history_shape_and_exposure_contract(project):
    daily = project.feeds["daily_sales_history"]
    assert len(daily) == config.N_SKUS * config.HISTORY_DAYS * 2
    assert daily["channel"].value_counts().to_dict() == {
        "AMZ_US": config.N_SKUS * config.HISTORY_DAYS,
        "SHOPIFY_DTC": config.N_SKUS * config.HISTORY_DAYS,
    }
    assert daily["activity_date"].nunique() == config.HISTORY_DAYS
    assert daily["in_stock_flag"].isin([0, 1]).all()
    assert daily["effective_active_day_weight"].between(0.0, 1.0).all()
    assert (daily["observed_sales"] >= 0).all()
    assert (daily["closing_inv"] >= 0).all()
    # Fully stocked days carry full exposure.
    full = daily[(daily["in_stock_flag"] == 1) & (daily["opening_inv"] > 0)]
    assert np.allclose(full["effective_active_day_weight"], 1.0)


def test_commercial_physical_specs_by_family(project):
    catalog = project.feeds["catalog_master"]
    specs = {
        "P01-PCK-CUBE": (100, 0.060, 12.2, 300),
        "P02-CMP-BAG": (100, 0.060, 12.2, 300),
        "P03-TLT-KIT": (50, 0.060, 12.2, 300),
        "P04-TCH-ORG": (100, 0.030, 9.4, 500),
        "P05-SHO-BAG": (50, 0.099, 14.6, 300),
        "P06-LAU-BAG": (50, 0.084, 12.5, 500),
    }
    for parent, (carton, cbm, weight, moq) in specs.items():
        family = catalog[catalog["parent_sku_id"] == parent]
        assert family["master_carton_qty"].eq(carton).all()
        assert family["carton_volume_cbm"].eq(cbm).all()
        assert family["carton_weight_kg"].eq(weight).all()
        assert family["sku_moq_units"].eq(moq).all()
        assert family["po_aggregate_moq_units"].eq(5000).all()


def test_generator_is_deterministic():
    first, first_truth = generator.build_catalog(seed=42)
    second, second_truth = generator.build_catalog(seed=42)
    pd.testing.assert_frame_equal(first, second)
    pd.testing.assert_frame_equal(first_truth, second_truth)
    assert first["abc_xyz_tier"].value_counts().to_dict() == {
        "CZ": 80, "BX": 30, "BY": 20, "AX": 18, "AY": 12,
    }
