"""DataUpdateCoordinator for SmartGridready dynamic tariffs.

Fetches and parses tariff data following the VSE 2026 / SmartGridready
TariffDto schema:

    {
      "publication_timestamp": "...",
      "prices": [
        {
          "start_timestamp": "2026-07-06T10:00:00Z",   # ISO 8601, UTC
          "end_timestamp":   "2026-07-06T10:15:00Z",
          "feed_in":     [ {"unit": "CHF_kWh", "value": 0.041} ],
          "electricity": [...], "grid": [...],
          "integrated": [...], "regional_fees": [...]
        }, ...
      ]
    }

Providers typically publish the next day's prices in the evening and may
then return ONLY the new day. The coordinator therefore merges every
fetch into a slot cache and keeps recent slots, so "today" stays known
after the evening publication.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, PRICE_COMPONENTS, UNIT_MAP, UPDATE_INTERVAL_MINUTES

_LOGGER = logging.getLogger(__name__)


def _parse_timestamp(raw: Any):
    """Parse an ISO 8601 timestamp; per spec values are UTC."""
    if raw is None:
        return None
    ts = dt_util.parse_datetime(str(raw))
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt_util.UTC)
    return ts


def parse_payload(payload: Any, component: str, vat: float, surcharge: float) -> dict:
    """Convert a TariffDto payload into normalized slot data."""
    if not isinstance(payload, dict) or "prices" not in payload:
        raise UpdateFailed(f"Unexpected payload (no 'prices' key): {payload!r:.200}")

    slots: list[dict] = []
    unit: str | None = None

    for item in payload.get("prices") or []:
        start = _parse_timestamp(item.get("start_timestamp"))
        end = _parse_timestamp(item.get("end_timestamp"))
        if start is None:
            _LOGGER.debug("Skipping slot without start_timestamp: %s", item)
            continue
        if end is None:
            end = start + timedelta(minutes=15)

        # Preferred component, with fallback to any component that has data
        components = item.get(component) or []
        if not components:
            for alt in PRICE_COMPONENTS:
                if item.get(alt):
                    components = item[alt]
                    _LOGGER.debug(
                        "Slot %s has no '%s' price, using '%s'", start, component, alt
                    )
                    break
        if not components:
            continue

        comp = components[0]
        value = comp.get("value")
        if value is None:
            continue
        if unit is None:
            raw_unit = comp.get("unit")
            unit = UNIT_MAP.get(raw_unit, raw_unit)

        slots.append(
            {
                "start": start,
                "end": end,
                "price": round(float(value) * vat + surcharge, 4),
            }
        )

    slots.sort(key=lambda s: s["start"])

    return {
        "slots": slots,
        "unit": unit,
        "publication_timestamp": payload.get("publication_timestamp"),
    }


class SgrTariffCoordinator(DataUpdateCoordinator[dict]):
    """Coordinator fetching one dynamic tariff source."""

    def __init__(
        self,
        hass: HomeAssistant,
        url: str,
        component: str,
        vat: float,
        surcharge: float,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {url}",
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self._session = async_get_clientsession(hass)
        self._url = url
        self._component = component
        self._vat = vat
        self._surcharge = surcharge
        self._slot_cache: dict[str, dict] = {}

    async def _async_update_data(self) -> dict:
        try:
            # Note: per spec, endpoints reject query parameters with 400,
            # so the URL is used exactly as configured.
            resp = await self._session.get(
                self._url, headers={"Accept": "application/json"}, timeout=30
            )
            if resp.status == 404:
                raise UpdateFailed("API: no tariff data currently available (404)")
            resp.raise_for_status()
            payload = await resp.json()
        except UpdateFailed:
            raise
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Error fetching tariffs: {err}") from err

        data = parse_payload(payload, self._component, self._vat, self._surcharge)

        # Merge into cache (new data wins per slot); prune old slots.
        for slot in data["slots"]:
            self._slot_cache[slot["start"].isoformat()] = slot
        cutoff = dt_util.utcnow() - timedelta(days=2)
        for key in [k for k, s in self._slot_cache.items() if s["end"] < cutoff]:
            del self._slot_cache[key]

        data["slots"] = sorted(self._slot_cache.values(), key=lambda s: s["start"])
        return data
