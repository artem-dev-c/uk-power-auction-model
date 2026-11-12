"""Load the workbook into two tidy tables and check it once."""

from pathlib import Path

import pandas as pd


def load_inputs(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (hourly, gas_plants).

    hourly is indexed by hour: demand_mw, gas_price_pence_per_therm, wind_available_mw,
    solar_available_mw. gas_plants is indexed by plant name: capacity_mw, efficiency.
    Every row is one hour, so a value in MW is also the energy in MWh for that hour.
    """
    sheets = pd.read_excel(path, sheet_name=None)

    # Building the table from separate tabs aligns them on hour. An hour missing from any
    # tab shows up as a NaN, which the validation below rejects.
    hourly = pd.DataFrame(
        {
            "demand_mw": sheets["demand"].set_index("hour")["demand"],
            "gas_price_pence_per_therm": sheets["gas_prices"].set_index("hour")["price"],
            "wind_available_mw": _available_mw(sheets["windplants"], sheets["wind_loadfactors"]),
            "solar_available_mw": _available_mw(sheets["solarplants"], sheets["solar_loadfactors"]),
        }
    )
    gas_plants = sheets["gasplants"].set_index("name").rename(columns={"capacity": "capacity_mw"})

    _validate(hourly, gas_plants)
    return hourly, gas_plants


def _available_mw(plants: pd.DataFrame, load_factors: pd.DataFrame) -> pd.Series:
    """Combined output available from one technology in each hour, in MW."""
    capacity_mw = plants.set_index("name")["capacity"]
    load_factors = load_factors.set_index("hour")

    # The multiplication at the end pairs each load factor column with the plant of the same
    # name, so the two lists of names must match exactly.
    if list(load_factors.columns) != list(capacity_mw.index):
        raise ValueError("load factor columns do not match the plant names")
    in_range = (load_factors >= 0) & (load_factors <= 1)
    if not in_range.all(axis=None):
        raise ValueError("load factors must be between 0 and 1, with no gaps")

    # Every plant of a technology bids the same £0, so the auction only needs their total.
    return (load_factors * capacity_mw).sum(axis=1)


def _validate(hourly: pd.DataFrame, gas_plants: pd.DataFrame) -> None:
    if hourly.isna().any(axis=None):
        raise ValueError("missing values, or hours not aligned across tabs")
    if list(hourly.index) != list(range(1, len(hourly) + 1)):
        raise ValueError("hours must run 1, 2, 3, ... with no gaps")
    if (hourly < 0).any(axis=None):
        raise ValueError("demand, gas price and available output cannot be negative")
    if (gas_plants["capacity_mw"] <= 0).any():
        raise ValueError("gas plant capacity must be positive")
    if not gas_plants["efficiency"].between(0, 1, inclusive="right").all():
        raise ValueError("gas plant efficiency must be above 0 and at most 1")
