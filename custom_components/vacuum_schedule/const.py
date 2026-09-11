"""Constants for Vacuum Schedule."""

from typing import Final

DOMAIN: Final = "vacuum_schedule"
VERSION: Final = "0.13.3"

PLATFORMS: Final = ["sensor", "calendar"]

CONF_VACUUM_ENTITY_ID: Final = "vacuum_entity_id"
CONF_RELATED_DEVICE_IDS: Final = "related_device_ids"
CONF_RELATED_ENTITIES: Final = "related_entities"
CONF_CLEAN_WATER_STATUS_ENTITY_ID: Final = "clean_water_status_entity_id"
CONF_DIRTY_WATER_STATUS_ENTITY_ID: Final = "dirty_water_status_entity_id"
CONF_SCHEDULES: Final = "schedules"
CONF_WEEKDAY_TIMES: Final = "weekday_times"
CONF_WEEKDAY_OVERRIDES: Final = "weekday_overrides"
CONF_FORCE_ENABLED: Final = "force_enabled"
CONF_FORCE_MAX_ADVANCE_MINUTES: Final = "force_max_advance_minutes"
CONF_FORCE_PRIORITY: Final = "force_priority"
CONF_FORCE_PREEMPTS_SCHEDULED: Final = "force_preempts_scheduled"
CONF_FORCE_CONDITION_GROUPS: Final = "force_condition_groups"
CONF_FORCE_CONDITION_GROUP_NAMES: Final = "force_condition_group_names"
CONF_CAPABILITY_BINDINGS: Final = "capability_bindings"
CONF_CLEANING_ZONES: Final = "cleaning_zones"
# Legacy early-0.5 option key, migrated to CONF_CLEANING_ZONES.
CONF_ROOM_PROFILES: Final = "room_profiles"
CONF_PREFLIGHT_POLICY: Final = "preflight_policy"
CONF_NOTIFICATION_SETTINGS: Final = "notification_settings"
CONF_INTERFACE_LANGUAGE: Final = "interface_language"
CONF_EXECUTION_MODE: Final = "execution_mode"
CONF_DRY_RUN_SETTINGS: Final = "dry_run_settings"
CONF_REAL_EXECUTION_SETTINGS: Final = "real_execution_settings"

SERVICE_EMIT_TEST_EVENT: Final = "emit_test_event"
SERVICE_REFRESH_CAPABILITIES: Final = "refresh_capabilities"
# Canonical runtime actions exposed to Home Assistant automations.
SERVICE_ADDITIONAL_RUN: Final = "additional_run"
SERVICE_RUN_EARLY: Final = "run_early"
SERVICE_SMART_RUN: Final = "smart_run"
SERVICE_RUN_SCHEDULE_NOW: Final = "run_schedule_now"
SERVICE_START_JOB_NOW: Final = "start_job_now"
SERVICE_SKIP_JOB: Final = "skip_job"
SERVICE_CANCEL_JOB: Final = "cancel_job"
SERVICE_PAUSE_JOB: Final = "pause_job"
SERVICE_RESUME_JOB: Final = "resume_job"
SERVICE_RECHECK_JOB: Final = "recheck_job"
SERVICE_SET_SCHEDULE_ENABLED: Final = "set_schedule_enabled"
SERVICE_SET_SCHEDULE_PAUSED: Final = "set_schedule_paused"
SERVICE_SET_EXECUTION_GATE: Final = "set_execution_gate"
SERVICE_REBUILD_SCHEDULE: Final = "rebuild_schedule"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

EVENT_TEST: Final = "vacuum_schedule_test_event"
EVENT_CAPABILITIES_REFRESHED: Final = "vacuum_schedule_capabilities_refreshed"
EVENT_SETTINGS_UPDATED: Final = "vacuum_schedule_settings_updated"

SIGNAL_RUNTIME_UPDATED: Final = f"{DOMAIN}_runtime_updated"
SIGNAL_JOB_UPDATED: Final = f"{DOMAIN}_job_updated"
SIGNAL_SCHEDULER_UPDATED: Final = f"{DOMAIN}_scheduler_updated"

# 0.5.0 cleaning-zone model supersedes the early RoomProfile schema.
ENTRY_VERSION: Final = 7
ENTRY_MINOR_VERSION: Final = 6

# Calendar model defaults.
DEFAULT_PREWARNING_MINUTES: Final = 15
DEFAULT_EXECUTION_WINDOW_MINUTES: Final = 120
DEFAULT_PASSES: Final = 1
CONF_ZONE_EXECUTION_POLICY: Final = "zone_execution_policy"
ZONE_EXECUTION_POLICY_COMBINED: Final = "combined"
ZONE_EXECUTION_POLICY_PROGRESSIVE: Final = "progressive"
DEFAULT_ZONE_EXECUTION_POLICY: Final = ZONE_EXECUTION_POLICY_COMBINED
LEGACY_ZONE_EXECUTION_POLICY: Final = ZONE_EXECUTION_POLICY_PROGRESSIVE
ZONE_EXECUTION_POLICIES: Final = (ZONE_EXECUTION_POLICY_COMBINED, ZONE_EXECUTION_POLICY_PROGRESSIVE)
DEFAULT_MIN_BATTERY_PERCENT: Final = 20
DEFAULT_RUNTIME_ERROR_RECOVERY_MINUTES: Final = 30

# Presentation locale. ``auto`` follows the current HA frontend for the panel
# and the HA server locale for unattended backend notification delivery.
INTERFACE_LANGUAGE_AUTO: Final = "auto"
SUPPORTED_PRESENTATION_LANGUAGES: Final = ("en", "ru", "uk")
SUPPORTED_INTERFACE_LANGUAGES: Final = (INTERFACE_LANGUAGE_AUTO, *SUPPORTED_PRESENTATION_LANGUAGES)

WEEKDAY_CODES: Final = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_TO_INDEX: Final = {code: index for index, code in enumerate(WEEKDAY_CODES)}
INDEX_TO_WEEKDAY: Final = {index: code for code, index in WEEKDAY_TO_INDEX.items()}

TARGET_TYPE_CLEANING_ZONES: Final = "cleaning_zones"
# Legacy target types are accepted only during migration/deserialization.
TARGET_TYPE_AREAS: Final = "areas"
TARGET_TYPE_SEGMENTS: Final = "segments"
TARGET_TYPE_ZONE: Final = "zone"
TARGET_TYPES: Final = (TARGET_TYPE_CLEANING_ZONES,)
