from __future__ import annotations

from datetime import date, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change

from .api import MetergyClient
from .const import (
    DEFAULT_ROLLING_BACKFILL_DAYS,
    DOMAIN,
    EVENT_IMPORT_COMPLETED,
    EVENT_IMPORT_FAILED,
    EVENT_IMPORT_STARTED,
    STAT_ID_COLD_WATER,
    STAT_ID_HOT_WATER,
)
from .importer import import_electricity_daily_hourly, import_water_daily

_LOGGER = logging.getLogger(__name__)


class MetergyCoordinator:
    """Schedules nightly import based on configured lag."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub = None

    async def async_setup(self) -> None:
        @callback
        async def _midnight_job(now):  # noqa: ANN001
            _LOGGER.debug("Scheduled nightly import triggering at %s", now)
            await self.async_run_import()

        # Run every day at 03:10 local time to allow provider settlement
        self._unsub = async_track_time_change(
            self.hass, _midnight_job, hour=3, minute=10, second=0
        )

    async def async_unload(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def async_run_import(self) -> None:
        data = self.hass.data[DOMAIN][self.entry.entry_id]
        meter_id: str = data["meter_id"]
        username: str = data["username"]
        password: str = data["password"]
        electricity: bool = data["electricity"]
        cold_water: bool = data["cold_water"]
        hot_water: bool = data["hot_water"]
        elec_lag: int = data["electricity_lag"]
        water_lag: int = data["water_lag"]
        backfill_days: int = int(
            data.get("rolling_backfill_days", DEFAULT_ROLLING_BACKFILL_DAYS)
        )
        if backfill_days < 1:
            backfill_days = 1

        _LOGGER.debug(
            (
                "Coordinator config: electricity=%s cold=%s hot=%s "
                "lag_e=%s lag_w=%s backfill_days=%s"
            ),
            electricity,
            cold_water,
            hot_water,
            elec_lag,
            water_lag,
            backfill_days,
        )

        # Fire start event
        self.hass.bus.fire(
            EVENT_IMPORT_STARTED,
            {
                "meter_id": meter_id,
                "electricity": electricity,
                "cold_water": cold_water,
                "hot_water": hot_water,
                "electricity_lag": elec_lag,
                "water_lag": water_lag,
                "rolling_backfill_days": backfill_days,
            },
        )

        failed_imports = []

        client = MetergyClient(
            self.hass,
            meter_id=meter_id,
            cookie="",  # Start with empty cookie, will auto-login
            username=username,
            password=password,
        )

        # Test and refresh authentication before importing
        try:
            await client.test_and_refresh_auth()
        except Exception as err:
            _LOGGER.error("Scheduled import failed: Authentication error: %s", err)
            await self._notify_import_failed(
                meter_id,
                "Authentication failed",
                str(err),
            )
            return

        # Electricity: D - elec_lag
        if electricity:
            target = date.today() - timedelta(days=elec_lag)
            start = target - timedelta(days=backfill_days - 1)
            _LOGGER.debug(
                "Electricity target range: %s to %s (lag=%s, backfill=%s)",
                start,
                target,
                elec_lag,
                backfill_days,
            )
            current = start
            while current <= target:
                try:
                    imported = await import_electricity_daily_hourly(
                        self.hass, client, current
                    )
                    _LOGGER.info(
                        "Metergy electricity imported %s samples for %s",
                        imported,
                        current,
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error(
                        "Metergy electricity import failed for %s: %s", current, err
                    )
                    failed_imports.append(f"Electricity ({current}): {err}")
                current += timedelta(days=1)

        # Water: Cold/Hot daily at D - water_lag
        target_w = date.today() - timedelta(days=water_lag)
        start_w = target_w - timedelta(days=backfill_days - 1)
        _LOGGER.debug(
            "Water target range: %s to %s (lag=%s, backfill=%s)",
            start_w,
            target_w,
            water_lag,
            backfill_days,
        )
        if cold_water:
            current = start_w
            while current <= target_w:
                try:
                    cnt = await import_water_daily(
                        self.hass, client, "ColdWater", STAT_ID_COLD_WATER, current
                    )
                    _LOGGER.info(
                        "Metergy cold water imported %s samples for %s", cnt, current
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error(
                        "Metergy cold water import failed for %s: %s", current, err
                    )
                    failed_imports.append(f"Cold Water ({current}): {err}")
                current += timedelta(days=1)

        if hot_water:
            current = start_w
            while current <= target_w:
                try:
                    cnt = await import_water_daily(
                        self.hass, client, "HotWater", STAT_ID_HOT_WATER, current
                    )
                    _LOGGER.info(
                        "Metergy hot water imported %s samples for %s", cnt, current
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error(
                        "Metergy hot water import failed for %s: %s", current, err
                    )
                    failed_imports.append(f"Hot Water ({current}): {err}")
                current += timedelta(days=1)

        # Fire events and notify on failures
        if failed_imports:
            await self._notify_import_failed(
                meter_id,
                "Some imports failed",
                "\n".join(failed_imports),
            )
        else:
            # Silent success - only fire event for automation
            self.hass.bus.fire(
                EVENT_IMPORT_COMPLETED,
                {
                    "meter_id": meter_id,
                    "electricity": electricity,
                    "cold_water": cold_water,
                    "hot_water": hot_water,
                },
            )

    async def _notify_import_failed(
        self,
        meter_id: str,
        title: str,
        error: str,
    ) -> None:
        """Send failure notification for scheduled import."""
        self.hass.bus.fire(
            EVENT_IMPORT_FAILED,
            {
                "meter_id": meter_id,
                "error": error,
            },
        )

        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Metergy Import Failed",
                "message": (
                    f"{title}\n\n{error}\n\nPlease check the logs for details."
                ),
                "notification_id": f"{DOMAIN}_import_failed_{self.entry.entry_id}",
            },
        )
