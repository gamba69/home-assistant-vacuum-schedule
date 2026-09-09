# Архітектура

## Runtime-шари

1. **Schedule / planning** — `schedule.py`, `planner.py`, `scheduler_engine.py` обчислюють occurrence та керують життєвим циклом Job.
2. **Pre-flight / dependencies** — `input_provider.py`, `preflight.py`, `dependency_index.py`, `force_conditions.py` оцінюють готовність без команд роботу.
3. **Execution boundary** — `execution_manager.py`, `execution_backend.py`, `execution_models.py` керують REAL/Dry-Run спробами та бар'єром at-most-once command-intent.
4. **Observation** — `execution_observer.py` нормалізує vendor/entity стани у фізичні фази й телеметрію.
5. **Notifications** — manager, formatting, models і store відповідають за семантичні події, доставлення маршрутами та архів повідомлень.
6. **Statistics / maintenance / forecasts** — статистика, вода, заряджання та прогнози зберігають довготривалі факти й моделі окремо від стану Scheduler.
7. **Frontend** — `frontend.py` надає WebSocket/API межі, `frontend/panel.js` відображає панель Home Assistant.

## Зберігання

- JobStore зберігає активні Jobs, обмежену operational history, terminal snapshots і lifecycle trace.
- Statistics Ledger зберігає immutable-факти виконання окремо від operational-стану Job.
- Для води, заряджання та прогнозів використовуються окремі Store.
- Ідентичність прогнозної моделі суворо залежить від ефективного фізичного профілю прибирання.
- Міграція перейменування domain ізольована в `domain_migration.py`; історичні schema migrations залишаються в `migrations.py` і `storage_migrations.py`.

## Ключові інваріанти

- Фізична команда не надсилається поза execution backend.
- Спроба після надсилання команди не перетворюється після рестарту на новий start.
- Для одного робота одночасно існує не більше одного фізичного execution lease.
- WAIT одного Job не блокує інший виконуваний Job.
- Dry-Run не забруднює REAL-статистику.
- Дострокове виконання не повинно непомітно створювати повторний запуск у початковий плановий час.
- Невизначеність води та прогнозів зберігається явно, а не замінюється вигаданими спостереженнями.

## Межа domain у 0.13

`vacuum_schedule` — єдиний публічний domain інтеграції. Рядок legacy-domain `vacuum_scheduler` дозволений лише в migration-коді/тестах, що розпізнають дані 0.12.61. Нові Store, actions, events, WebSocket-команди, маршрут панелі та frontend persistence використовують нову назву.
