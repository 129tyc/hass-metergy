"""Constants for the Metergy custom integration."""

DOMAIN = "metergy"

# Default configuration
DEFAULT_ELECTRICITY_ENABLED = True
DEFAULT_COLD_WATER_ENABLED = False
DEFAULT_HOT_WATER_ENABLED = False

DEFAULT_ELECTRICITY_LAG_DAYS = 2  # Provider appears ~2 days delayed
DEFAULT_WATER_LAG_DAYS = 3  # Water often ~3 days delayed
DEFAULT_ROLLING_BACKFILL_DAYS = 1  # Import target day only when set to 1

# Statistic IDs (use liters for water to match provider payload LTR)
STAT_ID_ELECTRICITY = "metergy:electricity_kwh"
STAT_ID_COLD_WATER = "metergy:cold_water_liters"
STAT_ID_HOT_WATER = "metergy:hot_water_liters"

# Units
UNIT_KWH = "kWh"
UNIT_M3 = "m³"

# Sources (must match domain in statistic_id for external statistics)
STAT_SOURCE = "metergy"

# Services
SERVICE_BACKFILL = "backfill"

# Backfill concurrency settings
MAX_CONCURRENT_REQUESTS = (
    5  # Limit concurrent API requests to avoid overwhelming provider
)
BATCH_SIZE = 14  # Process 14 days per batch to balance speed and memory

# Event types
EVENT_BACKFILL_STARTED = f"{DOMAIN}_backfill_started"
EVENT_BACKFILL_COMPLETED = f"{DOMAIN}_backfill_completed"
EVENT_BACKFILL_FAILED = f"{DOMAIN}_backfill_failed"
EVENT_IMPORT_STARTED = f"{DOMAIN}_import_started"
EVENT_IMPORT_COMPLETED = f"{DOMAIN}_import_completed"
EVENT_IMPORT_FAILED = f"{DOMAIN}_import_failed"
