"""Config flow for the SmartGridready Dynamic Tariff integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientError, ClientResponseError

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from .const import (
    CONF_POWER_ENTITY,
    CONF_POWER_INVERT,
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
    """Fetch and parse the source once."""
    session = async_get_clientsession(hass)
    clean_url = url.strip()

    async with session.get(
        clean_url,
        headers={"Accept": "application/json"},
        timeout=30,
    ) as resp:
        if resp.status == 404:
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
            ): str,
            vol.Required(
                CONF_URL,
                default=defaults.get(CONF_URL, DEFAULT_URL),
            ): str,
            vol.Required(
                CONF_PRICE_COMPONENT,
                default=defaults.get(CONF_PRICE_COMPONENT, DEFAULT_COMPONENT),
            ): vol.In(PRICE_COMPONENTS),
            vol.Optional(
                CONF_VAT,
                default=defaults.get(CONF_VAT, 1.0),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=2.0)),
            vol.Optional(
                CONF_SURCHARGE,
                default=defaults.get(CONF_SURCHARGE, 0.0),
            ): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
            vol.Optional(
                CONF_POWER_ENTITY,
                default=defaults.get(CONF_POWER_ENTITY, ""),
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_POWER_INVERT,
                default=defaults.get(CONF_POWER_INVERT, False),
            ): bool,
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
    current_power_entity = config_entry.options.get(
        CONF_POWER_ENTITY,
        config_entry.data.get(CONF_POWER_ENTITY, ""),
    )
    current_power_invert = config_entry.options.get(
        CONF_POWER_INVERT,
        config_entry.data.get(CONF_POWER_INVERT, False),
    )

    return vol.Schema(
        {
            vol.Optional(
                CONF_VAT,
                default=current_vat,
            ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=2.0)),
            vol.Optional(
                CONF_SURCHARGE,
                default=current_surcharge,
            ): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
            vol.Optional(
                CONF_POWER_ENTITY,
                default=current_power_entity,
            ): EntitySelector(EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_POWER_INVERT,
                default=current_power_invert,
            ): bool,
        }
    )


class SgrDyntariffConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the GUI setup of a tariff source."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            url = user_input[CONF_URL].strip()
            component = user_input[CONF_PRICE_COMPONENT]
            power_entity = (user_input.get(CONF_POWER_ENTITY) or "").strip()

            user_input = {
                **user_input,
                CONF_NAME: name,
                CONF_URL: url,
                CONF_POWER_ENTITY: power_entity,
            }

            if power_entity and self.hass.states.get(power_entity) is None:
                errors["base"] = "power_entity_not_found"
                return self.async_show_form(
                    step_id="user",
                    data_schema=_user_schema(user_input),
                    errors=errors,
                )

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
                return self.async_create_entry(title=name, data=user_input)

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
    """Allow changing VAT, surcharge, and display options after setup."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the integration options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            power_entity = (user_input.get(CONF_POWER_ENTITY) or "").strip()
            user_input = {**user_input, CONF_POWER_ENTITY: power_entity}

            if power_entity and self.hass.states.get(power_entity) is None:
                errors["base"] = "power_entity_not_found"
            else:
                return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.config_entry),
            errors=errors,
        )