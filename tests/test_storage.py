from pathlib import Path

import pandas as pd
import pytest
from scipy.optimize import linprog

from power_auction.constants import (
    DISPATCH_TOLERANCE_MW,
    STORAGE_ENERGY_MWH,
    STORAGE_POWER_MW,
    STORAGE_ROUND_TRIP_EFFICIENCY,
    VOLL_GBP_PER_MWH,
)
from power_auction.data import load_inputs
from power_auction.market import clear_market
from power_auction.report import storage_size_sweep, weekly_summary
from power_auction.storage import (
    build_dispatch_problem,
    clear_market_with_storage,
    schedule_storage,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SMALL_MW = 1e-6


@pytest.fixture(scope="module")
def inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    return load_inputs(REPO_ROOT / "data" / "data.xlsx")


@pytest.fixture(scope="module")
def without_storage(inputs) -> pd.DataFrame:
    hourly, gas_plants = inputs
    return clear_market(hourly, gas_plants)


@pytest.fixture(scope="module")
def with_storage(inputs) -> pd.DataFrame:
    hourly, gas_plants = inputs
    schedule = schedule_storage(
        hourly, gas_plants, STORAGE_POWER_MW, STORAGE_ENERGY_MWH, STORAGE_ROUND_TRIP_EFFICIENCY
    )
    return clear_market_with_storage(hourly, gas_plants, schedule)


def test_solver_without_a_battery_reproduces_the_auction(inputs, without_storage):
    """Two independent methods must agree: the optimiser and the merit-order auction."""
    hourly, gas_plants = inputs
    cost_gbp_per_mwh, equations, targets, limits = build_dispatch_problem(
        hourly, gas_plants, power_mw=0.0, energy_mwh=0.0, round_trip_efficiency=1.0
    )
    solved = linprog(cost_gbp_per_mwh, A_eq=equations, b_eq=targets, bounds=limits)

    auction_cost_gbp = weekly_summary(without_storage)["system_cost_gbp"]
    assert solved.fun == pytest.approx(auction_cost_gbp, rel=1e-9)

    # The solver reports what one more MW of demand would cost in each hour. Without storage
    # that is the marginal bid, so it must equal the auction price in all 168 hours.
    cost_of_one_more_mw = solved.eqlin.marginals[: len(hourly)]
    assert cost_of_one_more_mw == pytest.approx(without_storage["price_gbp_per_mwh"].to_numpy())


def test_small_market_with_a_battery_worked_out_by_hand():
    """Three hours: spare renewables, an ordinary hour, then a 50 MW shortfall."""
    hourly = pd.DataFrame(
        {
            "demand_mw": [100.0, 150.0, 300.0],
            "gas_price_pence_per_therm": 100.0,
            "wind_available_mw": [120.0, 60.0, 60.0],
            "solar_available_mw": [80.0, 40.0, 40.0],
        },
        index=[1, 2, 3],
    )
    gas_plants = pd.DataFrame(
        {"capacity_mw": [100, 50], "efficiency": [0.5, 0.4]}, index=["cheap", "dear"]
    )
    cheap_bid = 34.121 / 0.5
    dear_bid = 34.121 / 0.4

    # Round trip 0.64 means 0.8 each way: 100 MWh in, 80 MWh stored, 64 MWh out.
    schedule = schedule_storage(
        hourly, gas_plants, power_mw=100.0, energy_mwh=100.0, round_trip_efficiency=0.64
    )
    result = clear_market_with_storage(hourly, gas_plants, schedule)

    # Hour 1: the battery soaks up all 100 MW of spare renewables, so nothing is curtailed.
    assert result.loc[1, "storage_charge_mw"] == pytest.approx(100.0)
    assert result.loc[1, "storage_energy_mwh"] == pytest.approx(80.0)
    assert result.loc[1, "curtailed_mw"] == pytest.approx(0.0, abs=SMALL_MW)
    assert result.loc[1, "price_gbp_per_mwh"] == 0.0

    # Hour 2: charging from gas to displace dearer gas later would lose money after losses
    # (0.64 x dear bid < cheap bid), so the battery waits.
    assert result.loc[2, "storage_charge_mw"] == pytest.approx(0.0, abs=SMALL_MW)
    assert result.loc[2, "storage_discharge_mw"] == pytest.approx(0.0, abs=SMALL_MW)
    assert result.loc[2, "price_gbp_per_mwh"] == pytest.approx(cheap_bid)

    # Hour 3: 64 MW out covers the 50 MW shortfall and 14 MW of the dear plant's output.
    assert result.loc[3, "storage_discharge_mw"] == pytest.approx(64.0)
    assert result.loc[3, "storage_energy_mwh"] == pytest.approx(0.0, abs=SMALL_MW)
    assert result.loc[3, "unserved_mw"] == pytest.approx(0.0, abs=SMALL_MW)
    assert result.loc[3, "gas_mw"] == pytest.approx(100.0 + 36.0)
    assert result.loc[3, "price_setter"] == "dear"
    assert result.loc[3, "price_gbp_per_mwh"] == pytest.approx(dear_bid)


def test_battery_relieves_the_earliest_shortfall_hour_first():
    """Two equal 50 MW shortfalls and a battery that can deliver only 64 MWh."""
    hourly = pd.DataFrame(
        {
            "demand_mw": [100.0, 300.0, 300.0],
            "gas_price_pence_per_therm": 100.0,
            "wind_available_mw": [120.0, 60.0, 60.0],
            "solar_available_mw": [80.0, 40.0, 40.0],
        },
        index=[1, 2, 3],
    )
    gas_plants = pd.DataFrame(
        {"capacity_mw": [100, 50], "efficiency": [0.5, 0.4]}, index=["cheap", "dear"]
    )

    schedule = schedule_storage(
        hourly, gas_plants, power_mw=100.0, energy_mwh=100.0, round_trip_efficiency=0.64
    )
    result = clear_market_with_storage(hourly, gas_plants, schedule)

    # Any split of the 64 MWh between hours 2 and 3 costs the same. The rule sends it to the
    # first shortfall, so hour 2 is fully covered and its price drops to the dear plant's bid.
    assert result.loc[2, "storage_discharge_mw"] == pytest.approx(50.0)
    assert result.loc[2, "unserved_mw"] == pytest.approx(0.0, abs=SMALL_MW)
    assert result.loc[2, "price_setter"] == "dear"

    assert result.loc[3, "storage_discharge_mw"] == pytest.approx(14.0)
    assert result.loc[3, "unserved_mw"] == pytest.approx(36.0)
    assert result.loc[3, "price_gbp_per_mwh"] == VOLL_GBP_PER_MWH


def test_supply_plus_unserved_equals_demand_plus_charging_every_hour(with_storage):
    supplied_mw = (
        with_storage["wind_mw"]
        + with_storage["solar_mw"]
        + with_storage["gas_mw"]
        + with_storage["storage_discharge_mw"]
    )
    used_mw = with_storage["demand_mw"] + with_storage["storage_charge_mw"]
    difference_mw = supplied_mw + with_storage["unserved_mw"] - used_mw
    assert difference_mw.abs().max() < SMALL_MW


def test_battery_stays_within_its_limits_and_ends_empty(with_storage):
    assert with_storage["storage_charge_mw"].max() <= STORAGE_POWER_MW + SMALL_MW
    assert with_storage["storage_discharge_mw"].max() <= STORAGE_POWER_MW + SMALL_MW
    assert with_storage["storage_energy_mwh"].min() >= 0.0
    assert with_storage["storage_energy_mwh"].max() <= STORAGE_ENERGY_MWH + SMALL_MW
    assert with_storage["storage_energy_mwh"].iloc[-1] == pytest.approx(0.0, abs=SMALL_MW)


def test_stored_energy_follows_charging_and_discharging(with_storage):
    one_way_efficiency = STORAGE_ROUND_TRIP_EFFICIENCY**0.5
    stored_before_mwh = with_storage["storage_energy_mwh"].shift(1, fill_value=0.0)
    expected_mwh = (
        stored_before_mwh
        + with_storage["storage_charge_mw"] * one_way_efficiency
        - with_storage["storage_discharge_mw"] / one_way_efficiency
    )
    assert (with_storage["storage_energy_mwh"] - expected_mwh).abs().max() < SMALL_MW


def test_battery_never_charges_and_discharges_in_the_same_hour(with_storage):
    is_charging = with_storage["storage_charge_mw"] > SMALL_MW
    is_discharging = with_storage["storage_discharge_mw"] > SMALL_MW
    assert not (is_charging & is_discharging).any()


def test_auction_on_net_demand_costs_what_the_solver_found(inputs, with_storage):
    """The schedule comes from the solver and the prices from the auction; costs must match."""
    hourly, gas_plants = inputs
    cost_gbp_per_mwh, equations, targets, limits = build_dispatch_problem(
        hourly, gas_plants, STORAGE_POWER_MW, STORAGE_ENERGY_MWH, STORAGE_ROUND_TRIP_EFFICIENCY
    )
    solved = linprog(cost_gbp_per_mwh, A_eq=equations, b_eq=targets, bounds=limits)

    auction_cost_gbp = weekly_summary(with_storage)["system_cost_gbp"]
    assert auction_cost_gbp == pytest.approx(solved.fun, rel=1e-8)


def test_storage_lowers_system_cost_and_unserved_energy(without_storage, with_storage):
    before = weekly_summary(without_storage)
    after = weekly_summary(with_storage)
    assert after["system_cost_gbp"] < before["system_cost_gbp"]
    assert after["unserved_mwh"] == pytest.approx(37494, abs=1)
    assert after["shortfall_hours"] == 23
    assert after["system_cost_gbp"] == pytest.approx(335.104e6, rel=1e-5)


def test_size_sweep_matches_the_runs_without_and_with_the_modelled_battery(
    inputs, without_storage, with_storage
):
    hourly, gas_plants = inputs
    size_sweep = storage_size_sweep(
        hourly,
        gas_plants,
        power_mw_options=[0.0, STORAGE_POWER_MW],
        hours_at_full_power=STORAGE_ENERGY_MWH / STORAGE_POWER_MW,
        round_trip_efficiency=STORAGE_ROUND_TRIP_EFFICIENCY,
    )
    before = weekly_summary(without_storage)
    after = weekly_summary(with_storage)
    assert size_sweep.loc[0.0, "unserved_mwh"] == pytest.approx(before["unserved_mwh"])
    assert size_sweep.loc[0.0, "system_cost_gbp"] == pytest.approx(before["system_cost_gbp"])
    assert size_sweep.loc[STORAGE_POWER_MW, "unserved_mwh"] == pytest.approx(after["unserved_mwh"])


def test_shortfall_hours_with_storage_are_still_priced_at_voll(with_storage):
    is_shortfall = with_storage["unserved_mw"] > DISPATCH_TOLERANCE_MW
    assert (with_storage.loc[is_shortfall, "price_gbp_per_mwh"] == VOLL_GBP_PER_MWH).all()
    assert (with_storage.loc[~is_shortfall, "price_gbp_per_mwh"] < VOLL_GBP_PER_MWH).all()


def test_results_match_reference_copy(with_storage):
    reference = pd.read_csv(REPO_ROOT / "tests" / "golden" / "part2_hourly.csv", index_col="hour")
    pd.testing.assert_frame_equal(
        with_storage, reference, check_exact=False, rtol=0, atol=1e-2, check_dtype=False
    )
