"""Weekly totals for comparing the market with and without storage."""

import pandas as pd

from power_auction.constants import DISPATCH_TOLERANCE_MW, VOLL_GBP_PER_MWH


def weekly_summary(result: pd.DataFrame) -> pd.Series:
    """Totals over the week for one auction table, with or without storage columns."""
    served_mw = result["demand_mw"] - result["unserved_mw"]
    is_shortfall = result["unserved_mw"] > DISPATCH_TOLERANCE_MW
    price_outside_shortfall_gbp_per_mwh = result.loc[~is_shortfall, "price_gbp_per_mwh"]
    gas_cost_gbp = result["gas_cost_gbp"].sum()
    unserved_mwh = result["unserved_mw"].sum()

    # A table without storage columns has no battery, so its storage totals are zero.
    storage_discharge_mwh = 0.0
    storage_profit_gbp = 0.0
    if "storage_discharge_mw" in result.columns:
        net_sold_mw = result["storage_discharge_mw"] - result["storage_charge_mw"]
        storage_discharge_mwh = result["storage_discharge_mw"].sum()
        storage_profit_gbp = (result["price_gbp_per_mwh"] * net_sold_mw).sum()

    summary = {
        "wind_mwh": result["wind_mw"].sum(),
        "solar_mwh": result["solar_mw"].sum(),
        "gas_mwh": result["gas_mw"].sum(),
        "curtailed_mwh": result["curtailed_mw"].sum(),
        "unserved_mwh": unserved_mwh,
        "shortfall_hours": is_shortfall.sum(),
        "zero_price_hours": (result["price_gbp_per_mwh"] == 0).sum(),
        "mean_price_outside_shortfall_gbp_per_mwh": price_outside_shortfall_gbp_per_mwh.mean(),
        "gas_cost_gbp": gas_cost_gbp,
        # What the storage schedule minimises: real resources used, not what anyone is paid.
        "system_cost_gbp": gas_cost_gbp + VOLL_GBP_PER_MWH * unserved_mwh,
        "consumer_bill_gbp": (result["price_gbp_per_mwh"] * served_mw).sum(),
        "storage_discharge_mwh": storage_discharge_mwh,
        "storage_profit_gbp": storage_profit_gbp,
    }
    return pd.Series(summary)
