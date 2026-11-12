"""Storage: a battery scheduled to give the lowest total cost of meeting demand over the week."""

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from power_auction.constants import VOLL_GBP_PER_MWH
from power_auction.market import clear_market, gas_bids_gbp_per_mwh

# The solver works on one long list of unknowns. The list is made of blocks, each holding one
# value per hour: first a block per gas plant (cheapest first), then the five blocks below.
RENEWABLES_USED = 0
CHARGE = 1
DISCHARGE = 2
STORED = 3
UNSERVED = 4


def _block(position: int, n_gas_plants: int, n_hours: int) -> slice:
    """Where one of the five named blocks sits in the list of unknowns."""
    start = (n_gas_plants + position) * n_hours
    return slice(start, start + n_hours)


def build_dispatch_problem(
    hourly: pd.DataFrame,
    gas_plants: pd.DataFrame,
    power_mw: float,
    energy_mwh: float,
    round_trip_efficiency: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Write least-cost dispatch with a battery as a linear program for scipy's linprog.

    Returns (cost_gbp_per_mwh, equations, targets, limits): the cost of each unknown, the
    equations that must hold as equations @ unknowns == targets, and the lowest and highest
    value each unknown may take.
    """
    bids_gbp_per_mwh = gas_bids_gbp_per_mwh(hourly["gas_price_pence_per_therm"], gas_plants)
    renewables_available_mw = hourly["wind_available_mw"] + hourly["solar_available_mw"]
    n_hours = len(hourly)
    n_gas_plants = len(gas_plants)

    # The loss is split evenly between charging and discharging.
    one_way_efficiency = round_trip_efficiency**0.5

    # The battery ends the week empty, as it starts, so it cannot use energy it never paid for.
    stored_limit_mwh = np.full(n_hours, energy_mwh)
    stored_limit_mwh[-1] = 0.0

    free = np.zeros(n_hours)
    cost_blocks = []
    limit_blocks = []
    for plant in bids_gbp_per_mwh.columns:
        cost_blocks.append(bids_gbp_per_mwh[plant])
        limit_blocks.append(np.full(n_hours, gas_plants.loc[plant, "capacity_mw"]))
    # The five blocks after the gas plants, in the order named at the top of this file.
    cost_blocks += [
        free,  # renewables used
        free,  # charge
        free,  # discharge
        free,  # stored
        np.full(n_hours, VOLL_GBP_PER_MWH),  # unserved
    ]
    limit_blocks += [
        renewables_available_mw,  # renewables used
        np.full(n_hours, power_mw),  # charge
        np.full(n_hours, power_mw),  # discharge
        stored_limit_mwh,  # stored
        np.full(n_hours, np.inf),  # unserved
    ]
    cost_gbp_per_mwh = np.concatenate(cost_blocks)
    highest = np.concatenate(limit_blocks)
    limits = np.column_stack([np.zeros(len(highest)), highest])

    # Each block of an equation is an n_hours x n_hours matrix. The identity matrix means
    # "this unknown, in the same hour"; zeros means the unknown is not in the equation.
    same_hour = np.eye(n_hours)
    absent = np.zeros((n_hours, n_hours))
    # Row t is stored[t] - stored[t-1]. The battery starts empty, so row 0 is just stored[0].
    change_in_stored = same_hour - np.eye(n_hours, k=-1)

    # In every hour: gas + renewables used - charge + discharge + unserved = demand.
    supply_meets_demand = np.hstack(
        [same_hour] * n_gas_plants
        + [
            same_hour,  # renewables used
            -same_hour,  # charge
            same_hour,  # discharge
            absent,  # stored
            same_hour,  # unserved
        ]
    )
    # In every hour: change in stored energy = charge after losses - discharge before losses.
    battery_energy = np.hstack(
        [absent] * n_gas_plants
        + [
            absent,  # renewables used
            -one_way_efficiency * same_hour,  # charge
            same_hour / one_way_efficiency,  # discharge
            change_in_stored,  # stored
            absent,  # unserved
        ]
    )
    equations = np.vstack([supply_meets_demand, battery_energy])
    targets = np.concatenate([hourly["demand_mw"], np.zeros(n_hours)])

    return cost_gbp_per_mwh, equations, targets, limits


def schedule_storage(
    hourly: pd.DataFrame,
    gas_plants: pd.DataFrame,
    power_mw: float,
    energy_mwh: float,
    round_trip_efficiency: float,
) -> pd.DataFrame:
    """Battery schedule with the lowest system cost, one row per hour.

    Columns: storage_charge_mw, storage_discharge_mw, storage_energy_mwh (at the end of the
    hour). System cost is gas fuel plus the Value of Lost Load on unserved demand.
    """
    cost_gbp_per_mwh, equations, targets, limits = build_dispatch_problem(
        hourly, gas_plants, power_mw, energy_mwh, round_trip_efficiency
    )
    cheapest = linprog(cost_gbp_per_mwh, A_eq=equations, b_eq=targets, bounds=limits)
    if not cheapest.success:
        raise RuntimeError(cheapest.message)

    n_hours = len(hourly)
    n_gas_plants = len(gas_plants)
    charge_block = _block(CHARGE, n_gas_plants, n_hours)
    discharge_block = _block(DISCHARGE, n_gas_plants, n_hours)
    stored_block = _block(STORED, n_gas_plants, n_hours)

    # Many schedules share the lowest cost: every MWh that relieves a shortfall is worth the
    # same whichever shortfall hour it goes to, yet the hour chosen decides which prices fall.
    # So solve again, holding cost at the lowest, and pick the schedule that stores as little
    # energy for as short a time as possible and never cycles for nothing. The battery then
    # discharges as soon as a shortfall starts and charges as late as it can.
    battery_use = np.zeros(len(cost_gbp_per_mwh))
    battery_use[charge_block] = 1.0
    battery_use[discharge_block] = 1.0
    battery_use[stored_block] = 1.0

    # With no slack at all, solver rounding makes the second solve infeasible. This much lets
    # it spend up to about 30p more than the lowest cost over the week, which it does.
    cost_limit_gbp = cheapest.fun * (1 + 1e-9)
    chosen = linprog(
        battery_use,
        A_ub=cost_gbp_per_mwh.reshape(1, -1),
        b_ub=[cost_limit_gbp],
        A_eq=equations,
        b_eq=targets,
        bounds=limits,
    )
    if not chosen.success:
        raise RuntimeError(chosen.message)

    # The solver can return values like -1e-12 where it means zero.
    unknowns = chosen.x.clip(min=0.0)
    return pd.DataFrame(
        {
            "storage_charge_mw": unknowns[charge_block],
            "storage_discharge_mw": unknowns[discharge_block],
            "storage_energy_mwh": unknowns[stored_block],
        },
        index=hourly.index,
    )


def clear_market_with_storage(
    hourly: pd.DataFrame, gas_plants: pd.DataFrame, schedule: pd.DataFrame
) -> pd.DataFrame:
    """Run the same auction as without storage, on demand plus charging minus discharging.

    The battery takes part as extra demand when it charges and as supply that is always
    accepted when it discharges, so it never sets the price itself. Returns the auction table
    with the original demand and the battery's schedule alongside.
    """
    net_demand_mw = (
        hourly["demand_mw"] + schedule["storage_charge_mw"] - schedule["storage_discharge_mw"]
    )
    result = clear_market(hourly.assign(demand_mw=net_demand_mw), gas_plants)
    result["demand_mw"] = hourly["demand_mw"]
    return result.join(schedule)
