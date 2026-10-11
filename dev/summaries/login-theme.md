# Login-тема Keycloak `qaguru`

**Снято:** 2026-09-08 · [auth.qa.guru](https://auth.qa.guru) · пилот `staff-pilot`, не `svasenkov`.

Не LDAP. Не P9. IdP не гасили. Клиенты не трогали.

## Что

Одно лицо входа — `keycloak.v2` + DS header + CSS overlay — на realm `qaguru` и на login admin (`master` / `security-admin-console`). `adminTheme` (React-консоль после входа) не меняли.

**2026-10-11:** `master` вернули на стоковый `keycloak.v2` — школьная тема на входе админа путала людей: вводили учётку Jenkins в master. Корень auth.qa.guru теперь ведёт в кабинет `qaguru`. Строки «master» ниже — история.

SSOT: [`../themes/qaguru/`](../themes/qaguru/). Прод: `python3 deploy/theme.py apply`.

## Пробы

| Где | шапка | форма | вход |
|-----|-------|-------|------|
| стенд `:8543` qaguru | `.header` 41px | ids живы | `student-demo` → account |
| стенд `:8543` master | то же | username / Sign in | лицо, без логина admin |
| прод qaguru | то же | ids живы | `staff-pilot` |
| прод master | то же | username / Sign in | лицо, без логина admin |
