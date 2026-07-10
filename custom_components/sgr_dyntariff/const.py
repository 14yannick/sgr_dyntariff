"""Constants for the SmartGridready Dynamic Tariff integration."""

DOMAIN = "sgr_dyntariff"

CONF_PRICE_COMPONENT = "price_component"
CONF_VAT = "vat"
CONF_SURCHARGE = "surcharge"
CONF_POWER_ENTITY = "power_entity"
CONF_POWER_INVERT = "power_invert"

DEFAULT_NAME = "Dynamic Tariff"
DEFAULT_URL = "https://api.bkw.ch/api/dyntariffs/v1/Tariffs/energyreturn"
DEFAULT_COMPONENT = "feed_in"

# Price components defined by the VSE 2026 / SmartGridready TariffDto schema
PRICE_COMPONENTS = ["feed_in", "electricity", "grid", "integrated", "regional_fees"]

# Map API unit strings to Home-Assistant-friendly units. Per the VSE 2026
# PriceUnit enum, price components are always CHF-denominated (the enum's
# 24 values only vary the billing period, e.g. CHF_kWh, CHF_kW_1h, CHF_d --
# there is no non-CHF currency in the v1 spec).
UNIT_MAP = {
    "CHF_kWh": "CHF/kWh",
}

UPDATE_INTERVAL_MINUTES = 30
