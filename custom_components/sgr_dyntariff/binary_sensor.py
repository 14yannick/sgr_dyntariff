"""Binary sensor platform for the SmartGridready Dynamic Tariff integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_SUNRISE_BUFFER_HOURS, DEFAULT_SUNRISE_BUFFER_HOURS, DOMAIN, SUN_ENTITY_ID
from .coordinator import SgrTariffCoordinator
from .sensor import _current_slot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SgrTariffCoordinator = hass.data[DOMAIN][entry.entry_id]
    sunrise_buffer_hours = entry.options.get(
        CONF_SUNRISE_BUFFER_HOURS,
        entry.data.get(CONF_SUNRISE_BUFFER_HOURS, DEFAULT_SUNRISE_BUFFER_HOURS),
    )
    entity = SgrHigherPriceBeforeSunriseSensor(
        coordinator, entry, float(sunrise_buffer_hours)
    )
    async_add_entities([entity])

    @callback
    def _on_quarter_hour(_now: Any) -> None:
        """Refresh exactly on each 15-min slot boundary, same as sensor.py."""
        entity.async_schedule_update_ha_state(True)

    entry.async_on_unload(
        async_track_time_change(
            hass,
            _on_quarter_hour,
            minute=[0, 15, 30, 45],
            second=5,
        )
    )


class SgrHigherPriceBeforeSunriseSensor(
    CoordinatorEntity[SgrTariffCoordinator], BinarySensorEntity
):
    """On when a higher price than right now is still coming before sunrise.

    Before sunrise there's no solar production competing with a battery
    discharge, so a price spike still ahead in that window is a genuine
    "hold off and discharge then instead" signal -- e.g. for automations
    deciding whether to discharge a battery now or wait for a better slot
    overnight/early morning.
    """

    _attr_icon = "mdi:weather-sunset-up"
    _attr_has_entity_name = True
    _attr_name = "Higher price before sunrise"

    def __init__(
        self,
        coordinator: SgrTariffCoordinator,
        entry: ConfigEntry,
        sunrise_buffer_hours: float,
    ) -> None:
        super().__init__(coordinator)
        self._sunrise_buffer = timedelta(hours=sunrise_buffer_hours)
        self._attr_unique_id = f"{entry.entry_id}_higher_price_before_sunrise"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME) or entry.title,
            manufacturer="SmartGridready / VSE dynamic tariff",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get("url"),
        )

    @property
    def available(self) -> bool:
        return bool((self.coordinator.data or {}).get("slots"))

    def _next_sunrise(self):
        """Return the next sunrise, whether that's later today or tomorrow."""
        sun_state = self.hass.states.get(SUN_ENTITY_ID)
        if sun_state is None:
            return None
        return dt_util.parse_datetime(sun_state.attributes.get("next_rising") or "")

    def _window_end(self):
        """Return the end of the pre-sunrise window (next sunrise + buffer)."""
        next_sunrise = self._next_sunrise()
        if next_sunrise is None:
            return None
        return next_sunrise + self._sunrise_buffer

    def _peak_before_sunrise(self) -> tuple[float | None, dict | None, bool]:
        """Return (peak_price, peak_slot, has_data) for slots in the window."""
        window_end = self._window_end()
        if window_end is None:
            return None, None, False
        now = dt_util.utcnow()
        slots = [
            s
            for s in (self.coordinator.data or {}).get("slots", [])
            if now <= s["start"] < window_end
        ]
        if not slots:
            return None, None, False
        peak_slot = max(slots, key=lambda s: s["price"])
        return peak_slot["price"], peak_slot, True

    @property
    def is_on(self) -> bool | None:
        current_slot = _current_slot(self.coordinator.data)
        if current_slot is None:
            return None
        peak_price, _, has_data = self._peak_before_sunrise()
        if not has_data:
            return None
        return peak_price > current_slot["price"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current_slot = _current_slot(self.coordinator.data)
        peak_price, peak_slot, _ = self._peak_before_sunrise()
        next_sunrise = self._next_sunrise()
        window_end = self._window_end()
        return {
            "current_price": current_slot["price"] if current_slot else None,
            "peak_price_before_sunrise": peak_price,
            "peak_time_before_sunrise": (
                dt_util.as_local(peak_slot["start"]).isoformat() if peak_slot else None
            ),
            "next_sunrise": (
                dt_util.as_local(next_sunrise).isoformat() if next_sunrise else None
            ),
            "window_end": (
                dt_util.as_local(window_end).isoformat() if window_end else None
            ),
        }
