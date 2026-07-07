"""Constants for the SmartGridready Dynamic Tariff integration."""

DOMAIN = "sgr_dyntariff"

CONF_PRICE_COMPONENT = "price_component"
CONF_VAT = "vat"
CONF_SURCHARGE = "surcharge"
CONF_MINOR_UNIT = "display_minor_unit"

DEFAULT_NAME = "Dynamic Tariff"
DEFAULT_URL = "https://api.bkw.ch/api/dyntariffs/v1/Tariffs/energyreturn"
DEFAULT_COMPONENT = "feed_in"

# Price components defined by the VSE 2026 / SmartGridready TariffDto schema
PRICE_COMPONENTS = ["feed_in", "electricity", "grid", "integrated", "regional_fees"]

# Map API unit strings to Home-Assistant-friendly units
UNIT_MAP = {
    "CHF_kWh": "CHF/kWh",
    "Rp_kWh": "Rp./kWh",
    "EUR_kWh": "EUR/kWh",
    "ct_kWh": "ct/kWh",
}

# Applied when the minor-unit display toggle is on (e.g. CHF/kWh -> Rp./kWh)
MINOR_UNIT_MAP = {
    "CHF/kWh": "Rp./kWh",
    "EUR/kWh": "ct/kWh",
}

UPDATE_INTERVAL_MINUTES = 30
