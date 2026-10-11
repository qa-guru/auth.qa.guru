# Keycloak login theme `qaguru`

Лицо входа школы на [auth.qa.guru](https://auth.qa.guru) — realm `qaguru`. Вход в admin (`master` / `security-admin-console`) — стоковый `keycloak.v2`, чтобы его не путали со школьным (2026-10-11). Parent `keycloak.v2`. Ids формы не меняются. Сам admin UI после логина (`adminTheme`) не трогаем.

Шапка — DS header (бренд qa.guru + Главная / О школе + GitHub). CSS скопирован из `design-system/css/` (`tokens`, `header`, `link`, `icon`, `icon-btn`). `header.js` не подключаем. `ds-header.ftl` сам линкует эти CSS: прод кэширует `theme.properties`.

| | |
|--|--|
| Id | `qaguru` |
| Тип | `login` |
| Parent | `keycloak.v2` |
| Header | `login/ds-header.ftl` ← markup `templates/header.html` |
| Overlay | `login/resources/css/login.css` + `shell.css` |
| Realms | `qaguru` (`loginTheme` only); `master` — стоковый `keycloak.v2` |

Стенд: `python scripts/apply-theme.py`.
Прод: `python3 deploy/theme.py apply` — rsync в `:ro` bind mount + PUT двух realm, **без** stop IdP.
