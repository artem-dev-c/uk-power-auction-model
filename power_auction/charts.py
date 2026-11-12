"""The three charts shown in the README."""

from pathlib import Path

import matplotlib

# Draw straight to image files, so the program also runs on a machine with no display.
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

from power_auction.constants import (
    DISPATCH_TOLERANCE_MW,
    STORAGE_ENERGY_MWH,
    VOLL_GBP_PER_MWH,
)

MW_PER_GW = 1000.0

WIND_COLOUR = "#1baf7a"
SOLAR_COLOUR = "#eb6834"
GAS_COLOUR = "#2a78d6"
UNSERVED_COLOUR = "#d03b3b"
STORAGE_COLOUR = "#6250d6"
INK_COLOUR = "#0b0b0b"
MUTED_COLOUR = "#898781"
GRID_COLOUR = "#e1e0d9"
SURPLUS_SHADE = "#eda100"

HOURS_PER_DAY = 24

# Top of the price axis, just above the dearest gas bid in the data.
PRICE_AXIS_TOP_GBP_PER_MWH = 110


def _style(axis: plt.Axes, y_label: str) -> None:
    """Quiet grid and axes, so the data stands out."""
    axis.set_ylabel(y_label, color=MUTED_COLOUR)
    axis.grid(axis="y", color=GRID_COLOUR, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.tick_params(colors=MUTED_COLOUR)
    for side in ["top", "right", "left"]:
        axis.spines[side].set_visible(False)
    axis.spines["bottom"].set_color(GRID_COLOUR)


def _label_hours(axis: plt.Axes, n_hours: int) -> None:
    axis.set_xlim(0.5, n_hours + 0.5)
    axis.set_xticks(range(1, n_hours + 1, HOURS_PER_DAY))
    axis.set_xlabel("Hour of the week (one tick per day)", color=MUTED_COLOUR)


def _shade_hours(axis: plt.Axes, is_shaded: pd.Series, colour: str, label: str) -> None:
    """Shade the full height of the chart in the hours where is_shaded is true."""
    for hour in is_shaded.index[is_shaded]:
        axis.axvspan(hour - 0.5, hour + 0.5, color=colour, alpha=0.25, linewidth=0)
    # An empty patch that only puts one entry for all the shaded hours in the legend.
    axis.fill_between([], [], [], color=colour, alpha=0.25, linewidth=0, label=label)


def plot_market(without_storage: pd.DataFrame, path: Path) -> None:
    """Generation mix against demand, and the price, for the week without storage."""
    hours = without_storage.index
    wind_gw = without_storage["wind_mw"] / MW_PER_GW
    solar_gw = without_storage["solar_mw"] / MW_PER_GW
    gas_gw = without_storage["gas_mw"] / MW_PER_GW
    demand_gw = without_storage["demand_mw"] / MW_PER_GW
    supplied_gw = wind_gw + solar_gw + gas_gw
    is_shortfall = without_storage["unserved_mw"] > DISPATCH_TOLERANCE_MW
    is_surplus = without_storage["curtailed_mw"] > DISPATCH_TOLERANCE_MW

    figure, (mix_axis, price_axis) = plt.subplots(
        2, 1, figsize=(11, 6.5), sharex=True, height_ratios=[2, 1]
    )

    mix_axis.stackplot(
        hours,
        wind_gw,
        solar_gw,
        gas_gw,
        step="mid",
        colors=[WIND_COLOUR, SOLAR_COLOUR, GAS_COLOUR],
        labels=["Wind", "Solar", "Gas"],
    )
    mix_axis.fill_between(
        hours,
        supplied_gw,
        demand_gw,
        step="mid",
        color=UNSERVED_COLOUR,
        alpha=0.6,
        linewidth=0,
        label="Unserved demand",
    )
    mix_axis.step(hours, demand_gw, where="mid", color=INK_COLOUR, linewidth=1.2, label="Demand")
    _shade_hours(mix_axis, is_surplus, SURPLUS_SHADE, "Surplus hours (renewables curtailed)")
    mix_axis.set_title(
        f"Without storage: supply falls short in {is_shortfall.sum()} hours "
        f"and renewables are curtailed in {is_surplus.sum()}",
        loc="left",
        color=INK_COLOUR,
    )
    mix_axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=6, frameon=False)
    _style(mix_axis, "GW")

    # Shortfall hours are priced at the Value of Lost Load, at least 60 times any gas bid. On
    # one scale they would flatten everything else, so they are shaded and left off the line.
    price_outside_shortfall = without_storage["price_gbp_per_mwh"].mask(is_shortfall)
    price_axis.step(hours, price_outside_shortfall, where="mid", color=INK_COLOUR, linewidth=1.2)
    shortfall_label = f"Shortfall hours: price is £{VOLL_GBP_PER_MWH:,.0f}/MWh"
    _shade_hours(price_axis, is_shortfall, UNSERVED_COLOUR, shortfall_label)
    _shade_hours(price_axis, is_surplus, SURPLUS_SHADE, "Surplus hours: price is £0/MWh")
    price_axis.set_ylim(0, PRICE_AXIS_TOP_GBP_PER_MWH)
    price_axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=2, frameon=False)
    _style(price_axis, "Price, £/MWh")
    _label_hours(price_axis, len(hours))

    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def plot_storage(without_storage: pd.DataFrame, with_storage: pd.DataFrame, path: Path) -> None:
    """Unserved demand before and after, and what the battery holds, hour by hour."""
    hours = with_storage.index
    unserved_before_gw = without_storage["unserved_mw"] / MW_PER_GW
    unserved_after_gw = with_storage["unserved_mw"] / MW_PER_GW
    stored_gwh = with_storage["storage_energy_mwh"] / MW_PER_GW
    is_surplus = without_storage["curtailed_mw"] > DISPATCH_TOLERANCE_MW

    figure, (unserved_axis, stored_axis) = plt.subplots(2, 1, figsize=(11, 6), sharex=True)

    unserved_axis.fill_between(
        hours,
        0,
        unserved_before_gw,
        step="mid",
        color=UNSERVED_COLOUR,
        alpha=0.25,
        linewidth=0,
        label="Unserved demand without storage",
    )
    unserved_axis.fill_between(
        hours,
        0,
        unserved_after_gw,
        step="mid",
        color=UNSERVED_COLOUR,
        linewidth=0,
        label="Unserved demand with storage",
    )
    unserved_axis.set_title(
        "With storage: unserved demand before and after, and the energy the battery holds",
        loc="left",
        color=INK_COLOUR,
    )
    unserved_axis.legend(loc="upper left", frameon=False)
    _style(unserved_axis, "GW")

    stored_axis.fill_between(
        hours,
        0,
        stored_gwh,
        step="mid",
        color=STORAGE_COLOUR,
        alpha=0.8,
        linewidth=0,
        label="Energy stored in the battery",
    )
    _shade_hours(stored_axis, is_surplus, SURPLUS_SHADE, "Surplus hours without storage")
    stored_axis.set_ylim(0, STORAGE_ENERGY_MWH / MW_PER_GW * 1.25)
    stored_axis.legend(loc="upper left", ncol=2, frameon=False)
    _style(stored_axis, "GWh")
    _label_hours(stored_axis, len(hours))

    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def plot_size_sweep(size_sweep: pd.DataFrame, path: Path) -> None:
    """Unserved energy left over the week against the size of the battery."""
    energy_gwh = size_sweep["energy_mwh"] / MW_PER_GW
    unserved_gwh = size_sweep["unserved_mwh"] / MW_PER_GW
    is_modelled = size_sweep["energy_mwh"] == STORAGE_ENERGY_MWH

    figure, axis = plt.subplots(figsize=(7.5, 4.5))
    axis.plot(
        energy_gwh, unserved_gwh, color=UNSERVED_COLOUR, linewidth=2, marker="o", clip_on=False
    )
    axis.plot(
        energy_gwh[is_modelled],
        unserved_gwh[is_modelled],
        marker="o",
        markersize=13,
        markerfacecolor="none",
        markeredgecolor=INK_COLOUR,
        linestyle="none",
    )
    axis.annotate(
        "Battery modelled here",
        xy=(energy_gwh[is_modelled].iloc[0], unserved_gwh[is_modelled].iloc[0]),
        xytext=(14, 10),
        textcoords="offset points",
        color=INK_COLOUR,
    )
    axis.set_title(
        "Additional analysis: unserved energy by battery size", loc="left", color=INK_COLOUR
    )
    axis.set_xlabel("Battery energy capacity, GWh", color=MUTED_COLOUR)
    axis.set_ylim(bottom=0)
    _style(axis, "Unserved energy over the week, GWh")

    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)
