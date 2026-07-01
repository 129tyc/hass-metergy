from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .api import MetergyClient
from .const import (
    BATCH_SIZE,
    DOMAIN,
    EVENT_BACKFILL_COMPLETED,
    EVENT_BACKFILL_FAILED,
    EVENT_BACKFILL_STARTED,
    MAX_CONCURRENT_REQUESTS,
    SERVICE_BACKFILL,
    STAT_ID_COLD_WATER,
    STAT_ID_HOT_WATER,
)
from .importer import import_electricity_daily_hourly, import_water_daily

_LOGGER = logging.getLogger(__name__)


SERVICE_BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Optional("start"): cv.date,
        vol.Optional("end"): cv.date,
        vol.Optional("electricity", default=True): bool,
        vol.Optional("cold_water", default=False): bool,
        vol.Optional("hot_water", default=False): bool,
    }
)


async def async_setup_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    async def _handle_backfill(call: ServiceCall) -> None:
        """Handle backfill service call - run in background with concurrent fetching."""
        data = hass.data[DOMAIN][entry.entry_id]
        _LOGGER.info("Backfill service called: %s", dict(call.data))

        # Create background task to avoid blocking service return
        hass.async_create_task(
            _backfill_background_job(hass, data, call.data),
            name=f"metergy_backfill_{call.data.get('start', 'default')}",
        )

    async def _backfill_background_job(
        hass: HomeAssistant, config: dict[str, Any], params: dict[str, Any]
    ) -> None:
        """Background job for concurrent backfill with batch processing."""
        meter_id: str = config["meter_id"]
        username: str = config["username"]
        password: str = config["password"]

        start: date | None = params.get("start")
        end: date | None = params.get("end")
        if start is None or end is None:
            end = date.today()
            start = end - timedelta(days=7)

        do_e = bool(params.get("electricity", True))
        do_cw = bool(params.get("cold_water", False))
        do_hw = bool(params.get("hot_water", False))

        # Fire start event and create notification
        event_data = {
            "meter_id": meter_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "electricity": do_e,
            "cold_water": do_cw,
            "hot_water": do_hw,
        }
        hass.bus.fire(EVENT_BACKFILL_STARTED, event_data)

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Metergy Backfill Started",
                "message": f"Importing data from {start} to {end}...",
                "notification_id": f"{DOMAIN}_backfill_{entry.entry_id}",
            },
        )

        client = MetergyClient(
            hass,
            meter_id=meter_id,
            cookie="",  # Start with empty cookie, will auto-login
            username=username,
            password=password,
        )

        # Test and refresh authentication before starting backfill
        # This prevents concurrent requests from triggering multiple login attempts
        try:
            await client.test_and_refresh_auth()
        except Exception as err:
            _LOGGER.error("Authentication failed: %s", err)
            await _notify_backfill_failed(hass, meter_id, start, end, str(err))
            return

        _LOGGER.info(
            "Backfill job started: e=%s cw=%s hw=%s range=%s to %s",
            do_e,
            do_cw,
            do_hw,
            start,
            end,
        )

        # Generate list of days to process
        days: list[date] = []
        current = start
        while current <= end:  # type: ignore[operator]
            days.append(current)
            current += timedelta(days=1)

        total_days = len(days)
        _LOGGER.info("Total days to process: %d", total_days)

        # Process in batches to balance memory and progress visibility
        for batch_idx in range(0, total_days, BATCH_SIZE):
            batch = days[batch_idx : batch_idx + BATCH_SIZE]
            batch_num = (batch_idx // BATCH_SIZE) + 1
            total_batches = (total_days + BATCH_SIZE - 1) // BATCH_SIZE

            _LOGGER.info(
                "Processing batch %d/%d: %s to %s (%d days)",
                batch_num,
                total_batches,
                batch[0],
                batch[-1],
                len(batch),
            )

            # Concurrent fetch with semaphore to limit parallel requests
            await _process_batch_concurrent(hass, client, batch, do_e, do_cw, do_hw)

            # Brief pause between batches to avoid overwhelming provider
            if batch_idx + BATCH_SIZE < total_days:
                await asyncio.sleep(0.5)

        _LOGGER.info("Backfill job completed: processed %d days", total_days)

        # Fire completion event and update notification
        completion_data = {
            **event_data,
            "days_processed": total_days,
            "status": "completed",
        }
        hass.bus.fire(EVENT_BACKFILL_COMPLETED, completion_data)

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Metergy Backfill Completed",
                "message": (
                    f"Successfully imported {total_days} days of data "
                    f"from {start} to {end}.\n"
                    f"Electricity: {'Yes' if do_e else 'No'}, "
                    f"Cold Water: {'Yes' if do_cw else 'No'}, "
                    f"Hot Water: {'Yes' if do_hw else 'No'}"
                ),
                "notification_id": f"{DOMAIN}_backfill_{entry.entry_id}",
            },
        )

    async def _notify_backfill_failed(
        hass: HomeAssistant,
        meter_id: str,
        start: date,
        end: date,
        error: str,
    ) -> None:
        """Send failure notification for backfill."""
        hass.bus.fire(
            EVENT_BACKFILL_FAILED,
            {
                "meter_id": meter_id,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "error": error,
            },
        )

        await hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Metergy Backfill Failed",
                "message": (
                    f"Failed to import data from {start} to {end}.\n"
                    f"Error: {error}\n\n"
                    "Please check the logs for details."
                ),
                "notification_id": f"{DOMAIN}_backfill_{entry.entry_id}",
            },
        )

    async def _process_batch_concurrent(
        hass: HomeAssistant,
        client: MetergyClient,
        batch: list[date],
        do_e: bool,
        do_cw: bool,
        do_hw: bool,
    ) -> None:
        """Process a batch with concurrent API fetches, then sequential statistics import."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async def fetch_day_data(day: date) -> tuple[date, dict[str, Any | None]]:
            """Fetch all requested data for a single day with concurrency limit."""
            async with semaphore:
                results: dict[str, Any | None] = {}

                # Fetch electricity
                if do_e:
                    try:
                        async with asyncio.timeout(30):
                            results["electricity"] = await client.fetch_consumption(
                                "Electricity", "Hourly", day, day
                            )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.error("Fetch electricity failed for %s: %s", day, err)
                        results["electricity"] = None

                # Fetch cold water
                if do_cw:
                    try:
                        async with asyncio.timeout(30):
                            results["cold_water"] = await client.fetch_consumption(
                                "ColdWater", "Daily", day, day
                            )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.error("Fetch cold water failed for %s: %s", day, err)
                        results["cold_water"] = None

                # Fetch hot water
                if do_hw:
                    try:
                        async with asyncio.timeout(30):
                            results["hot_water"] = await client.fetch_consumption(
                                "HotWater", "Daily", day, day
                            )
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.error("Fetch hot water failed for %s: %s", day, err)
                        results["hot_water"] = None

                return (day, results)

        # Phase 1: Concurrent fetch of all days in batch
        _LOGGER.debug("Fetching data for %d days concurrently...", len(batch))
        fetch_tasks = [fetch_day_data(day) for day in batch]
        fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # Phase 2: Sequential import to ensure correct cumulative sums
        _LOGGER.debug("Importing statistics sequentially...")
        for result in fetch_results:
            if isinstance(result, Exception):
                _LOGGER.error("Fetch task failed: %s", result)
                continue

            day, data = result

            # Import electricity
            if do_e and data.get("electricity"):
                try:
                    await import_electricity_daily_hourly(hass, client, day)
                    _LOGGER.debug("Imported electricity for %s", day)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("Import electricity failed for %s: %s", day, err)

            # Import cold water
            if do_cw and data.get("cold_water"):
                try:
                    await import_water_daily(
                        hass, client, "ColdWater", STAT_ID_COLD_WATER, day
                    )
                    _LOGGER.debug("Imported cold water for %s", day)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("Import cold water failed for %s: %s", day, err)

            # Import hot water
            if do_hw and data.get("hot_water"):
                try:
                    await import_water_daily(
                        hass, client, "HotWater", STAT_ID_HOT_WATER, day
                    )
                    _LOGGER.debug("Imported hot water for %s", day)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("Import hot water failed for %s: %s", day, err)

    hass.services.async_register(
        DOMAIN, SERVICE_BACKFILL, _handle_backfill, schema=SERVICE_BACKFILL_SCHEMA
    )
