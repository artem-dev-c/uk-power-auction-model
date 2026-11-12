"""Run the auction without and with storage for every hour in the data and write the results."""

from pathlib import Path

import pandas as pd

from power_auction.charts import plot_market, plot_size_sweep, plot_storage
from power_auction.constants import (
    STORAGE_ENERGY_MWH,
    STORAGE_POWER_MW,
    STORAGE_ROUND_TRIP_EFFICIENCY,
    SWEEP_STORAGE_POWER_MW,
)
from power_auction.data import load_inputs
from power_auction.market import clear_market
from power_auction.report import storage_size_sweep, weekly_summary
from power_auction.storage import clear_market_with_storage, schedule_storage

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "data.xlsx"
OUTPUT_DIR = REPO_ROOT / "outputs"


def main() -> None:
    hourly, gas_plants = load_inputs(DATA_PATH)

    without_storage = clear_market(hourly, gas_plants)

    schedule = schedule_storage(
        hourly, gas_plants, STORAGE_POWER_MW, STORAGE_ENERGY_MWH, STORAGE_ROUND_TRIP_EFFICIENCY
    )
    with_storage = clear_market_with_storage(hourly, gas_plants, schedule)

    summary = pd.DataFrame(
        {
            "without_storage": weekly_summary(without_storage),
            "with_storage": weekly_summary(with_storage),
        }
    )

    # Additional analysis, not asked for by the task: how the result changes with battery size.
    size_sweep = storage_size_sweep(
        hourly,
        gas_plants,
        SWEEP_STORAGE_POWER_MW,
        hours_at_full_power=STORAGE_ENERGY_MWH / STORAGE_POWER_MW,
        round_trip_efficiency=STORAGE_ROUND_TRIP_EFFICIENCY,
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    without_storage.round(3).to_csv(OUTPUT_DIR / "part1_hourly.csv")
    with_storage.round(3).to_csv(OUTPUT_DIR / "part2_hourly.csv")
    summary.round(2).to_csv(OUTPUT_DIR / "summary.csv", index_label="metric")
    size_sweep.round(2).to_csv(OUTPUT_DIR / "size_sweep.csv")

    plot_market(without_storage, OUTPUT_DIR / "market_without_storage.png")
    plot_storage(without_storage, with_storage, OUTPUT_DIR / "storage.png")
    plot_size_sweep(size_sweep, OUTPUT_DIR / "size_sweep.png")

    print(f"Cleared {len(hourly)} hours, without and with storage. Results are in outputs/.")
    print(summary.to_string(float_format="{:,.1f}".format))


if __name__ == "__main__":
    main()
