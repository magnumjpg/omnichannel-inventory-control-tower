"""MOQ, DOS-cap and volumetric container-knapsack invariants."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src import config
from src.engine.container_knapsack import (
    _container_for,
    build_factory_plan,
    moq_cartons,
    optimize_container,
    reconcile_sku_moq,
)


def test_moq_helpers_round_up_to_carton_and_supplier_minimum():
    assert moq_cartons(300, 100) == 3
    assert moq_cartons(500, 50) == 10
    assert moq_cartons(1, 100) == 1
    assert reconcile_sku_moq(301, 300, 100) == 4
    assert reconcile_sku_moq(1, 300, 100) == 3
    assert reconcile_sku_moq(1000, 500, 50) == 20
    assert reconcile_sku_moq(-5, 500, 50) == 10


def test_container_selection_thresholds():
    assert _container_for(0) == ("LCL_OCEAN", 0.0)
    assert _container_for(14.99) == ("LCL_OCEAN", 0.0)
    assert _container_for(15.0) == ("FCL_20GP", config.CONTAINER_20GP_CBM)
    assert _container_for(28.8) == ("FCL_20GP", config.CONTAINER_20GP_CBM)
    assert _container_for(28.81) == ("FCL_40HC", config.CONTAINER_40HC_CBM)
    assert _container_for(70.0) == ("FCL_40HC", config.CONTAINER_40HC_CBM)
    kind, capacity = _container_for(70.01)
    assert kind == "FCL_40HC_MULTI"
    assert capacity == pytest.approx(140.0)


def test_factory_lines_are_carton_exact_and_meet_sku_moq(project):
    plan = project.factory_plan
    lines = plan.lines
    assert not lines.empty
    assert plan.total_units == int(lines["base_units"].sum())
    assert plan.total_cartons == int(lines["base_cartons"].sum())
    assert plan.total_cbm == pytest.approx(float(lines["line_cbm"].sum()), abs=1e-4)

    assert (lines["base_units"] % lines["master_carton_qty"] == 0).all()
    assert (lines["base_units"] >= lines["sku_moq_units"]).all()
    assert (lines["base_cartons"] > 0).all()
    assert lines["base_units"].eq(
        lines["base_cartons"] * lines["master_carton_qty"]
    ).all()
    assert lines["line_cbm"].eq(
        np.round(lines["base_cartons"] * lines["carton_volume_cbm"], 4)
    ).all()


def test_factory_aggregate_moq_and_dos_cap(project):
    plan = project.factory_plan
    assert plan.total_units >= config.DEFAULT_PO_AGGREGATE_MOQ_UNITS
    assert plan.aggregate_moq_met
    assert plan.aggregate_moq_gap_units == 0

    all_lines = plan.all_lines
    assert all_lines is not None
    over = all_lines[
        (all_lines["base_cartons"] > 0)
        & (all_lines["post_po_dos"] > config.SYSTEM_DOS_CAP_DAYS + 1e-6)
        & (~all_lines["dos_cap_exception"])
    ]
    assert over.empty
    # A cap exception is only allowed when the supplier MOQ itself exceeds the
    # remaining headroom; the order still has to clear the MOQ.
    exceptions = all_lines[
        all_lines["dos_cap_exception"] & (all_lines["base_cartons"] > 0)
    ]
    if not exceptions.empty:
        assert (exceptions["base_units"] >= exceptions["sku_moq_units"]).all()


def test_weekly_order_respects_container_reserve(project):
    # The baseline order is trimmed to leave 1.8 CBM for the Tier-A top-up.
    assert project.factory_plan.total_cbm <= (
        config.CONTAINER_20GP_CBM - config.TOPUP_RESERVE_CBM + 1e-6
    )
    assert project.factory_plan.total_cbm >= 0.833 * config.CONTAINER_20GP_CBM


def test_factory_plan_is_reproducible(project):
    rebuilt = build_factory_plan(
        project.feeds["catalog_master"],
        project.demand,
        project.fba_plan,
        project.feeds["warehouse_3pl_stock"],
    )
    pd.testing.assert_frame_equal(rebuilt.lines, project.factory_plan.lines)
    assert rebuilt.total_units == project.factory_plan.total_units
    assert rebuilt.total_cbm == pytest.approx(project.factory_plan.total_cbm)


def test_container_is_filled_within_20gp_capacity(project):
    plan = project.container_plan
    assert plan.container_type == "FCL_20GP"
    assert plan.capacity_cbm == pytest.approx(28.8)
    assert 28.0 <= plan.total_cbm <= 28.8 + 1e-9
    assert plan.base_cbm + plan.topup_cbm == pytest.approx(plan.total_cbm, abs=1e-4)
    assert plan.utilization == pytest.approx(plan.total_cbm / plan.capacity_cbm, abs=1e-4)
    assert plan.utilization <= 1.0

    lines = plan.lines
    assert int(lines["total_cartons"].sum()) == plan.to_dict()["total_cartons"]
    assert int(lines["total_units"].sum()) == plan.to_dict()["total_units"]
    assert lines["total_cartons"].eq(
        lines["base_cartons"] + lines["topup_cartons"]
    ).all()
    assert lines["total_units"].eq(
        lines["base_units"] + lines["topup_units"]
    ).all()
    assert np.allclose(
        lines["total_cbm"],
        lines["total_cartons"] * lines["carton_volume_cbm"],
        atol=1e-6,
    )


def test_topup_is_greedy_tier_a_profit_velocity(project):
    lines = project.container_plan.lines
    topup = lines[lines["topup_cartons"] > 0].sort_values("topup_rank")
    assert not topup.empty
    assert topup["abc_xyz_tier"].isin(["AX", "AY"]).all()
    assert topup["topup_rank"].is_monotonic_increasing
    assert topup["marginal_profit_velocity"].is_monotonic_decreasing
    assert topup["marginal_profit_velocity"].gt(0).all()

    total_topup_cbm = float((topup["topup_cartons"] * topup["carton_volume_cbm"]).sum())
    assert total_topup_cbm == pytest.approx(project.container_plan.topup_cbm, abs=1e-6)
    # The baseline is trimmed to leave at least the reserve; the greedy fill then
    # consumes whatever whole cartons still fit, so it may slightly overshoot the
    # reserve by less than one master carton.  It can never exceed free capacity.
    remaining_cbm = config.CONTAINER_20GP_CBM - project.container_plan.base_cbm
    assert remaining_cbm >= config.TOPUP_RESERVE_CBM - 1e-6
    assert total_topup_cbm <= remaining_cbm + 1e-6
    assert project.container_plan.total_cbm <= config.CONTAINER_20GP_CBM + 1e-6


def test_topup_respects_system_dos_cap(project):
    lines = project.container_plan.lines
    topup = lines[lines["topup_cartons"] > 0]
    for _, line in topup.iterrows():
        post_topup_units = float(line["ip_sys"]) + float(line["total_units"])
        if float(line["mu_global"]) <= 0:
            continue
        dos = post_topup_units / float(line["mu_global"])
        assert dos <= config.SYSTEM_DOS_CAP_DAYS + 1e-6


def test_no_topup_breaches_sku_moq(project):
    lines = project.container_plan.lines
    for _, line in lines[lines["topup_cartons"] > 0].iterrows():
        units = float(line["base_units"]) + float(line["topup_units"])
        assert units >= float(line["sku_moq_units"])
        assert units % int(line["master_carton_qty"]) == 0


def test_optimizer_returns_lcl_when_nothing_triggers(project):
    empty = type(project.factory_plan)(
        lines=pd.DataFrame(),
        total_units=0,
        total_cartons=0,
        total_cbm=0.0,
        aggregate_moq_gap_units=0,
        aggregate_moq_met=True,
    )
    plan = optimize_container(
        empty,
        project.feeds["catalog_master"],
        project.demand,
        project.fba_plan,
    )
    assert plan.container_type == "LCL_OCEAN"
    assert plan.total_cbm == 0.0
    assert plan.lines.empty
