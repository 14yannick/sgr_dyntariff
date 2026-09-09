"""Sensor platform for the SmartGridready Dynamic Tariff integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable

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
    CONF_SUNRISE_BUFFER_HOURS,
    DEFAULT_COMPONENT,
    DEFAULT_SUNRISE_BUFFER_HOURS,
    DOMAIN,
    SUN_ENTITY_ID,
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


def _price_extreme_run(
    data: dict | None, pick: Callable[[list[float]], float]
) -> tuple[float, dict, dict] | None:
    """Return (price, first_slot, last_slot) for today's first run at the picked price."""
    today_slots = _today_slots(data)
    if not today_slots:
        return None
    target_price = pick([s["price"] for s in today_slots])

    start_slot = end_slot = None
    for slot in today_slots:
        if slot["price"] == target_price:
            if start_slot is None:
                start_slot = slot
            end_slot = slot
        elif start_slot is not None:
            break  # contiguous run at target_price ended

    return target_price, start_slot, end_slot


def _max_price_run(data: dict | None) -> tuple[float, dict, dict] | None:
    """Return (max_price, first_slot, last_slot) for today's first run at that price."""
    return _price_extreme_run(data, max)


def _min_price_run(data: dict | None) -> tuple[float, dict, dict] | None:
    """Return (min_price, first_slot, last_slot) for today's first run at that price."""
    return _price_extreme_run(data, min)


def _next_sunrise(hass: HomeAssistant):
    """Return the next sunrise, whether that's later today or tomorrow."""
    sun_state = hass.states.get(SUN_ENTITY_ID)
    if sun_state is None:
        return None
    return dt_util.parse_datetime(sun_state.attributes.get("next_rising") or "")


def _relevant_sunrise(hass: HomeAssistant):
    """Return the sunrise the pre-sunrise buffer window should be anchored to.

    `sun.sun`'s next_rising always points to the *next* sunrise and flips
    forward to tomorrow the instant today's sunrise actually happens -- so
    using it as-is would make the buffer window balloon out to ~24h right
    at that crossing instead of just covering the intended buffer past
    sunrise. If next_rising is nearly a full day away, today's sunrise has
    already passed, so step it back ~24h to recover today's actual sunrise
    (day length shifts by only minutes day to day, well within a
    buffer-sized margin of error).
    """
    next_sunrise = _next_sunrise(hass)
    if next_sunrise is None:
        return None
    if next_sunrise - dt_util.utcnow() > timedelta(hours=20):
        return next_sunrise - timedelta(hours=24)
    return next_sunrise


def _peak_before_sunrise(
    hass: HomeAssistant, data: dict | None, buffer: timedelta
) -> tuple[float, dict] | None:
    """Return (peak_price, peak_slot) for the best slot before sunrise + buffer.

    The day's overall max (see _max_price_run) can land in the evening, which
    is the wrong target for a "discharge before solar takes over" automation
    -- this scans only the window up to sunrise (plus a buffer for solar's
    ramp-up) instead.
    """
    sunrise = _relevant_sunrise(hass)
    if sunrise is None:
        return None
    window_end = sunrise + buffer
    now = dt_util.utcnow()
    slots = [s for s in (data or {}).get("slots", []) if now <= s["start"] < window_end]
    if not slots:
        return None
    peak_slot = max(slots, key=lambda s: s["price"])
    return peak_slot["price"], peak_slot


class _SgrCoordinatorEntity(CoordinatorEntity[SgrTariffCoordinator], SensorEntity):
    """Shared base that stays available as long as slot data is cached.

    CoordinatorEntity's default `available` tracks `last_update_success`,
    which would flip every sensor to unavailable on a single transient fetch
    failure -- even though the coordinator still holds perfectly usable
    cached slots from the last successful fetch (the merge in
    `_async_update_data` never discards them). Basing availability on the
    cache itself means a one-off bad fetch no longer blanks the sensors;
    `native_value` still correctly returns None/unknown if the cache has no
    slot covering the current moment.
    """

    @property
    def available(self) -> bool:
        return bool((self.coordinator.data or {}).get("slots"))


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

    entities: list[SensorEntity] = [
        SgrPriceSensor(coordinator, entry),
        SgrPriceExtremeTodaySensor(coordinator, entry, "max"),
        SgrPriceExtremeTodaySensor(coordinator, entry, "min"),
        SgrPriceExtremeSlotSensor(coordinator, entry, "max", "start"),
        SgrPriceExtremeSlotSensor(coordinator, entry, "max", "end"),
        SgrPriceExtremeSlotSensor(coordinator, entry, "min", "start"),
        SgrPriceExtremeSlotSensor(coordinator, entry, "min", "end"),
        SgrPeakBeforeSunriseSensor(coordinator, entry, float(sunrise_buffer_hours)),
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


class SgrPriceSensor(_SgrCoordinatorEntity):
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


class SgrExportValueRateSensor(_SgrCoordinatorEntity):
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


class SgrPriceExtremeTodaySensor(_SgrCoordinatorEntity):
    """Highest or lowest price of today, with the time interval it applies to."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SgrTariffCoordinator, entry: ConfigEntry, kind: str
    ) -> None:
        super().__init__(coordinator)
        self._run_fn = _max_price_run if kind == "max" else _min_price_run
        self._attr_icon = "mdi:trending-up" if kind == "max" else "mdi:trending-down"
        self._attr_name = f"{'Max' if kind == 'max' else 'Min'} price today"
        self._attr_unique_id = f"{entry.entry_id}_{kind}_price_today"
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
        run = self._run_fn(self.coordinator.data)
        return run[0] if run else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        run = self._run_fn(self.coordinator.data)
        if run is None:
            return {"valid_from": None, "valid_until": None}
        _, start_slot, end_slot = run
        return {
            "valid_from": dt_util.as_local(start_slot["start"]).isoformat(),
            "valid_until": dt_util.as_local(end_slot["end"]).isoformat(),
        }


class SgrPriceExtremeSlotSensor(_SgrCoordinatorEntity):
    """Start or end of today's highest/lowest-price slot, for time-trigger automations.

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
        kind: str,
        edge: str,
    ) -> None:
        super().__init__(coordinator)
        self._run_fn = _max_price_run if kind == "max" else _min_price_run
        self._edge = edge
        label = "Max" if kind == "max" else "Min"
        self._attr_icon = "mdi:clock-start" if edge == "start" else "mdi:clock-end"
        self._attr_name = f"{label} price {edge}"
        self._attr_unique_id = f"{entry.entry_id}_{kind}_price_{edge}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME) or entry.title,
            manufacturer="SmartGridready / VSE dynamic tariff",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get("url"),
        )

    @property
    def native_value(self):
        run = self._run_fn(self.coordinator.data)
        if run is None:
            return None
        _, start_slot, end_slot = run
        return start_slot["start"] if self._edge == "start" else end_slot["end"]


class SgrPeakBeforeSunriseSensor(_SgrCoordinatorEntity):
    """Start time of the best-priced slot before the next sunrise (+ buffer).

    The day's overall max/min (see SgrPriceExtremeSlotSensor) can land in
    the evening, which is the wrong trigger for a "discharge before solar
    takes over" morning automation -- this is the pre-sunrise-window
    equivalent, usable the same way as an `at:` target in a time trigger.
    See also the "Higher price before sunrise" binary sensor for the
    yes/no signal on whether it's worth holding off an evening discharge
    for this slot instead.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:weather-sunset-up"
    _attr_has_entity_name = True
    _attr_name = "Peak time before sunrise"

    def __init__(
        self,
        coordinator: SgrTariffCoordinator,
        entry: ConfigEntry,
        sunrise_buffer_hours: float,
    ) -> None:
        super().__init__(coordinator)
        self._buffer = timedelta(hours=sunrise_buffer_hours)
        self._attr_unique_id = f"{entry.entry_id}_peak_time_before_sunrise"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME) or entry.title,
            manufacturer="SmartGridready / VSE dynamic tariff",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=entry.data.get("url"),
        )

    @property
    def native_value(self):
        run = _peak_before_sunrise(self.hass, self.coordinator.data, self._buffer)
        return run[1]["start"] if run else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        run = _peak_before_sunrise(self.hass, self.coordinator.data, self._buffer)
        return {"price": run[0] if run else None}
