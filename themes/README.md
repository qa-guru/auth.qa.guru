# Keycloak login theme `qaguru`

Общее лицо входа на [auth.qa.guru](https://auth.qa.guru). Расширяет `keycloak.v2` CSS-ом — FTL не копируем, ids формы не меняются.

| | |
|--|--|
| Id | `qaguru` |
| Тип | `login` (account / email / admin не трогаем) |
| Parent | `keycloak.v2` |
| Логотип | `login/resources/img/qa-guru-logo.svg` — светлый вариант DS-марки (SSOT тёмный: `design-system/assets/qa-guru-logo.svg`) |

Стенд: volume `./themes/qaguru` + `python scripts/apply-theme.py`.
Прод: `python3 auth.qa.guru/deploy/theme.py apply` — rsync + `docker cp`, **без** `compose up` / stop IdP.
