"""Engine invariants: unconstraining, two-echelon buffers, fees and cartons."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src import config
from src.engine.fee_audit import build_fee_risk_audit
from src.engine.multiechelon import (
    age_safe_master_carton_rounding,
    build_replenishment_plan,
    lead_time_demand_variance,
    policy_shield_buffer,
    safety_stock,
)
from src.engine.unconstraining import (
    TIER_SIGMA_CAP,
    TIER_SIGMA_FLOOR,
    credibility_blend,
    cross_sectional_impute,
    continuous_exposure,
    estimate_unconstrained_demand,
    inverse_mills_ratio,
    normal_cdf,
    tobit_censored_expectation,
)


# ---------------------------------------------------------------------------
# Unconstraining primitives
# ---------------------------------------------------------------------------
def test_normal_and_tobit_primitives():
    assert normal_cdf(0.0) == pytest.approx(0.5)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert inverse_mills_ratio(0.0) == pytest.approx(math.sqrt(2.0 / math.pi))
    assert inverse_mills_ratio(2.0) > 2.0
    assert tobit_censored_expectation(10.0, 2.0, 10.0) > 10.0
    assert tobit_censored_expectation(10.0, 0.0, 12.0) == 12.0


def test_credibility_and_exposure_weights():
    assert credibility_blend(10.0, 20.0, 7.0, kappa=7.0) == pytest.approx(15.0)
    assert credibility_blend(10.0, 20.0, 0.0, kappa=7.0) == pytest.approx(20.0)
    assert credibility_blend(10.0, 20.0, 70.0, kappa=7.0) == pytest.approx(
        (70.0 / 77.0) * 10.0 + (7.0 / 77.0) * 20.0
    )

    assert continuous_exposure(5, 10, 10, 5, 2) == 1.0
    assert continuous_exposure(5, 0, 0, 5, 2) == 0.0
    partial = continuous_exposure(2, 10, 0, 5, 2)
    assert 0.0 < partial < 1.0
    # A day whose sales dwarf the prior mean must not exceed full exposure.
    assert continuous_exposure(500, 10, 0, 5, 0) == 1.0


def test_cross_sectional_imputation_uses_gamma_deflator():
    # Two available sisters with total omega 0.6, omega_i = 0.2, gamma = 0.1.
    value = cross_sectional_impute(
        omega_i=0.2, sister_sales=100.0,
        active_omega_sum=0.6, inactive_omega_sum=0.4, gamma=0.1,
    )
    assert value == pytest.approx(0.2 * 100.0 / (0.6 + 0.1 * 0.4))
    assert cross_sectional_impute(0.2, 0.0, 0.6, 0.4) == 0.0
    assert cross_sectional_impute(0.0, 100.0, 0.6, 0.4) == 0.0


def test_critical_scenarios_recover_latent_demand(project):
    merged = project.truth.merge(
        project.demand, on="sku_id", suffixes=("_truth", "")
    )
    critical = merged[merged["scenario_kind"] == "critical"]
    assert len(critical) == 3
    assert (critical["active_days_30d"] < 30.0).all()
    assert (critical["stockout_days_30d"] > 0).all()
    # The shipped estimator must recover at least the naive run-rate and
    # materially lift the most censored hero.
    assert (critical["mu_unconstrained"] >= critical["naive_30d"] - 1e-9).all()
    assert critical["unconstraining_uplift"].max() > 0.50
    assert critical["mu_unconstrained"].max() > critical["naive_30d"].max()


def test_unconstrained_output_is_complete_and_consistent(project):
    demand = project.demand
    assert len(demand) == 160
    assert demand["sku_id"].is_unique
    assert demand.notna().all().all()
    assert (demand["mu_unconstrained"] >= 0).all()
    assert (demand["sigma_unconstrained"] >= 0).all()
    assert (demand["mu_global"] >= demand["mu_unconstrained"] - 1e-4).all()
    assert np.allclose(
        demand["mu_global"], demand["mu_unconstrained"] + demand["mu_dtc"], atol=2e-4
    )
    assert np.allclose(
        demand["sigma_global"],
        np.sqrt(demand["sigma_unconstrained"] ** 2 + demand["sigma_dtc"] ** 2),
        atol=2e-4,
    )
    assert demand["pct_stockout_30d"].between(0.0, 1.0).all()


def test_sigma_respects_tier_floor_and_cap(project):
    for _, row in project.demand.iterrows():
        tier = row["abc_xyz_tier"]
        mu = max(float(row["mu_unconstrained"]), 1e-9)
        ratio = float(row["sigma_unconstrained"]) / mu
        assert ratio <= TIER_SIGMA_CAP[tier] + 2e-3
        # A zero-demand SKU is allowed to carry zero sigma.
        if row["mu_unconstrained"] > 0:
            assert ratio >= min(TIER_SIGMA_FLOOR[tier], 1.0) - 2e-3


def test_estimator_is_reproducible(project):
    again = estimate_unconstrained_demand(
        project.feeds["daily_sales_history"], project.feeds["catalog_master"]
    )
    pd.testing.assert_frame_equal(again, project.demand)


# ---------------------------------------------------------------------------
# Two-echelon buffers
# ---------------------------------------------------------------------------
def test_variance_decomposition_identity():
    parts = lead_time_demand_variance(20.0, 4.0, 14.0, 3.0)
    assert parts["demand_variance_component"] == pytest.approx(14.0 * 16.0)
    assert parts["lead_time_variance_component"] == pytest.approx(400.0 * 9.0)
    assert parts["total_variance"] == pytest.approx(
        parts["demand_variance_component"] + parts["lead_time_variance_component"]
    )
    assert parts["sigma_dl"] == pytest.approx(math.sqrt(parts["total_variance"]))
    assert parts["lead_time_variance_share"] > 0.9


def test_lead_time_variance_dominates_for_ax(project):
    ax = project.demand[project.demand["abc_xyz_tier"] == "AX"]
    l1 = [
        lead_time_demand_variance(
            row["mu_unconstrained"], row["sigma_unconstrained"],
            config.L1_MEAN_DAYS, config.L1_STD_DAYS,
        )["lead_time_variance_share"]
        for _, row in ax.iterrows()
    ]
    l2 = [
        lead_time_demand_variance(
            row["mu_global"], row["sigma_global"],
            config.L2_MEAN_DAYS, config.L2_STD_DAYS,
        )["lead_time_variance_share"]
        for _, row in ax.iterrows()
    ]
    assert min(l1) > 0.85
    assert min(l2) > 0.93
    assert np.mean(l1) > 0.86
    assert np.mean(l2) > 0.95


def test_safety_stock_is_z_times_sigma_dl():
    ss = safety_stock(10.0, 2.0, 14.0, 3.0, 2.054)
    parts = lead_time_demand_variance(10.0, 2.0, 14.0, 3.0)
    assert ss == pytest.approx(2.054 * parts["sigma_dl"])


def test_policy_shield_and_transfer_plan(project):
    plan = project.fba_plan
    assert len(plan) == 160
    assert (plan["b_fba"] >= plan["ss1"] - 1e-9).all()

    eligible = plan[plan["shield_eligible"]]
    assert len(eligible) > 0
    assert (eligible["b_fba"] >= config.FBA_SHIELD_DAYS * eligible["mu_unconstrained"] - 1e-9).all()

    # Every executable transfer is a whole master-carton multiple.
    moved = plan[plan["executable_transfer_units"] > 0]
    assert len(moved) > 0
    assert (
        moved["executable_transfer_units"] % moved["master_carton_qty"] == 0
    ).all()
    assert (
        moved["executable_transfer_cartons"] <= moved["tpl_cartons_available"]
    ).all()

    # The 120-day FBA age cap is never breached by an executed transfer.
    breach = moved[moved["post_transfer_dos"] > config.FBA_AGE_CAP_DAYS + 1e-6]
    assert breach.empty

    # Trigger + ROP identities.
    assert np.allclose(
        plan["rop1_shield"],
        plan["mu_unconstrained"] * config.L1_MEAN_DAYS + plan["b_fba"],
        atol=0.02,
    )
    assert np.allclose(
        plan["smax1"],
        (config.L1_MEAN_DAYS + config.FBA_REVIEW_CYCLE_DAYS) * plan["mu_unconstrained"]
        + plan["b_fba"],
        atol=0.02,
    )


def test_age_safe_carton_rounding_piecewise():
    # Comfortably below the cap: round up to whole cartons.
    res = age_safe_master_carton_rounding(450, 100, 10, 100, cap_days=120)
    assert res["target_cartons"] == 5
    assert res["rounded_units"] == 500
    assert res["post_transfer_dos"] == pytest.approx(60.0)

    # Age cap binds: floor rather than breach 120 days.
    res = age_safe_master_carton_rounding(1500, 1100, 10, 100, cap_days=120)
    assert res["target_cartons"] == 1
    assert res["rounded_units"] == 100
    assert res["post_transfer_dos"] <= 120.0

    # No demand or no deficit -> no transfer.
    assert age_safe_master_carton_rounding(100, 50, 0, 100)["target_cartons"] == 0
    assert age_safe_master_carton_rounding(-5, 50, 1, 100)["target_cartons"] == 0

    with pytest.raises(ValueError):
        age_safe_master_carton_rounding(10, 10, 1, 0)


def test_policy_shield_buffer_switches_on_fee_exposure():
    assert policy_shield_buffer(10.0, 20.0, 100, False) == pytest.approx(
        config.FBA_SHIELD_DAYS * 20.0
    )
    assert policy_shield_buffer(1000.0, 20.0, 100, False) == pytest.approx(1000.0)
    assert policy_shield_buffer(10.0, 20.0, 100, True) == pytest.approx(10.0)
    assert policy_shield_buffer(10.0, 20.0, 5, False) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Fee micro-economics
# ---------------------------------------------------------------------------
def test_low_inventory_fee_schedule_and_thresholds():
    fee = config.low_inventory_fee_per_unit
    assert fee(10.0, 100) == pytest.approx(config.LOW_INVENTORY_FEE_US["HDoS_0_14"])
    assert fee(13.999, 100) == pytest.approx(0.89)
    assert fee(14.0, 100) == pytest.approx(0.63)
    assert fee(20.999, 100) == pytest.approx(0.63)
    assert fee(21.0, 100) == pytest.approx(0.32)
    assert fee(27.999, 100) == pytest.approx(0.32)
    assert fee(28.0, 100) == 0.0
    assert fee(30.0, 100) == 0.0
    assert fee(10.0, 19) == 0.0
    assert fee(10.0, 20) == pytest.approx(0.89)
    assert fee(10.0, 100, exempt=True) == 0.0


def test_aged_inventory_tiers_and_unit_floors():
    assert len(config.AGED_INVENTORY_TIERS) == 9
    labels = [tier.label for tier in config.AGED_INVENTORY_TIERS]
    assert labels == [
        "0-180", "181-210", "211-240", "241-270", "271-300",
        "301-330", "331-365", "366-455", "456+",
    ]
    rates = {tier.label: tier.rate_usd_per_cuft_month for tier in config.AGED_INVENTORY_TIERS}
    assert rates["271-300"] == pytest.approx(5.45)
    assert rates["366-455"] == pytest.approx(6.90)
    assert rates["456+"] == pytest.approx(7.90)

    surcharge = config.aged_inventory_monthly_surcharge(
        {"0_90": 100, "181_270": 10, "271_365": 10, "365_plus": 10}, 0.01
    )
    assert surcharge == pytest.approx(
        10 * 0.01 * 1.00 + 10 * 0.01 * 5.45 + max(10 * 0.01 * 6.90, 10 * 0.30)
    )


def test_transfer_fee_exposure_and_fee_audit(project):
    plan = project.fba_plan
    expected = np.round(
        plan["low_inventory_fee_per_unit"] * plan["executable_transfer_units"], 2
    )
    assert np.allclose(plan["low_inventory_fee_exposure"], expected, atol=0.011)
    assert plan["low_inventory_fee_exposure"].sum() > 0

    audit = project.fee_audit
    assert len(audit) == 160
    assert (audit["projected_monthly_ais"] >= 0).all()
    assert (audit["removal_units"] >= 0).all()
    removals = audit[audit["recommendation"] == "EXECUTE REMOVAL"]
    assert len(removals) == 2
    assert (removals["payback_months"] <= config.AIS_PAYBACK_MONTHS).all()
    assert (removals["projected_monthly_ais"] > 0).all()
    assert (removals["removal_units"] > 0).all()

    # Any aged charge that does not pay back within 12 months is a markdown,
    # not a removal (same decision tree as the Tab 05 formula).
    stale = audit[(audit["projected_monthly_ais"] > 0) & (audit["payback_months"] > 12)]
    assert (stale["recommendation"] != "EXECUTE REMOVAL").all()


def test_fee_audit_matches_pipeline_recompute(project):
    recomputed = build_fee_risk_audit(
        project.feeds["catalog_master"], project.feeds["amazon_fba_inventory"]
    )
    pd.testing.assert_frame_equal(recomputed, project.fee_audit)
