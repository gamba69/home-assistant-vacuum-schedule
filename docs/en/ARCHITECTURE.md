# Architecture

## Runtime layers

1. **Schedule / planning** — `schedule.py`, `planner.py`, `scheduler_engine.py` calculate occurrences and own Job lifecycle.
2. **Pre-flight / dependencies** — `input_provider.py`, `preflight.py`, `dependency_index.py`, `force_conditions.py` evaluate readiness without issuing robot commands.
3. **Execution boundary** — `execution_manager.py`, `execution_backend.py`, `execution_models.py` own REAL/Dry-Run attempts and the at-most-once command-intent barrier.
4. **Observation** — `execution_observer.py` normalizes vendor/entity state into physical phases and telemetry.
5. **Notifications** — notification manager, formatting, models and store modules own semantic events, route delivery and retained message history.
6. **Statistics / maintenance / forecasts** — statistics, water, charging and forecasting modules own long-term facts and models independently from Scheduler state.
7. **Frontend** — `frontend.py` exposes WebSocket/API boundaries; `frontend/panel.js` renders the Home Assistant panel.

## Persistence

- JobStore keeps active Jobs, bounded operational history, terminal snapshots and lifecycle trace.
- Statistics Ledger stores immutable execution facts independently from operational Job state.
- Water, charging and forecast state use dedicated stores.
- Forecast identity is strict by effective physical cleaning profile.
- Domain rename data migration is isolated in `domain_migration.py`; historical schema migrations remain in `migrations.py` and `storage_migrations.py`.

## Key invariants

- No physical command is issued outside the execution backend.
- A post-command attempt is never converted into a fresh start after restart.
- Only one physical execution lease may exist per robot.
- WAIT for one Job does not block another runnable Job.
- Dry-Run does not contaminate REAL statistics.
- A forced/manual-early execution consumes the intended occurrence according to its scheduler semantics and is never silently duplicated at the original planned time.
- Water and forecast uncertainty are preserved instead of inventing observations.

## 0.13 domain boundary

`vacuum_schedule` is the only public integration domain. The literal legacy domain `vacuum_scheduler` is allowed only inside migration code/tests that identify 0.12.61 data. New stores, actions, events, WebSocket commands, panel routes and frontend persistence use the new identity.
