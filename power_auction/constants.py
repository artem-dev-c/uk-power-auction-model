"""Unit conversions and modelling assumptions, in one place."""

THERMS_PER_MWH = 34.121
PENCE_PER_GBP = 100

RENEWABLE_BID_GBP_PER_MWH = 0.0

# Price in hours where all available supply cannot meet demand. The brief's price rule has no
# answer there, so we use the Value of Lost Load applied in GB imbalance pricing since 2018.
VOLL_GBP_PER_MWH = 6000.0

# When setting the price, a plant producing less than this counts as not running and unserved
# demand below it as zero. The storage schedule comes from a numerical solver and often puts
# demand exactly on the edge between two plants; without a tolerance, rounding noise would
# pick the price.
DISPATCH_TOLERANCE_MW = 1e-3

# Illustrative battery: about 7% of peak demand in power. The energy figure is what it can
# hold, four hours at full power before losses.
STORAGE_POWER_MW = 2000.0
STORAGE_ENERGY_MWH = 8000.0
STORAGE_ROUND_TRIP_EFFICIENCY = 0.85

# Additional analysis: battery sizes to compare, each with the same hours at full power and
# the same efficiency as the battery above.
SWEEP_STORAGE_POWER_MW = [0, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000]
