"""Unit conversions and modelling assumptions, in one place."""

THERMS_PER_MWH = 34.121
PENCE_PER_GBP = 100

RENEWABLE_BID_GBP_PER_MWH = 0.0

# Price in hours where all available supply cannot meet demand. The brief's price rule has no
# answer there, so we use the Value of Lost Load applied in GB imbalance pricing since 2018.
VOLL_GBP_PER_MWH = 6000.0
