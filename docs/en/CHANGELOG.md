# Changelog

## 0.13.0

- Renamed the integration to **Vacuum Schedule**.
- Changed the public domain to `vacuum_schedule` and the integration directory to `custom_components/vacuum_schedule/`.
- Moved Home Assistant action/service namespace to `vacuum_schedule.*`.
- Changed the panel path to `/vacuum-schedule`, frontend storage namespace to `vacuum_schedule.*`, and integration events/WebSocket commands to the new public identity.
- Added one-time 0.12.61 legacy-domain migration for config-entry data/options and all integration-owned persistent Store layers, including monthly Statistics Ledger chunks.
- Removed per-patch release-note documents permanently; release history is maintained only in the synchronized changelog/docs.
- Established synchronized project documentation in English, Russian and Ukrainian.
- Prepared the repository for HACS with `hacs.json`, hassfest validation and HACS validation workflows.
- Kept 0.12.61 scheduler/statistics/forecast behavior as the functional baseline; 0.13.0 is a release-identity/migration/documentation release.
- Added legacy statistics backup archive migration into the new `vacuum_schedule_backups/` namespace and new config-entry id.
- Release verification: **983 tests passed**, Python compile, JavaScript syntax, JSON and YAML validation passed.

## 0.12.61 — baseline

- Fixed unsafe Forecast fallback that mixed different cleaning profiles merely because the same zones were used.
- Time and battery forecasts require enough observations from the exact effective cleaning profile, either as whole-job samples or same-profile per-zone composites.
- Historical dry/mop-only forecast signatures are canonicalized so stale irrelevant fields no longer split one physical profile; missing passes normalize to 1×.
- Estimate table labels insufficient exact-profile history explicitly instead of showing a borrowed same-zone estimate.
- Regression suite baseline: **974 tests passed**.
