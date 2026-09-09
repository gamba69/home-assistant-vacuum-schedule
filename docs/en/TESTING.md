# Testing and release verification

Every release is validated from a clean tree and again from a freshly unpacked final archive.

## Required checks

```bash
pytest -q
python -m compileall -q custom_components tests
node --check custom_components/vacuum_schedule/frontend/panel.js
```

Additionally:

- parse every JSON file;
- ensure no `PATCH_*.md` files exist;
- ensure `custom_components/` contains exactly one integration directory: `vacuum_schedule`;
- ensure `manifest.json` reports `Vacuum Schedule`, `vacuum_schedule` and the release version;
- ensure public actions use `vacuum_schedule.*`;
- ensure the old literal `vacuum_scheduler` exists only in explicit migration code/tests/documentation;
- verify EN/RU/UK documentation filename parity;
- verify EN/RU/UK Home Assistant translation key parity;
- exclude caches and `*.pyc` from release archives.

## 0.13.0 migration regression

The release must verify that the migration layer copies every 0.12.61 integration Store, discovers monthly Statistics Ledger chunks from the old index, creates the new entry with copied `data` and `options`, and removes the obsolete config entry only after the data copy completes.

## Baseline

0.12.61: 974 tests passed. The 0.13.0 suite retains that regression base and adds rename, documentation and legacy-data migration contracts.

## GitHub CI

The same project gate runs automatically on pushes to `main`, pull requests, version tags and published releases. HACS validation and hassfest remain separate workflows. Generated `*.patch` files are local delivery artifacts and must never be committed.
