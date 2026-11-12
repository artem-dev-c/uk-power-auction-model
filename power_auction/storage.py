"""Storage: a battery scheduled to give the lowest total cost of meeting demand over the week."""

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from power_auction.constants import VOLL_GBP_PER_MWH
from power_auction.market import clear_market, gas_bids_gbp_per_mwh

# Besides the output of each gas plant, the solver chooses these five things in every hour.
OTHER_UNKNOWNS = ["renewables_used", "charge", "discharge", "stored", "unserved"]


def _positions(gas_plants: pd.DataFrame, n_hours: int) -> dict[str, slice]:
    """Where each unknown sits in the solver's one long list.

    The list holds one value per hour for each gas plant, then one value per hour for each of
    the other unknowns. The result maps a name to its stretch of the list.
    """
    names = list(gas_plants.index) + OTHER_UNKNOWNS
    positions = {}
    for number, name in enumerate(names):
        positions[name] = slice(number * n_hours, (number + 1) * n_hours)
    return positions


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
    n_hours = len(hourly)
    position = _positions(gas_plants, n_hours)
    n_unknowns = len(position) * n_hours

    bids_gbp_per_mwh = gas_bids_gbp_per_mwh(hourly["gas_price_pence_per_therm"], gas_plants)
    renewables_available_mw = hourly["wind_available_mw"] + hourly["solar_available_mw"]

    # Cost: gas costs its bid, unserved demand costs the Value of Lost Load, the rest is free.
    cost_gbp_per_mwh = np.zeros(n_unknowns)
    for plant in gas_plants.index:
        cost_gbp_per_mwh[position[plant]] = bids_gbp_per_mwh[plant]
    cost_gbp_per_mwh[position["unserved"]] = VOLL_GBP_PER_MWH

    # The battery ends the week empty, as it starts, so it cannot use energy it never paid for.
    stored_limit_mwh = np.full(n_hours, energy_mwh)
    stored_limit_mwh[-1] = 0.0

    # Limits: every unknown is at least zero and at most the value set here.
    highest = np.zeros(n_unknowns)
    for plant in gas_plants.index:
        highest[position[plant]] = gas_plants.loc[plant, "capacity_mw"]
    highest[position["renewables_used"]] = renewables_available_mw
    highest[position["charge"]] = power_mw
    highest[position["discharge"]] = power_mw
    highest[position["stored"]] = stored_limit_mwh
    highest[position["unserved"]] = np.inf
    limits = np.column_stack([np.zeros(n_unknowns), highest])

    # Each equation is written once per hour, so an unknown enters it as an n_hours x n_hours
    # matrix. The identity matrix means "the value in the same hour".
    same_hour = np.eye(n_hours)
    previous_hour = np.eye(n_hours, k=-1)

    # Gas + renewables used + discharge + unserved - charge = demand.
    supply_meets_demand = np.zeros((n_hours, n_unknowns))
    for plant in gas_plants.index:
        supply_meets_demand[:, position[plant]] = same_hour
    supply_meets_demand[:, position["renewables_used"]] = same_hour
    supply_meets_demand[:, position["discharge"]] = same_hour
    supply_meets_demand[:, position["unserved"]] = same_hour
    supply_meets_demand[:, position["charge"]] = -same_hour

    # Stored now - stored an hour ago - charge after losses + discharge before losses = 0.
    # The loss is split evenly between charging and discharging. In the first hour there is no
    # hour before, which is the same as starting empty.
    one_way_efficiency = round_trip_efficiency**0.5
    battery_energy = np.zeros((n_hours, n_unknowns))
    battery_energy[:, position["stored"]] = same_hour - previous_hour
    battery_energy[:, position["charge"]] = -one_way_efficiency * same_hour
    battery_energy[:, position["discharge"]] = same_hour / one_way_efficiency

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

    # Many schedules share the lowest cost: every MWh that relieves a shortfall is worth the
    # same whichever shortfall hour it goes to, yet the hour chosen decides which prices fall.
    # So solve again, holding cost at the lowest, and pick the schedule that stores as little
    # energy for as short a time as possible and never cycles for nothing. The battery then
    # discharges as soon as a shortfall starts and charges as late as it can.
    position = _positions(gas_plants, len(hourly))
    battery_use = np.zeros(len(cost_gbp_per_mwh))
    battery_use[position["charge"]] = 1.0
    battery_use[position["discharge"]] = 1.0
    battery_use[position["stored"]] = 1.0

    # With no slack at all, solver rounding can make the second solve infeasible. This much lets
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
            "storage_charge_mw": unknowns[position["charge"]],
            "storage_discharge_mw": unknowns[position["discharge"]],
            "storage_energy_mwh": unknowns[position["stored"]],
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
