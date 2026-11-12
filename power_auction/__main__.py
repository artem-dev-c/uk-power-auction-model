"""Run the auction for every hour in the data and write the results."""

from pathlib import Path

from power_auction.data import load_inputs
from power_auction.market import clear_market

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "data.xlsx"
OUTPUT_DIR = REPO_ROOT / "outputs"


def main() -> None:
    hourly, gas_plants = load_inputs(DATA_PATH)
    result = clear_market(
        hourly["demand_mw"],
        hourly["wind_available_mw"],
        hourly["solar_available_mw"],
        hourly["gas_price_pence_per_therm"],
        gas_plants,
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / "part1_hourly.csv"
    result.round(3).to_csv(output_path)

    generation_mwh = result[["wind_mw", "solar_mw", "gas_mw"]].sum()
    mix_percent = (generation_mwh / generation_mwh.sum() * 100).round(1)
    is_shortfall = result["price_setter"] == "shortfall"
    mean_price_gbp_per_mwh = result.loc[~is_shortfall, "price_gbp_per_mwh"].mean()

    print(f"Cleared {len(result)} hours -> {output_path.relative_to(REPO_ROOT)}")
    print(f"Mix over the week (%): {mix_percent.to_dict()}")
    print(f"Hours by price setter: {result['price_setter'].value_counts().to_dict()}")
    print(f"Mean price outside shortfall hours: {mean_price_gbp_per_mwh:.2f} GBP/MWh")
    print(f"Unserved energy: {result['unserved_mw'].sum():,.0f} MWh")
    print(f"Curtailed energy: {result['curtailed_mw'].sum():,.0f} MWh")


if __name__ == "__main__":
    main()
