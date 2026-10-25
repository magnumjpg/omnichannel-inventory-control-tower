"""Monthly Aged Inventory Surcharge (AIS) versus removal-cost audit."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src import config


def build_fee_risk_audit(catalog: pd.DataFrame, fba: pd.DataFrame) -> pd.DataFrame:
    df = catalog[["sku_id", "parent_sku_id", "abc_xyz_tier", "unit_volume_cuft"]].merge(
        fba[
            [
                "sku_id", "fba_available", "inv_age_0_90", "inv_age_91_180",
                "inv_age_181_270", "inv_age_271_365", "inv_age_365_plus",
                "short_term_hdos_30d", "long_term_hdos_90d",
                "weekly_units_sold_7d", "low_inv_fee_exempt_flag",
            ]
        ],
        on="sku_id",
        how="left",
    ).fillna(0)
    df["effective_hdos"] = df[["short_term_hdos_30d", "long_term_hdos_90d"]].max(axis=1)
    df["low_inventory_fee_per_unit"] = [
        config.low_inventory_fee_per_unit(
            float(h), float(w), bool(e)
        )
        for h, w, e in zip(
            df["effective_hdos"], df["weekly_units_sold_7d"], df["low_inv_fee_exempt_flag"]
        )
    ]

    ais_181 = df["inv_age_181_270"] * df["unit_volume_cuft"] * config.AGE_BUCKET_VOL_RATES["181_270"]
    ais_271 = df["inv_age_271_365"] * df["unit_volume_cuft"] * config.AGE_BUCKET_VOL_RATES["271_365"]
    ais_365_vol = df["inv_age_365_plus"] * df["unit_volume_cuft"] * config.AGE_BUCKET_VOL_RATES["365_plus"]
    ais_365_floor = df["inv_age_365_plus"] * config.AGE_BUCKET_UNIT_FLOORS["365_plus"]
    ais_365 = np.maximum(ais_365_vol, ais_365_floor)
    monthly = ais_181 + ais_271 + ais_365
    removal_units = df["inv_age_181_270"] + df["inv_age_271_365"] + df["inv_age_365_plus"]
    removal_cost = removal_units * config.REMOVAL_FEE_PER_UNIT_USD
    payback = np.where(monthly > 0, removal_cost / np.maximum(monthly, 1e-9), np.inf)
    recommendation = np.where(
        (removal_units > 0) & (payback <= config.AIS_PAYBACK_MONTHS),
        "EXECUTE REMOVAL",
        np.where(monthly > 0, "DISCOUNT 25% NOW", "HEALTHY"),
    )

    df["ais_181_270"] = np.round(ais_181, 4)
    df["ais_271_365"] = np.round(ais_271, 4)
    df["ais_365_plus"] = np.round(ais_365, 4)
    df["projected_monthly_ais"] = np.round(monthly, 4)
    df["removal_units"] = removal_units.astype(int)
    df["removal_cost_usd"] = np.round(removal_cost, 2)
    df["payback_months"] = np.where(np.isfinite(payback), np.round(payback, 2), np.nan)
    df["recommendation"] = recommendation
    return df.sort_values(
        ["projected_monthly_ais", "removal_units"], ascending=False
    ).reset_index(drop=True)
