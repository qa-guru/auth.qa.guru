# GC наставники → Keycloak `/mentors` — 2026-09-10

**IdP:** [auth.qa.guru](https://auth.qa.guru). Источник: Box3 `metrics.users` (ETL getcourse-import-data), не логин в teach-UI. Email в чат не писали.

## Источник

Таблица `users` = проверяющие ДЗ в GetCourse. Live **15** строк, `id=-1` служебная → **14** наставников. Июльский freeze (33) не брали.

## `/mentors`

| | Handles |
|---|---|
| Было | `iskander-d`, `mentor-pilot` |
| Стало | `iskander-d`, `alexov`, `luroy`, `mentor-pilot` |

`/staff` не трогали (`svasenkov`, `staff-pilot`).

## Выдано

| Handle | Было | Сделано |
|---|---|---|
| `alexov` | `/students` `/contour` | + `/mentors` |
| `luroy` | `/students` | + `/mentors` |

`iskander-d` уже был. GC `92979074` — email-коллизия `svasenkov` / `tempuser`; это staff, `/mentors` не вешали.

## Нет учётки в realm (10)

Exact email из L0 не нашёл user. Группу без создания учётки не вешали. GC id: `125202846` `167063837` `168836901` `262981435` `404630579` `436214749` `477451654` `483763670` `492263729` `511124933`.

Чтобы они проверяли ДЗ в Jenkins: сначала вход/создание в [auth.qa.guru](https://auth.qa.guru), потом снова `/mentors`. JWT группы — после re-login.

## Не делали

- Создание user в Keycloak
- `oidc.py configure` / realm
- Nested push `oidc.py` mentor-ACL (другой diff)
- GetCourse write / Playwright
