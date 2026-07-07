"""Config flow for the SmartGridready Dynamic Tariff integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientError, ClientResponseError

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_MINOR_UNIT,
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

_LOGGER = logging.getLogger(__name__)


async def _validate_source(
    hass: HomeAssistant,
    url: str,
    component: str,
) -> dict[str, Any]:
    """Fetch and parse the source once; return info or raise."""
    session = async_get_clientsession(hass)
    clean_url = url.strip()

    async with session.get(
        clean_url,
        headers={"Accept": "application/json"},
        timeout=30,
    ) as resp:
        if resp.status == 404:
            # Valid endpoint, cache currently empty -> accept.
            return {"slots": 0, "unit": None}

        if resp.status >= 400:
            body = await resp.text()
            _LOGGER.warning(
                "Tariff source returned HTTP %s for %s: %s",
                resp.status,
                clean_url,
                body[:500],
            )
            resp.raise_for_status()

        payload = await resp.json()

    data = parse_payload(payload, component, 1.0, 0.0)
    return {"slots": len(data["slots"]), "unit": data["unit"]}


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the config flow schema."""
    defaults = defaults or {}

    return vol.Schema(
        {
            vol.Required(
                CONF_NAME,
                default=defaults.get(CONF_NAME, DEFAULT_NAME),
            ): TextSelector(),
            vol.Required(
                CONF_URL,
                default=defaults.get(CONF_URL, DEFAULT_URL),
            ): TextSelector(),
            vol.Required(
                CONF_PRICE_COMPONENT,
                default=defaults.get(CONF_PRICE_COMPONENT, DEFAULT_COMPONENT),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=PRICE_COMPONENTS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_VAT,
                default=defaults.get(CONF_VAT, 1.0),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.5,
                    max=2.0,
                    step=0.001,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_SURCHARGE,
                default=defaults.get(CONF_SURCHARGE, 0.0),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=-1.0,
                    max=1.0,
                    step=0.0001,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_MINOR_UNIT,
                default=defaults.get(CONF_MINOR_UNIT, False),
            ): BooleanSelector(),
        }
    )


def _options_schema(config_entry: ConfigEntry) -> vol.Schema:
    """Return the options flow schema."""
    current_vat = config_entry.options.get(
        CONF_VAT,
        config_entry.data.get(CONF_VAT, 1.0),
    )
    current_surcharge = config_entry.options.get(
        CONF_SURCHARGE,
        config_entry.data.get(CONF_SURCHARGE, 0.0),
    )
    current_minor_unit = config_entry.options.get(
        CONF_MINOR_UNIT,
        config_entry.data.get(CONF_MINOR_UNIT, False),
    )

    return vol.Schema(
        {
            vol.Optional(
                CONF_VAT,
                default=current_vat,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0.5,
                    max=2.0,
                    step=0.001,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_SURCHARGE,
                default=current_surcharge,
            ): NumberSelector(
                NumberSelectorConfig(
                    min=-1.0,
                    max=1.0,
                    step=0.0001,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_MINOR_UNIT,
                default=current_minor_unit,
            ): BooleanSelector(),
        }
    )


class SgrDyntariffConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the GUI setup of a tariff source."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip()
            component = user_input[CONF_PRICE_COMPONENT]

            user_input = {
                **user_input,
                CONF_URL: url,
            }

            await self.async_set_unique_id(f"{url}::{component}")
            self._abort_if_unique_id_configured()

            try:
                await _validate_source(self.hass, url, component)
            except ClientResponseError as err:
                _LOGGER.warning(
                    "Tariff source validation failed with HTTP %s for %s",
                    err.status,
                    url,
                )
                errors["base"] = "cannot_connect"
            except (ClientError, TimeoutError, ValueError, KeyError, TypeError) as err:
                _LOGGER.warning(
                    "Tariff source validation failed for %s: %s",
                    url,
                    err,
                )
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error while validating tariff source")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Create the options flow."""
        return SgrDyntariffOptionsFlow()


class SgrDyntariffOptionsFlow(OptionsFlow):
    """Allow changing VAT and surcharge after setup."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Manage the integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.config_entry),
        )
