"""SmartGridready Dynamic Tariff integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_PRICE_COMPONENT,
    CONF_SURCHARGE,
    CONF_VAT,
    DEFAULT_COMPONENT,
    DOMAIN,
)
from .coordinator import SgrTariffCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a dynamic tariff source from a config entry."""
    # Options (editable after setup) override initial data
    vat = entry.options.get(CONF_VAT, entry.data.get(CONF_VAT, 1.0))
    surcharge = entry.options.get(
        CONF_SURCHARGE, entry.data.get(CONF_SURCHARGE, 0.0)
    )

    coordinator = SgrTariffCoordinator(
        hass,
        url=entry.data[CONF_URL],
        component=entry.data.get(CONF_PRICE_COMPONENT, DEFAULT_COMPONENT),
        vat=float(vat),
        surcharge=float(surcharge),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
