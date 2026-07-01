from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

from homeassistant.components.recorder import (
    statistics,
    get_instance as recorder_get_instance,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util.dt import DEFAULT_TIME_ZONE, as_utc

from .api import MetergyClient
from .const import (
    STAT_ID_ELECTRICITY,
    STAT_SOURCE,
)


@dataclass
class ImportResult:
    imported: int
    start: date
    end: date


def _local_midnight(d: date) -> datetime:
    # Align to local midnight, then convert to UTC. Works with zoneinfo tz.
    local_dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=DEFAULT_TIME_ZONE)
    return as_utc(local_dt)


async def _get_sum_at_timestamp(
    hass: HomeAssistant, statistic_id: str, before_time: datetime
) -> float:
    """Return cumulative sum at a specific timestamp, or 0.0 if none exists before that time.

    This queries statistics before the given timestamp to get the baseline for new imports.
    """
    instance = recorder_get_instance(hass)

    # Get statistics during a period ending just before our target time
    # Use a 30-day lookback window to find the last value before target
    start_time = before_time - timedelta(days=30)
    end_time = before_time

    def _get_stats():
        return statistics.statistics_during_period(
            hass, start_time, end_time, {statistic_id}, "hour", None, {"sum"}
        )

    result = await instance.async_add_executor_job(_get_stats)

    if result and (items := result.get(statistic_id)):
        # Get the last (most recent) entry before our target time
        try:
            return float(items[-1].get("sum") or 0.0)
        except (IndexError, Exception):  # noqa: BLE001
            return 0.0
    return 0.0


async def import_electricity_daily_hourly(
    hass: HomeAssistant,
    client: MetergyClient,
    target_day: date,
) -> int:
    """Import a single day's hourly electricity into statistics.

    Creates 24 samples (or fewer if provider returns less) with kWh sums.
    """

    payload = await client.fetch_consumption(
        "Electricity", "Hourly", target_day, target_day
    )
    logger = logging.getLogger(__name__)
    try:
        kwh_list = MetergyClient.extract_hourly_kwh_list(payload)
    except Exception as err:  # noqa: BLE001
        logger.debug("No hourly data for %s: %s", target_day, err)
        return 0
    logger.debug("Electricity %s hourly values: %s", target_day, kwh_list)

    # Build statistics list with hourly start times based on local boundaries
    # _local_midnight returns UTC timestamp representing local midnight boundary
    start_base = _local_midnight(target_day)
    logger.debug("Electricity base UTC start for %s: %s", target_day, start_base)

    # Determine baseline cumulative sum from the last sample BEFORE this day starts
    # This ensures idempotent imports even with out-of-order or repeated backfills
    last_sum = await _get_sum_at_timestamp(hass, STAT_ID_ELECTRICITY, start_base)
    logger.debug("Electricity baseline sum before %s: %s", target_day, last_sum)
    stats: List[Dict[str, Any]] = []
    running_sum = last_sum
    for idx, kwh in enumerate(kwh_list):
        bucket_start = start_base + timedelta(hours=idx)
        running_sum += float(kwh)
        stats.append(
            {
                "start": bucket_start,
                "state": None,
                "sum": running_sum,
            }
        )

    if not stats:
        return 0

    metadata = statistics.StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name="Metergy Electricity",
        source=STAT_SOURCE,
        statistic_id=STAT_ID_ELECTRICITY,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )

    logger.debug(
        "Importing electricity stats: count=%s last_sum=%s last_start=%s",
        len(stats),
        running_sum,
        (stats[-1]["start"] if stats else None),
    )
    statistics.async_add_external_statistics(hass, metadata, stats)
    return len(stats)


async def import_water_daily(
    hass: HomeAssistant,
    client: MetergyClient,
    service_type: str,  # "ColdWater" or "HotWater"
    statistic_id: str,
    target_day: date,
) -> int:
    """Import one day of water volume as a daily sample at local midnight.

    Uses Daily interval for the target day; takes the first/only value.
    """

    payload = await client.fetch_consumption(
        service_type, "Daily", target_day, target_day
    )
    logger = logging.getLogger(__name__)
    try:
        vol_list = MetergyClient.extract_daily_volume_list(payload)
    except Exception as err:  # noqa: BLE001
        logger.debug("No daily data for %s %s: %s", service_type, target_day, err)
        return 0
    logger.debug("%s %s daily values: %s", service_type, target_day, vol_list)
    if not vol_list:
        return 0

    midnight = _local_midnight(target_day)
    daily_volume = float(vol_list[0])

    # Determine unit from payload; Metergy examples show "LTR" for water
    unit_str = MetergyClient.extract_unit_of_measure(payload).upper()
    logger.debug("%s %s unit: %s", service_type, target_day, unit_str)
    # Map to HA unit; prefer cubic meters if your property uses m³. Here we map LTR -> L.
    unit = (
        UnitOfVolume.LITERS
        if unit_str in {"LTR", "L", "LITERS"}
        else UnitOfVolume.CUBIC_METERS
    )

    # Determine baseline cumulative sum from the last sample BEFORE this day starts
    # This ensures idempotent imports even with out-of-order or repeated backfills
    last_sum = await _get_sum_at_timestamp(hass, statistic_id, midnight)
    logger.debug("%s baseline sum before %s: %s", service_type, target_day, last_sum)
    cumulative = last_sum + daily_volume

    metadata = statistics.StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name=f"Metergy {service_type}",
        source=STAT_SOURCE,
        statistic_id=statistic_id,
        unit_of_measurement=unit,
    )

    # Daily value as cumulative sum: add onto prior sum; recorder will handle continuity when backfilling
    # For idempotency, we provide only the sum at midnight for that day
    stats = [
        {
            "start": midnight,
            "state": None,
            "sum": cumulative,
        }
    ]

    logger.debug(
        "Importing %s stats for %s: sum=%s start=%s",
        service_type,
        target_day,
        cumulative,
        midnight,
    )
    statistics.async_add_external_statistics(hass, metadata, stats)
    return 1
