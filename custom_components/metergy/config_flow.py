from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DEFAULT_COLD_WATER_ENABLED,
    DEFAULT_ELECTRICITY_ENABLED,
    DEFAULT_ELECTRICITY_LAG_DAYS,
    DEFAULT_HOT_WATER_ENABLED,
    DEFAULT_ROLLING_BACKFILL_DAYS,
    DEFAULT_WATER_LAG_DAYS,
    DOMAIN,
)


class MetergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Metergy."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            # Enforce unique entry per meter_id
            meter_id = str(user_input["meter_id"]).strip()
            await self.async_set_unique_id(meter_id)
            self._abort_if_unique_id_configured()
            title = f"Metergy {meter_id}"
            return self.async_create_entry(
                title=title, data={**user_input, "meter_id": meter_id}
            )

        schema = vol.Schema(
            {
                vol.Required("meter_id"): str,
                vol.Required("username"): str,
                vol.Required("password"): str,
                vol.Optional("electricity", default=DEFAULT_ELECTRICITY_ENABLED): bool,
                vol.Optional("cold_water", default=DEFAULT_COLD_WATER_ENABLED): bool,
                vol.Optional("hot_water", default=DEFAULT_HOT_WATER_ENABLED): bool,
                vol.Optional(
                    "electricity_lag", default=DEFAULT_ELECTRICITY_LAG_DAYS
                ): int,
                vol.Optional("water_lag", default=DEFAULT_WATER_LAG_DAYS): int,
                vol.Optional(
                    "rolling_backfill_days", default=DEFAULT_ROLLING_BACKFILL_DAYS
                ): int,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MetergyOptionsFlow(config_entry)


class MetergyOptionsFlow(config_entries.OptionsFlow):
    """Options flow to update cookie and flags without restart."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="Options", data=user_input)

        data = {**self._entry.data, **self._entry.options}
        schema = vol.Schema(
            {
                vol.Required("meter_id", default=data.get("meter_id", "")): str,
                vol.Required("username", default=data.get("username", "")): str,
                vol.Required("password", default=data.get("password", "")): str,
                vol.Optional(
                    "electricity",
                    default=data.get("electricity", DEFAULT_ELECTRICITY_ENABLED),
                ): bool,
                vol.Optional(
                    "cold_water",
                    default=data.get("cold_water", DEFAULT_COLD_WATER_ENABLED),
                ): bool,
                vol.Optional(
                    "hot_water",
                    default=data.get("hot_water", DEFAULT_HOT_WATER_ENABLED),
                ): bool,
                vol.Optional(
                    "electricity_lag",
                    default=data.get("electricity_lag", DEFAULT_ELECTRICITY_LAG_DAYS),
                ): int,
                vol.Optional(
                    "water_lag", default=data.get("water_lag", DEFAULT_WATER_LAG_DAYS)
                ): int,
                vol.Optional(
                    "rolling_backfill_days",
                    default=data.get(
                        "rolling_backfill_days", DEFAULT_ROLLING_BACKFILL_DAYS
                    ),
                ): int,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
