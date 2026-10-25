"""Two-echelon stochastic buffers, policy shields and age-safe cartonization."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src import config


# ---------------------------------------------------------------------------
# Stochastic primitives
# ---------------------------------------------------------------------------
def lead_time_demand_variance(
    d_mean: float,
    sigma_d: float,
    l_mean: float,
    sigma_l: float,
) -> dict[str, float]:
    """Convolution of independent daily demand and lead-time distributions.

    ``sigma_DL^2 = L_mean * sigma_D^2 + D_mean^2 * sigma_L^2``.
    """

    demand_component = l_mean * sigma_d ** 2
    lead_component = d_mean ** 2 * sigma_l ** 2
    total = demand_component + lead_component
    return {
        "demand_variance_component": float(demand_component),
        "lead_time_variance_component": float(lead_component),
        "total_variance": float(total),
        "sigma_dl": float(math.sqrt(max(total, 0.0))),
        "lead_time_variance_share": float(lead_component / total) if total > 0 else 0.0,
    }


def safety_stock(
    d_mean: float,
    sigma_d: float,
    l_mean: float,
    sigma_l: float,
    z_score: float,
) -> float:
    parts = lead_time_demand_variance(d_mean, sigma_d, l_mean, sigma_l)
    return float(z_score * parts["sigma_dl"])


def policy_shield_buffer(
    ss1: float,
    d_fba: float,
    weekly_units: float,
    exempt: bool,
) -> float:
    """``max(SS_1, 32 * D_FBA)`` for non-exempt FBA heroes."""

    if exempt or weekly_units < config.HERO_WEEKLY_UNIT_THRESHOLD:
        return float(max(ss1, 0.0))
    return float(max(ss1, config.FBA_SHIELD_DAYS * d_fba))


def age_safe_master_carton_rounding(
    raw_deficit: float,
    ip_fba: float,
    d_fba: float,
    master_carton_qty: int,
    cap_days: float = config.FBA_AGE_CAP_DAYS,
) -> dict[str, float]:
    """Piecewise carton rounding that never breaches the FBA age cap.

    Returns the target carton count, the age-safe raw quantity and the
    resulting days of supply after the transfer.
    """

    m = int(master_carton_qty)
    if m <= 0:
        raise ValueError("master_carton_qty must be positive")
    if d_fba <= 0 or raw_deficit <= 0:
        return {
            "target_cartons": 0.0,
            "rounded_units": 0.0,
            "post_transfer_dos": (ip_fba / d_fba if d_fba > 0 else 0.0),
        }

    ceil_cartons = math.ceil(raw_deficit / m - 1e-12)
    if ip_fba + ceil_cartons * m <= cap_days * d_fba + 1e-9:
        target = float(ceil_cartons)
    else:
        target = float(max(0, math.floor((cap_days * d_fba - ip_fba) / m + 1e-9)))
    return {
        "target_cartons": target,
        "rounded_units": target * m,
        "post_transfer_dos": (ip_fba + target * m) / d_fba,
    }


# ---------------------------------------------------------------------------
# Echelon 1 + 2 planning
# ---------------------------------------------------------------------------
def build_replenishment_plan(
    catalog: pd.DataFrame,
    demand: pd.DataFrame,
    fba_snapshot: pd.DataFrame,
    tpl_stock: pd.DataFrame,
) -> pd.DataFrame:
    """Build the 3PL -> FBA transfer plan and the upstream policy parameters."""

    cat = catalog[
        [
            "sku_id", "parent_sku_id", "abc_xyz_tier", "z_score",
            "master_carton_qty", "carton_volume_cbm", "sku_moq_units",
            "landed_cost", "retail_price",
        ]
    ].copy()
    dem = demand[
        [
            "sku_id", "mu_unconstrained", "sigma_unconstrained", "mu_dtc",
            "sigma_dtc", "mu_global", "sigma_global", "naive_30d",
            "active_days_30d", "mu_active", "mu_cs", "stockout_days_30d",
            "pct_stockout_30d",
        ]
    ].copy()
    fba = fba_snapshot[
        [
            "sku_id", "fba_available", "fba_fc_transfer", "fba_inbound_shipped",
            "fba_inbound_receiving", "short_term_hdos_30d", "long_term_hdos_90d",
            "weekly_units_sold_7d", "low_inv_fee_exempt_flag",
        ]
    ].copy()
    tpl = tpl_stock[["sku_id", "on_hand_units", "allocated_dtc_units"]].copy()

    df = cat.merge(dem, on="sku_id", how="left").merge(fba, on="sku_id", how="left").merge(
        tpl, on="sku_id", how="left"
    )
    df = df.fillna(
        {
            "mu_unconstrained": 0.0, "sigma_unconstrained": 0.0,
            "mu_dtc": 0.0, "sigma_dtc": 0.0, "mu_global": 0.0,
            "sigma_global": 0.0, "fba_available": 0, "fba_fc_transfer": 0,
            "fba_inbound_shipped": 0, "fba_inbound_receiving": 0,
            "short_term_hdos_30d": 0.0, "long_term_hdos_90d": 0.0,
            "weekly_units_sold_7d": 0, "low_inv_fee_exempt_flag": True,
            "on_hand_units": 0, "allocated_dtc_units": 0,
        }
    )

    d_fba = df["mu_unconstrained"].to_numpy(dtype=float)
    sigma_fba = df["sigma_unconstrained"].to_numpy(dtype=float)
    d_global = df["mu_global"].to_numpy(dtype=float)
    sigma_global = df["sigma_global"].to_numpy(dtype=float)
    z = df["z_score"].to_numpy(dtype=float)
    m = df["master_carton_qty"].to_numpy(dtype=int)
    weekly = df["weekly_units_sold_7d"].to_numpy(dtype=float)
    exempt = df["low_inv_fee_exempt_flag"].astype(bool).to_numpy()

    ss1 = np.array([
        safety_stock(d_fba[i], sigma_fba[i], config.L1_MEAN_DAYS, config.L1_STD_DAYS, z[i])
        for i in range(len(df))
    ])
    ss2 = np.array([
        safety_stock(d_global[i], sigma_global[i], config.L2_MEAN_DAYS, config.L2_STD_DAYS, z[i])
        for i in range(len(df))
    ])
    # Buffers are integer units in the workbook (CEILING.MATH(...,1)); keep
    # the Python plan on the same convention so the fee shield is never
    # rounded a fraction of a unit below 32 * D_FBA.
    shield = np.ceil(np.array([
        policy_shield_buffer(ss1[i], d_fba[i], weekly[i], bool(exempt[i]))
        for i in range(len(df))
    ]))

    ip_fba = (
        df["fba_available"].to_numpy(dtype=float)
        + df["fba_fc_transfer"].to_numpy(dtype=float)
        + df["fba_inbound_shipped"].to_numpy(dtype=float)
        + df["fba_inbound_receiving"].to_numpy(dtype=float)
    )
    rop1 = d_fba * config.L1_MEAN_DAYS + shield
    smax1 = (config.L1_MEAN_DAYS + config.FBA_REVIEW_CYCLE_DAYS) * d_fba + shield
    triggered = ip_fba <= rop1
    raw_def = np.where(triggered, np.maximum(0.0, smax1 - ip_fba), 0.0)

    target_cartons = np.zeros(len(df))
    age_safe_units = np.zeros(len(df))
    post_dos = np.zeros(len(df))
    for i in range(len(df)):
        res = age_safe_master_carton_rounding(raw_def[i], ip_fba[i], d_fba[i], int(m[i]))
        target_cartons[i] = res["target_cartons"]
        age_safe_units[i] = res["rounded_units"]
        post_dos[i] = res["post_transfer_dos"]

    tpl_cartons = np.floor(df["on_hand_units"].to_numpy(dtype=float) / m).astype(int)
    exec_cartons = np.maximum(0, np.minimum(target_cartons.astype(int), tpl_cartons))
    transfer_units = exec_cartons * m
    post_transfer_dos = np.where(
        d_fba > 0, (ip_fba + transfer_units) / np.maximum(d_fba, 1e-9), 0.0
    )

    effective_hdos = np.maximum(
        df["short_term_hdos_30d"].to_numpy(dtype=float),
        df["long_term_hdos_90d"].to_numpy(dtype=float),
    )
    fee = np.array([
        config.low_inventory_fee_per_unit(float(effective_hdos[i]), float(weekly[i]), bool(exempt[i]))
        for i in range(len(df))
    ])

    df["ss1"] = np.round(ss1, 2)
    df["ss2"] = np.round(ss2, 2)
    df["b_fba"] = np.round(shield, 2)
    df["shield_eligible"] = (~exempt) & (weekly >= config.HERO_WEEKLY_UNIT_THRESHOLD)
    df["ip_fba"] = np.round(ip_fba, 2)
    df["rop1_shield"] = np.round(rop1, 2)
    df["smax1"] = np.round(smax1, 2)
    df["trigger_transfer"] = triggered
    df["raw_deficit_units"] = np.round(raw_def, 2)
    df["target_cartons"] = target_cartons.astype(int)
    df["age_safe_target_units"] = age_safe_units.astype(int)
    df["tpl_cartons_available"] = tpl_cartons
    df["executable_transfer_cartons"] = exec_cartons
    df["executable_transfer_units"] = transfer_units
    df["post_transfer_dos"] = np.round(post_transfer_dos, 2)
    df["effective_hdos"] = np.round(effective_hdos, 2)
    df["low_inventory_fee_per_unit"] = np.round(fee, 4)
    df["low_inventory_fee_exposure"] = np.round(fee * transfer_units, 2)
    df["transfer_cbm"] = np.round(exec_cartons * df["carton_volume_cbm"].to_numpy(), 4)
    return df.sort_values(["abc_xyz_tier", "mu_global"], ascending=[True, False]).reset_index(drop=True)
