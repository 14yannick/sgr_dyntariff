"""Config flow for the SmartGridready Dynamic Tariff integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_PRICE_COMPONENT,
    CONF_SURCHARGE,
    CONF_VAT,
    DEFAULT_COMPONENT,
    DEFAULT_NAME,
    DEFAULT_URL,
    DOMAIN,
    PRICE_COMPONENTS,
)
from .coordinator import parse_payload


async def _validate_source(
    hass: HomeAssistant, url: str, component: str
) -> dict[str, Any]:
    """Fetch and parse the source once; return info or raise."""
    session = async_get_clientsession(hass)
    resp = await session.get(url, headers={"Accept": "application/json"}, timeout=30)
    if resp.status == 404:
        # Valid endpoint, cache currently empty -> accept
        return {"slots": 0, "unit": None}
    resp.raise_for_status()
    payload = await resp.json()
    data = parse_payload(payload, component, 1.0, 0.0)
    return {"slots": len(data["slots"]), "unit": data["unit"]}


def _user_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)
            ): TextSelector(),
            vol.Required(
                CONF_URL, default=defaults.get(CONF_URL, DEFAULT_URL)
            ): TextSelector(),
            vol.Required(
                CONF_PRICE_COMPONENT,
                default=defaults.get(CONF_PRICE_COMPONENT, DEFAULT_COMPONENT),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=PRICE_COMPONENTS,
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="price_component",
                )
            ),
            vol.Optional(CONF_VAT, default=defaults.get(CONF_VAT, 1.0)): NumberSelector(
                NumberSelectorConfig(
                    min=0.5, max=2.0, step=0.001, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_SURCHARGE, default=defaults.get(CONF_SURCHARGE, 0.0)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=-1.0, max=1.0, step=0.0001, mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


class SgrDyntariffConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the GUI setup of a tariff source."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            # One entry per (url, component) pair
            await self.async_set_unique_id(
                f"{user_input[CONF_URL]}::{user_input[CONF_PRICE_COMPONENT]}"
            )
            self._abort_if_unique_id_configured()

            try:
                info = await _validate_source(
                    self.hass,
                    user_input[CONF_URL],
                    user_input[CONF_PRICE_COMPONENT],
                )
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                # info["slots"] == 0 is accepted: endpoint valid but the
                # provider cache may be temporarily empty (404 case).
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SgrDyntariffOptionsFlow()


class SgrDyntariffOptionsFlow(OptionsFlow):
    """Allow changing VAT and surcharge after setup."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        entry = self.config_entry
        current_vat = entry.options.get(CONF_VAT, entry.data.get(CONF_VAT, 1.0))
        current_surcharge = entry.options.get(
            CONF_SURCHARGE, entry.data.get(CONF_SURCHARGE, 0.0)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_VAT, default=current_vat): NumberSelector(
                        NumberSelectorConfig(
                            min=0.5, max=2.0, step=0.001, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_SURCHARGE, default=current_surcharge
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=-1.0, max=1.0, step=0.0001,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
