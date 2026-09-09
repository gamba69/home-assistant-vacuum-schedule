# Архитектура

## Runtime-слои

1. **Schedule / planning** — `schedule.py`, `planner.py`, `scheduler_engine.py` рассчитывают occurrence и управляют жизненным циклом Job.
2. **Pre-flight / dependencies** — `input_provider.py`, `preflight.py`, `dependency_index.py`, `force_conditions.py` оценивают готовность без команд роботу.
3. **Execution boundary** — `execution_manager.py`, `execution_backend.py`, `execution_models.py` управляют REAL/Dry-Run попытками и барьером at-most-once command-intent.
4. **Observation** — `execution_observer.py` нормализует vendor/entity состояния в физические фазы и телеметрию.
5. **Notifications** — manager, formatting, models и store отвечают за семантические события, доставку по маршрутам и архив сообщений.
6. **Statistics / maintenance / forecasts** — статистика, вода, зарядка и прогнозы хранят долгосрочные факты и модели отдельно от состояния Scheduler.
7. **Frontend** — `frontend.py` предоставляет WebSocket/API границы, `frontend/panel.js` рисует панель Home Assistant.

## Хранение

- JobStore хранит активные Jobs, ограниченную operational history, terminal snapshots и lifecycle trace.
- Statistics Ledger хранит immutable-факты выполнения отдельно от operational-состояния Job.
- Для воды, зарядки и прогнозов используются отдельные Store.
- Идентичность прогнозной модели строго зависит от эффективного физического профиля уборки.
- Миграция переименования domain изолирована в `domain_migration.py`; исторические schema migrations остаются в `migrations.py` и `storage_migrations.py`.

## Ключевые инварианты

- Физическая команда не отправляется вне execution backend.
- Попытка после отправки команды не превращается после рестарта в новый start.
- Для одного робота одновременно существует не более одного физического execution lease.
- WAIT одного Job не блокирует другой исполнимый Job.
- Dry-Run не загрязняет REAL-статистику.
- Досрочное выполнение не должно молча порождать повторный запуск в исходное плановое время.
- Неопределённость воды и прогнозов сохраняется явно, а не заменяется выдуманными наблюдениями.

## Граница domain в 0.13

`vacuum_schedule` — единственный публичный domain интеграции. Строка legacy-domain `vacuum_scheduler` допустима только в migration-коде/тестах, распознающих данные 0.12.61. Новые Store, actions, events, WebSocket-команды, маршрут панели и frontend persistence используют новое имя.
