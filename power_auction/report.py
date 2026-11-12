"""Weekly totals for comparing the market with and without storage."""

import pandas as pd

from power_auction.constants import DISPATCH_TOLERANCE_MW, VOLL_GBP_PER_MWH
from power_auction.storage import clear_market_with_storage, schedule_storage


def weekly_summary(result: pd.DataFrame) -> pd.Series:
    """Totals over the week for one auction table, with or without storage columns."""
    served_mw = result["demand_mw"] - result["unserved_mw"]
    is_shortfall = result["unserved_mw"] > DISPATCH_TOLERANCE_MW
    gas_cost_gbp = result["gas_cost_gbp"].sum()
    unserved_mwh = result["unserved_mw"].sum()

    # A table without storage columns has no battery, so its storage totals are zero.
    storage_charge_mwh = 0.0
    storage_discharge_mwh = 0.0
    storage_profit_gbp = 0.0
    if "storage_discharge_mw" in result.columns:
        net_sold_mw = result["storage_discharge_mw"] - result["storage_charge_mw"]
        storage_charge_mwh = result["storage_charge_mw"].sum()
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
        "gas_cost_gbp": gas_cost_gbp,
        # What the storage schedule minimises: real resources used, not what anyone is paid.
        "system_cost_gbp": gas_cost_gbp + VOLL_GBP_PER_MWH * unserved_mwh,
        "consumer_bill_gbp": (result["price_gbp_per_mwh"] * served_mw).sum(),
        "storage_charge_mwh": storage_charge_mwh,
        "storage_discharge_mwh": storage_discharge_mwh,
        "storage_profit_gbp": storage_profit_gbp,
    }
    return pd.Series(summary)


def storage_size_sweep(
    hourly: pd.DataFrame,
    gas_plants: pd.DataFrame,
    power_mw_options: list[float],
    hours_at_full_power: float,
    round_trip_efficiency: float,
) -> pd.DataFrame:
    """Unserved energy and system cost for a range of battery sizes, one row per size.

    Only these two totals are reported because they are the same for every schedule with the
    lowest cost. Prices, the consumer bill and battery profit are not.
    """
    rows = []
    for power_mw in power_mw_options:
        energy_mwh = power_mw * hours_at_full_power
        schedule = schedule_storage(hourly, gas_plants, power_mw, energy_mwh, round_trip_efficiency)
        summary = weekly_summary(clear_market_with_storage(hourly, gas_plants, schedule))
        rows.append(
            {
                "power_mw": power_mw,
                "energy_mwh": energy_mwh,
                "unserved_mwh": summary["unserved_mwh"],
                "system_cost_gbp": summary["system_cost_gbp"],
            }
        )
    return pd.DataFrame(rows).set_index("power_mw")
