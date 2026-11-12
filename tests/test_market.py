from pathlib import Path

import pandas as pd
import pytest

from power_auction.constants import VOLL_GBP_PER_MWH
from power_auction.data import load_inputs
from power_auction.market import clear_market, gas_bids_gbp_per_mwh

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def result() -> pd.DataFrame:
    hourly, gas_plants = load_inputs(REPO_ROOT / "data" / "data.xlsx")
    return clear_market(
        hourly["demand_mw"],
        hourly["wind_available_mw"],
        hourly["solar_available_mw"],
        hourly["gas_price_pence_per_therm"],
        gas_plants,
    )


def test_gas_bid_converts_pence_per_therm_to_gbp_per_mwh():
    gas_price_pence_per_therm = pd.Series([70.0], index=[1])
    gas_plants = pd.DataFrame({"capacity_mw": [2500], "efficiency": [0.35]}, index=["gas1"])
    bids = gas_bids_gbp_per_mwh(gas_price_pence_per_therm, gas_plants)
    # 70p/therm = £0.70/therm; 34.121 therms per MWh of gas; 0.35 MWh of power per MWh of gas.
    assert bids.loc[1, "gas1"] == pytest.approx(0.70 * 34.121 / 0.35)


def test_small_market_covers_every_case():
    """Two gas plants of 100 MW each and 100 MW of renewables, worked out by hand."""
    hours = [1, 2, 3, 4, 5]
    demand_mw = pd.Series([80.0, 150.0, 200.0, 250.0, 350.0], index=hours)
    wind_available_mw = pd.Series(60.0, index=hours)
    solar_available_mw = pd.Series(40.0, index=hours)
    gas_price_pence_per_therm = pd.Series(100.0, index=hours)
    gas_plants = pd.DataFrame(
        {"capacity_mw": [100, 100], "efficiency": [0.25, 0.5]}, index=["dear", "cheap"]
    )
    cheap_bid = 34.121 / 0.5
    dear_bid = 34.121 / 0.25

    result = clear_market(
        demand_mw,
        wind_available_mw,
        solar_available_mw,
        gas_price_pence_per_therm,
        gas_plants,
    )

    # Hour 1: renewables exceed demand, so 20 MW is curtailed pro rata and the price is zero.
    assert result.loc[1, "price_gbp_per_mwh"] == 0.0
    assert result.loc[1, "wind_mw"] == pytest.approx(48.0)
    assert result.loc[1, "solar_mw"] == pytest.approx(32.0)
    assert result.loc[1, "curtailed_mw"] == pytest.approx(20.0)

    # Hour 2: the cheap plant is part-loaded and sets the price.
    assert result.loc[2, "price_setter"] == "cheap"
    assert result.loc[2, "price_gbp_per_mwh"] == pytest.approx(cheap_bid)
    assert result.loc[2, "gas_cost_gbp"] == pytest.approx(50 * cheap_bid)

    # Hour 3: demand is met exactly when the cheap plant is full. The brief's rule is the
    # lowest bid that fills demand, so the dear plant does not set the price.
    assert result.loc[3, "price_setter"] == "cheap"

    # Hour 4: the dear plant is needed and sets the price for everyone.
    assert result.loc[4, "price_setter"] == "dear"
    assert result.loc[4, "price_gbp_per_mwh"] == pytest.approx(dear_bid)
    assert result.loc[4, "gas_cost_gbp"] == pytest.approx(100 * cheap_bid + 50 * dear_bid)

    # Hour 5: everything runs and 50 MW of demand is still not served.
    assert result.loc[5, "price_setter"] == "shortfall"
    assert result.loc[5, "price_gbp_per_mwh"] == VOLL_GBP_PER_MWH
    assert result.loc[5, "unserved_mw"] == pytest.approx(50.0)


def test_hour_1_most_expensive_gas_plant_sets_the_price(result):
    hour = result.loc[1]
    assert hour["wind_mw"] == pytest.approx(4620.6, abs=0.05)
    assert hour["solar_mw"] == 0.0
    # The four cheaper plants are full (15,000 MW) and gas1 supplies the last 57.4 MW.
    assert hour["gas_mw"] == pytest.approx(15057.4, abs=0.05)
    assert hour["price_setter"] == "gas1"
    assert hour["price_gbp_per_mwh"] == pytest.approx(68.24, abs=0.005)


def test_hour_84_surplus_renewables_are_curtailed_at_zero_price(result):
    hour = result.loc[84]
    assert hour["wind_mw"] + hour["solar_mw"] == pytest.approx(24169.0)
    assert hour["gas_mw"] == 0.0
    assert hour["curtailed_mw"] == pytest.approx(6114.0, abs=0.05)
    assert hour["price_setter"] == "renewables"
    assert hour["price_gbp_per_mwh"] == 0.0


def test_hour_120_shortfall_is_priced_at_voll(result):
    hour = result.loc[120]
    assert hour["wind_mw"] == pytest.approx(2542.8, abs=0.05)
    assert hour["gas_mw"] == pytest.approx(17500.0)
    assert hour["unserved_mw"] == pytest.approx(1371.7, abs=0.05)
    assert hour["price_setter"] == "shortfall"
    assert hour["price_gbp_per_mwh"] == VOLL_GBP_PER_MWH


def test_hours_by_price_setter(result):
    counts = result["price_setter"].value_counts().to_dict()
    assert counts == {
        "renewables": 11,
        "gas5": 16,
        "gas4": 33,
        "gas3": 30,
        "gas2": 32,
        "gas1": 17,
        "shortfall": 29,
    }


def test_shortfall_and_surplus_hours(result):
    shortfall_hours = list(result.index[result["unserved_mw"] > 0])
    surplus_hours = list(result.index[result["curtailed_mw"] > 0])
    assert shortfall_hours == [*range(19, 22), *range(42, 47), *range(114, 129), *range(163, 169)]
    assert surplus_hours == [*range(81, 89), 134, 135, 158]
    assert result["unserved_mw"].sum() == pytest.approx(60326, abs=1)
    assert result["curtailed_mw"].sum() == pytest.approx(35969, abs=1)


def test_supply_plus_unserved_equals_demand_every_hour(result):
    supplied_mw = result["wind_mw"] + result["solar_mw"] + result["gas_mw"]
    difference_mw = supplied_mw + result["unserved_mw"] - result["demand_mw"]
    assert difference_mw.abs().max() < 1e-6


def test_results_match_saved_output(result):
    saved = pd.read_csv(REPO_ROOT / "outputs" / "part1_hourly.csv", index_col="hour")
    pd.testing.assert_frame_equal(result, saved, check_exact=False, atol=1e-3, check_dtype=False)
