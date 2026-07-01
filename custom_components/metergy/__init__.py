from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_COLD_WATER_ENABLED,
    DEFAULT_ELECTRICITY_ENABLED,
    DEFAULT_ELECTRICITY_LAG_DAYS,
    DEFAULT_HOT_WATER_ENABLED,
    DEFAULT_ROLLING_BACKFILL_DAYS,
    DEFAULT_WATER_LAG_DAYS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


PLATFORMS: list[str] = []  # No entity platforms; we import external statistics


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Metergy from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Store options with defaults
    options = {**entry.data, **entry.options}
    hass.data[DOMAIN][entry.entry_id] = {
        "meter_id": options.get("meter_id"),
        "username": options.get("username"),
        "password": options.get("password"),
        "electricity": options.get("electricity", DEFAULT_ELECTRICITY_ENABLED),
        "cold_water": options.get("cold_water", DEFAULT_COLD_WATER_ENABLED),
        "hot_water": options.get("hot_water", DEFAULT_HOT_WATER_ENABLED),
        "electricity_lag": options.get("electricity_lag", DEFAULT_ELECTRICITY_LAG_DAYS),
        "water_lag": options.get("water_lag", DEFAULT_WATER_LAG_DAYS),
        "rolling_backfill_days": options.get(
            "rolling_backfill_days", DEFAULT_ROLLING_BACKFILL_DAYS
        ),
    }

    # Register coordinator and services
    from .coordinator import MetergyCoordinator  # import locally to avoid circular
    from .services import async_setup_services

    coordinator = MetergyCoordinator(hass, entry)
    await coordinator.async_setup()
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

    await async_setup_services(hass, entry)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data and (coordinator := data.get("coordinator")):
        await coordinator.async_unload()
    return True
