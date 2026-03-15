# S02 Customer Demo — Скриншоты (10.03.2026)

Цель: показать заказчику, что исходная серия Word-ТЗ по WABA закрыта в production.

## Что приложить

1. `01_G1_after_consultation.png` — `gosniki_consultation_done` (`id=35`)
2. `02_B1_after_assignment.png` — `berater_accepted` (`id=34`)
3. `03_B2_day_minus_7.png` — `berater_day_minus_7` (`id=36`)
4. `04_B3_day_minus_3.png` — `berater_day_minus_3` (`id=37`)
5. `05_B4_day_minus_1.png` — `berater_day_minus_1` (`id=38`)
6. `06_B5_day_0.png` — `berater_day_0` (`id=39`)

Можно использовать 2-3 длинных скрина вместо 6 отдельных, но все 6 сообщений должны быть читаемы.

## Что должно быть видно на скринах

- Название чата: `SternMeister`
- Текст сообщения (читаемо без обрезки ключевых фраз)
- Время отправки сообщения
- Для B3/B4: видимые quick reply-кнопки (если отображаются)

## Техническое подтверждение (из production)

- Артефакт прогона: `/app/whatsapp/backups/full-series-correct-20260310T164558Z/result.json`
- `missing_lines = []`
- `phones_sent_in_run_window = ["+996501354144"]`
- error counters: `runtime=0`, `console=0`, `request=0`, `server=0`

## Что не демонстрируется этим пакетом

- `После термина` (пункт 6 из Word) — не входит в S02 scope.
- Email-шаблоны из Word — не входят в S02 scope.
