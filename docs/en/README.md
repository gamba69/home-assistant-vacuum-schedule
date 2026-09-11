# Vacuum Schedule

Vacuum Schedule is a Home Assistant custom integration for deterministic robot-vacuum scheduling, pre-flight checks, WAIT handling, Dry-Run/REAL execution, notifications, maintenance, statistics and forecasting.

## Public identity from 0.13.0

- Name: **Vacuum Schedule**
- Domain: `vacuum_schedule`
- Integration directory: `custom_components/vacuum_schedule/`
- Repository: `gamba69/home-assistant-vacuum-schedule`
- Home Assistant actions/services: `vacuum_schedule.*`
- Panel path: `/vacuum-schedule`

The previous `vacuum_scheduler` public identity is legacy and is used only by the one-time 0.12.61 → 0.13.0 migration code.

## Installation

For a new installation, copy `custom_components/vacuum_schedule/` to the Home Assistant `custom_components` directory and restart Home Assistant. The repository layout is HACS-ready, so it can also be added as a custom HACS integration repository.

For an upgrade from 0.12.61, install 0.13.4, restart Home Assistant, add **Vacuum Schedule**, select the same vacuum and confirm migration of the detected legacy entry.

## Current model

- A **Schedule** defines recurring local-time occurrences and weekday-specific corrections.
- A materialized **JobInstance** represents a scheduled occurrence and owns its lifecycle.
- Cleaning Zones map logical scheduler zones to physical robot targets and carry occupancy/access rules and nominal area.
- Pre-flight and WAIT are independent from physical execution. Recoverable blockers do not consume an occurrence before the command-intent boundary.
- Dry-Run follows scheduler/pre-flight/notification semantics without sending physical commands or training REAL statistics.
- Statistics Ledger and forecast models are stored independently from Home Assistant Recorder.
- External physical runs are retained and train a forecast model only when target/profile attribution is reliable.
- Forecasting is strict by effective cleaning profile; unrelated same-zone history is never borrowed merely to produce an estimate.

## Manual and automation starts

- **Additional run** creates a separate manual Job and does not consume the next scheduled occurrence.
- **Run early manually** executes the existing future occurrence and retains that occurrence's original effective profile.
- **Smart run** executes a still-pending scheduled occurrence for the current local day when one exists; otherwise it creates an additional run.
- Runtime controls have matching Home Assistant actions under `vacuum_schedule.*`.

## Localization

Project documentation is maintained in **English, Russian and Ukrainian** from the 0.12.61 baseline onward. The Home Assistant integration and custom panel also ship EN/RU/UK localization.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Testing and release checks](TESTING.md)
- [Changelog](CHANGELOG.md)
