"""Sensor platform for the SmartGridready Dynamic Tariff integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_PRICE_COMPONENT, DEFAULT_COMPONENT, DOMAIN
from .coordinator import SgrTariffCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SgrTariffCoordinator = hass.data[DOMAIN][entry.entry_id]
    entity = SgrPriceSensor(coordinator, entry)
    async_add_entities([entity])

    # Flip the state exactly on every quarter hour (15-min slot resolution)
    entry.async_on_unload(
        async_track_time_change(
            hass,
            lambda _now: entity.async_schedule_update_ha_state(True),
            minute=[0, 15, 30, 45],
            second=5,
        )
    )


class SgrPriceSensor(CoordinatorEntity[SgrTariffCoordinator], SensorEntity):
    """Current dynamic tariff price with forecast attributes."""

    _attr_icon = "mdi:transmission-tower"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_name = None  # entity takes the device (= entry) name

    def __init__(self, coordinator: SgrTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._component = entry.data.get(CONF_PRICE_COMPONENT, DEFAULT_COMPONENT)
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME) or entry.title,
            manufacturer="SmartGridready / VSE dynamic tariff",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get("url"),
        )

    @property
    def native_unit_of_measurement(self) -> str | None:
        return (self.coordinator.data or {}).get("unit")

    def _slots(self) -> list[dict]:
        return (self.coordinator.data or {}).get("slots", [])

    def _current_slot(self) -> dict | None:
        now = dt_util.utcnow()
        for slot in self._slots():
            if slot["start"] <= now < slot["end"]:
                return slot
        return None

    @property
    def native_value(self) -> float | None:
        slot = self._current_slot()
        return slot["price"] if slot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slots = self._slots()
        now = dt_util.now()
        today = now.date()
        tomorrow = today + timedelta(days=1)

        def fmt(items: list[dict]) -> list[dict]:
            return [
                {
                    "start": dt_util.as_local(s["start"]).isoformat(),
                    "end": dt_util.as_local(s["end"]).isoformat(),
                    "price": s["price"],
                }
                for s in items
            ]

        today_slots = [s for s in slots if dt_util.as_local(s["start"]).date() == today]
        tomorrow_slots = [
            s for s in slots if dt_util.as_local(s["start"]).date() == tomorrow
        ]
        today_prices = [s["price"] for s in today_slots]

        return {
            "price_component": self._component,
            "publication_timestamp": (self.coordinator.data or {}).get(
                "publication_timestamp"
            ),
            "today": fmt(today_slots),
            "tomorrow": fmt(tomorrow_slots),
            "tomorrow_valid": bool(tomorrow_slots),
            "min_today": min(today_prices) if today_prices else None,
            "max_today": max(today_prices) if today_prices else None,
            "average_today": (
                round(sum(today_prices) / len(today_prices), 4)
                if today_prices
                else None
            ),
        }
