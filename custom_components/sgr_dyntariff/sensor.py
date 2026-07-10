"""Sensor platform for the SmartGridready Dynamic Tariff integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_POWER_ENTITY,
    CONF_POWER_INVERT,
    CONF_PRICE_COMPONENT,
    DEFAULT_COMPONENT,
    DOMAIN,
)
from .coordinator import SgrTariffCoordinator


def _current_slot(data: dict | None) -> dict | None:
    """Return the slot covering the current instant, if any."""
    now = dt_util.utcnow()
    for slot in (data or {}).get("slots", []):
        if slot["start"] <= now < slot["end"]:
            return slot
    return None


def _today_slots(data: dict | None) -> list[dict]:
    """Return today's slots, in chronological order."""
    today = dt_util.now().date()
    slots = (data or {}).get("slots", [])
    return sorted(
        (s for s in slots if dt_util.as_local(s["start"]).date() == today),
        key=lambda s: s["start"],
    )


def _max_price_run(data: dict | None) -> tuple[float, dict, dict] | None:
    """Return (max_price, first_slot, last_slot) for today's first run at that price."""
    today_slots = _today_slots(data)
    if not today_slots:
        return None
    max_price = max(s["price"] for s in today_slots)

    start_slot = end_slot = None
    for slot in today_slots:
        if slot["price"] == max_price:
            if start_slot is None:
                start_slot = slot
            end_slot = slot
        elif start_slot is not None:
            break  # contiguous run at max_price ended

    return max_price, start_slot, end_slot


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SgrTariffCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        SgrPriceSensor(coordinator, entry),
        SgrMaxPriceTodaySensor(coordinator, entry),
        SgrMaxPriceSlotSensor(coordinator, entry, "start"),
        SgrMaxPriceSlotSensor(coordinator, entry, "end"),
    ]

    power_entity = entry.options.get(
        CONF_POWER_ENTITY, entry.data.get(CONF_POWER_ENTITY, "")
    ).strip()
    if power_entity:
        power_invert = entry.options.get(
            CONF_POWER_INVERT, entry.data.get(CONF_POWER_INVERT, False)
        )
        entities.append(
            SgrExportValueRateSensor(coordinator, entry, power_entity, bool(power_invert))
        )

    async_add_entities(entities)

    @callback
    def _on_quarter_hour(_now: Any) -> None:
        """Refresh the sensors exactly on each 15-min slot boundary."""
        for entity in entities:
            entity.async_schedule_update_ha_state(True)

    # Flip the state exactly on every quarter hour (15-min slot resolution)
    entry.async_on_unload(
        async_track_time_change(
            hass,
            _on_quarter_hour,
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

    @property
    def native_value(self) -> float | None:
        slot = _current_slot(self.coordinator.data)
        return slot["price"] if slot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slots = self._slots()
        tomorrow = dt_util.now().date() + timedelta(days=1)

        def fmt(items: list[dict]) -> list[dict]:
            return [
                {
                    "start": dt_util.as_local(s["start"]).isoformat(),
                    "end": dt_util.as_local(s["end"]).isoformat(),
                    "price": s["price"],
                }
                for s in items
            ]

        today_slots = _today_slots(self.coordinator.data)
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


class SgrExportValueRateSensor(CoordinatorEntity[SgrTariffCoordinator], SensorEntity):
    """Instantaneous earning rate (price/kWh x exported kW) from a power sensor."""

    _attr_icon = "mdi:cash-fast"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_name = "Export value rate"

    def __init__(
        self,
        coordinator: SgrTariffCoordinator,
        entry: ConfigEntry,
        power_entity: str,
        invert: bool,
    ) -> None:
        super().__init__(coordinator)
        self._power_entity = power_entity
        self._invert = invert
        self._attr_unique_id = f"{entry.entry_id}_export_value_rate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME) or entry.title,
            manufacturer="SmartGridready / VSE dynamic tariff",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get("url"),
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._power_entity], self._handle_power_change
            )
        )

    @callback
    def _handle_power_change(self, event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()

    @property
    def native_unit_of_measurement(self) -> str | None:
        unit = (self.coordinator.data or {}).get("unit")
        return unit.replace("/kWh", "/h") if unit else None

    @property
    def native_value(self) -> float | None:
        power_state = self.hass.states.get(self._power_entity)
        if power_state is None or power_state.state in ("unknown", "unavailable"):
            return None
        try:
            raw = float(power_state.state)
        except ValueError:
            return None

        slot = _current_slot(self.coordinator.data)
        if slot is None:
            return None

        # Default convention: negative power = exporting (matches most
        # inverters); the invert flag flips that assumption.
        sign = 1.0 if self._invert else -1.0
        export_kw = max(sign * raw, 0.0) / 1000.0
        return round(export_kw * slot["price"], 5)


class SgrMaxPriceTodaySensor(CoordinatorEntity[SgrTariffCoordinator], SensorEntity):
    """Highest price of today, with the time interval it applies to."""

    _attr_icon = "mdi:trending-up"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_name = "Max price today"

    def __init__(self, coordinator: SgrTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_max_price_today"
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

    @property
    def native_value(self) -> float | None:
        run = _max_price_run(self.coordinator.data)
        return run[0] if run else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        run = _max_price_run(self.coordinator.data)
        if run is None:
            return {"valid_from": None, "valid_until": None}
        _, start_slot, end_slot = run
        return {
            "valid_from": dt_util.as_local(start_slot["start"]).isoformat(),
            "valid_until": dt_util.as_local(end_slot["end"]).isoformat(),
        }


class SgrMaxPriceSlotSensor(CoordinatorEntity[SgrTariffCoordinator], SensorEntity):
    """Start or end of today's highest-price slot, for time-trigger automations.

    HA's time trigger accepts a sensor entity_id for its `at:` option as long
    as the state is a datetime, so these can be used directly, e.g.:

        trigger:
          - platform: time
            at: sensor.dynamic_tariff_max_price_start
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SgrTariffCoordinator,
        entry: ConfigEntry,
        edge: str,
    ) -> None:
        super().__init__(coordinator)
        self._edge = edge
        self._attr_icon = "mdi:clock-start" if edge == "start" else "mdi:clock-end"
        self._attr_name = f"Max price {edge}"
        self._attr_unique_id = f"{entry.entry_id}_max_price_{edge}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME) or entry.title,
            manufacturer="SmartGridready / VSE dynamic tariff",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get("url"),
        )

    @property
    def native_value(self):
        run = _max_price_run(self.coordinator.data)
        if run is None:
            return None
        _, start_slot, end_slot = run
        return start_slot["start"] if self._edge == "start" else end_slot["end"]
