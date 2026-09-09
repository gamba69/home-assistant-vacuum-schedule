# Changelog

## 0.13.2

- Fixed hassfest manifest key ordering.
- Added `*.patch` to `.gitignore` and removed the accidentally committed 0.13.1 patch artifact.
- Added a GitHub Actions project-test workflow that runs the regression suite, Python compilation, frontend JavaScript syntax check, JSON/YAML validation and rejects committed patch artifacts on `main`, pull requests, version tags and published releases.
- Updated GitHub checkout actions to the Node 24 based version.
- Bumped the integration and frontend cache/component identity to 0.13.2.
- Release verification: **989 tests passed**, Python compile, frontend JavaScript syntax and JSON/YAML validation passed.

## 0.13.1

- Fixed HACS/hassfest release validation for the new repository.
- Declared the Home Assistant `http` dependency used by the custom panel static path.
- Declared the integration as config-entry-only for YAML schema validation.
- Normalized translatable sensor state keys to Home Assistant-compatible lowercase values without changing internal Job results.
- Replaced the invalid selector sentinel with a valid value while retaining compatibility with legacy stored schedules.
- Added the repository license and removed obsolete migration-document links from README files.
- Bumped the frontend component/cache identity to 0.13.1.

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
