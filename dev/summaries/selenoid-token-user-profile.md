# User Profile: `selenoidToken`

**Снято:** 2026-09-10 · [auth.qa.guru](https://auth.qa.guru) · пилот `staff-pilot`, не `svasenkov`.

Окно 1 плана [персональных токенов](../../../../../../docs/plans/selenoid-personal-tokens.md). Не ферма, не nginx, не htpasswd. IdP не гасили. Клиенты не трогали. `unmanagedAttributePolicy` не задавали.

## Что

В realm `qaguru` объявлен атрибут `selenoidToken` (edit `user`+`admin`), группа **Selenoid**. Поле само появляется в Account Console — своей страницы нет.

SSOT: [`../realm/user-profile-selenoid-token.json`](../realm/user-profile-selenoid-token.json). Живой PUT: `python scripts/apply-user-profile.py` / `--prod`.

## Пробы

| Где | поле | сохранить | после reload |
|-----|------|-----------|--------------|
| стенд `:8543` | `Токен Selenoid` | `student-demo` | значение на месте, потом очистили |
| прод [кабинет](https://auth.qa.guru/realms/qaguru/account/) | то же | `staff-pilot` | то же; исходное пустое вернули |

117 людей на проде до и после PUT. Клиентов 16, как было.
