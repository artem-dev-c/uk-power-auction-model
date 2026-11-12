"""The hourly pay-as-clear auction: bids, dispatch and the clearing price."""

import pandas as pd

from power_auction.constants import (
    PENCE_PER_GBP,
    RENEWABLE_BID_GBP_PER_MWH,
    THERMS_PER_MWH,
    VOLL_GBP_PER_MWH,
)


def gas_bids_gbp_per_mwh(
    gas_price_pence_per_therm: pd.Series, gas_plants: pd.DataFrame
) -> pd.DataFrame:
    """Break-even bid of each gas plant in each hour, columns ordered cheapest plant first."""
    fuel_cost_gbp_per_mwh = gas_price_pence_per_therm / PENCE_PER_GBP * THERMS_PER_MWH

    # All plants pay the same gas price, so the most efficient is the cheapest in every hour
    # and the merit order never changes.
    efficiency = gas_plants["efficiency"].sort_values(ascending=False)
    bids_gbp_per_mwh = {}
    for plant in efficiency.index:
        bids_gbp_per_mwh[plant] = fuel_cost_gbp_per_mwh / efficiency[plant]
    return pd.DataFrame(bids_gbp_per_mwh)


def clear_market(
    demand_mw: pd.Series,
    wind_available_mw: pd.Series,
    solar_available_mw: pd.Series,
    gas_price_pence_per_therm: pd.Series,
    gas_plants: pd.DataFrame,
) -> pd.DataFrame:
    """Dispatch cheapest-first in every hour and return one row per hour.

    Columns: demand_mw, price_gbp_per_mwh, price_setter, wind_mw, solar_mw, gas_mw,
    curtailed_mw, unserved_mw, gas_cost_gbp.
    """
    renewables_available_mw = wind_available_mw + solar_available_mw

    # Wind and solar tie at £0. When together they exceed demand, both are scaled back by
    # the same share and the rest is curtailed.
    share_used = (demand_mw / renewables_available_mw).clip(upper=1.0)
    wind_mw = wind_available_mw * share_used
    solar_mw = solar_available_mw * share_used
    curtailed_mw = (renewables_available_mw - demand_mw).clip(lower=0.0)

    bids_gbp_per_mwh = gas_bids_gbp_per_mwh(gas_price_pence_per_therm, gas_plants)
    price_gbp_per_mwh = pd.Series(RENEWABLE_BID_GBP_PER_MWH, index=demand_mw.index)
    price_setter = pd.Series("renewables", index=demand_mw.index)
    gas_mw = pd.Series(0.0, index=demand_mw.index)
    gas_cost_gbp = pd.Series(0.0, index=demand_mw.index)

    still_needed_mw = (demand_mw - renewables_available_mw).clip(lower=0.0)
    for plant in bids_gbp_per_mwh.columns:
        plant_mw = still_needed_mw.clip(upper=gas_plants.loc[plant, "capacity_mw"])
        is_running = plant_mw > 0

        # Plants are visited cheapest first, so the last one to overwrite the price in an
        # hour is the most expensive plant needed: the marginal bid.
        price_gbp_per_mwh[is_running] = bids_gbp_per_mwh.loc[is_running, plant]
        price_setter[is_running] = plant

        gas_mw = gas_mw + plant_mw
        gas_cost_gbp = gas_cost_gbp + plant_mw * bids_gbp_per_mwh[plant]
        still_needed_mw = still_needed_mw - plant_mw

    unserved_mw = still_needed_mw
    is_shortfall = unserved_mw > 0
    price_gbp_per_mwh[is_shortfall] = VOLL_GBP_PER_MWH
    price_setter[is_shortfall] = "shortfall"

    return pd.DataFrame(
        {
            "demand_mw": demand_mw,
            "price_gbp_per_mwh": price_gbp_per_mwh,
            "price_setter": price_setter,
            "wind_mw": wind_mw,
            "solar_mw": solar_mw,
            "gas_mw": gas_mw,
            "curtailed_mw": curtailed_mw,
            "unserved_mw": unserved_mw,
            "gas_cost_gbp": gas_cost_gbp,
        }
    )
