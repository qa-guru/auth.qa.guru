# dev — локальный стенд Keycloak

Окно **P1** фазы `11.qa-guru-identity`. Прод не трогается: ни DNS, ни VM, ни конфиг ни одного сервиса.

| Путь | Что это |
|------|---------|
| [`docker-compose.yml`](docker-compose.yml) | Keycloak 26.7.3 + PostgreSQL 16, порт **8543** на `127.0.0.1` |
| [`realm/qaguru-realm.json`](realm/qaguru-realm.json) | Realm as code. **Единственный источник конфигурации** — руками в admin UI ничего не досоздаётся |
| [`themes/qaguru/`](themes/qaguru/) | Login-тема (лицо входа). Parent `keycloak.v2`, CSS overlay |
| `.env` | Секреты стенда, режим 600, в git не попадает. Шаблон — [`.env.example`](.env.example) |
| `scripts/` | Поднять, засеять демо-людей, проверить, экспортировать |

## Быстрый старт

```bash
./scripts/init-env.sh                                  # .env со случайными секретами
python ../../../../../scripts/stands/ensure.py auth-keycloak   # поднять и дождаться 2xx
python scripts/seed-stand-users.py                     # демо-люди (только стенд)
python scripts/grant-provisioning-roles.py             # realm-management → svc-provisioning (P8)
python scripts/ensure-selenoid-token-client.py         # view-users → svc-selenoid-token (ферма, не режет P8)
python scripts/ensure-groups.py                        # missing groups from realm JSON (live, no reimport)
python scripts/apply-smtp.py                           # SMTP → Mailpit (живой realm, без down -v)
python scripts/apply-theme.py                          # login theme qaguru (живой realm, без down -v)
python scripts/apply-session.py                        # SSO 2d idle / 7d max (живой realm, без down -v)
node scripts/theme-login-check.mjs                     # форма + CSS + student-demo
python scripts/apply-user-profile.py                   # selenoidToken в User Profile (живой realm, без down -v)
node scripts/account-selenoid-token-check.mjs          # поле видно и сохраняется в Account Console
python scripts/learn.py seed-env                       # P9: секрет клиента learn + break-glass (дописывает .env)
python scripts/learn.py apply-client                   # P9: клиент learn на живом realm, без down -v
python scripts/ensure-autotests-ai-client.py       # dest cloud: клиент autotests-ai, не learn
python ../../../../../scripts/stands/ensure.py learn-oidc-stub  # P9 smoke RS :3040
python scripts/learn.py login-check                    # staff / mentor / student видят разное
python scripts/verify-realm.py                         # приёмка по ADR 017
./scripts/smoke.sh                                     # OIDC + SAML + форма входа + «Забыли пароль?»
node scripts/passkey-login-check.mjs                   # вход по passkey и парольный fallback
node scripts/reset-password-check.mjs                  # сброс по username: письмо в Mailpit → новый пароль
```

Admin UI — [http://localhost:8543/admin/master/console/](http://localhost:8543/admin/master/console/), логин из `KC_BOOTSTRAP_ADMIN_*`. Короткий `/admin` отвечает 302, поэтому в ссылки его не класть: `stand-url-gate` требует ровно 2xx.
Вход человека — [http://localhost:8543/realms/qaguru/account/](http://localhost:8543/realms/qaguru/account/).

Погасить: `docker compose down` из этого каталога. **Не** `ensure.py --stop`: на published-порту слушает Docker backend, и стоп прибьёт сеть всех контейнеров.

## Проверка realm на чистом volume

Критерий P1 — realm воспроизводится из файла, а не из чьей-то памяти:

```bash
docker compose down -v && python ../../../../../scripts/stands/ensure.py auth-keycloak
```

`--import-realm` импортирует только отсутствующий realm, поэтому правку `realm/qaguru-realm.json` надо проверять именно так. User Profile (`selenoidToken`) в импорт не входит — после чистого volume: `python scripts/apply-user-profile.py`. Обратный диф (что сервер реально держит против того, что в файле) — `python scripts/export-realm.py`, результат в `export/` и он gitignored: это генерат с сотнями строк дефолтных флоу, а источник правды — рукописный файл.

## Что проверяет приёмка

`verify-realm.py` бьётся о решения ADR 017, а не о дефолты Keycloak:

| Решение | Как проверяется |
|---|---|
| `username` = handle, email вторичен | вход по email выключен, дубли email разрешены; `mentor-demo` и `student-demo` намеренно делят один email |
| Существующий username не переименовывается | `editUsernameAllowed: false` |
| Passkey + обязательный fallback | passkeys включены, RP ID из env, парольный вход и сброс пароля живы |
| SMTP для сброса | host из env (стенд — Mailpit), секрет `${KC_SMTP_PASSWORD}`; `loginWithEmailAllowed` остаётся false |
| Оба протокола | `sonar` / `jira` / `confluence` — SAML; `grafana` / `jenkins` / `gitlab` / `testops` / `oauth2-proxy` / `learn` / `autotests-ai` — OIDC |
| Машинный трафик — токены | `svc-provisioning`: service account есть, интерактивный вход выключен. `svc-selenoid-token`: только `view-users`+`query-users` |
| Группы в claim | OIDC отдаёт `/students` со слешем (Jenkins Role Strategy ждёт ровно так), SAML для Sonar — `students` без слеша. TestOps: `/staff` ADMIN, `/mentors` `/contour` `/students` USER |
| В git нет секретов | все secret-поля realm-файла обязаны быть `${ENV}`-плейсхолдерами |
| Личный токен Selenoid | атрибут `selenoidToken` в User Profile, `user` может редактировать; `unmanagedAttributePolicy` не задан |
| SSO-сессия | idle 2 суток, max 7 суток; Remember Me 7 / 14 — не дефолт 30 мин |

`passkey-login-check.mjs` гоняет полный круг passkey на виртуальном FIDO2-аутентификаторе из CDP: регистрация, сброс cookies, вход без пароля. Доказательство входа — счётчик подписей ключа, он двигается только при реальном WebAuthn-assertion.

## GitHub brokering

Единственный шаг, который нельзя выпустить скриптом: у GitHub **нет API** ни на создание, ни на правку OAuth App. На стенде приложение должно быть **своё тестовое**, не школьное.

В realm-файле провайдер лежит с `"enabled": false` — включённая кнопка с плейсхолдерными credentials ведёт всех в никуда (на проде это нашли живым дефектом в P5). Включает провайдер тот же скрипт, что кладёт настоящие credentials: `apply-github-idp.py` на стенде, `oidc.py seed-idp` на проде.

1. [github.com/settings/developers](https://github.com/settings/developers) → **New OAuth App**
   - Homepage URL: `http://localhost:8543`
   - Authorization callback URL: `http://localhost:8543/realms/qaguru/broker/github/endpoint`
2. `KC_GITHUB_CLIENT_ID` и `KC_GITHUB_CLIENT_SECRET` в `.env`.
3. `python scripts/apply-github-idp.py` — плейсхолдеры подставляются только на `--import-realm`, а это патчит IdP на живом стенде, не снося зарегистрированные passkey. Чистый импорт из того же `.env` даёт ровно ту же конфигурацию.
4. Кнопка **GitHub** на [странице входа](http://localhost:8543/realms/qaguru/account/) → войти.
5. `python scripts/verify-realm.py` — проверка ищет federated identity `github` на сервере.

Account linking: `firstBrokerLoginFlowAlias` — штатный `first broker login`. Он **не** склеивает по email, пока `duplicateEmailsAllowed` (ADR 017 §5): `Create User If Unique` считает уникальным username. Mapper `github-to-students` (`FORCE`) всё равно кладёт GitHub-вход в `/students` — New Item на Jenkins есть даже у нового GitHub-логина. Если GitHub login ≠ handle, вторую учётку склеивает [`relink-github-duplicates.py`](scripts/relink-github-duplicates.py). GitHub → handle, не наоборот. `/contour` mapper не выдаёт. Join-классификация — [`join-github-handles.py`](scripts/join-github-handles.py) (`--prod --dry-run`). Apply A+B — `relink --prod --from-join`.

## SMTP и «Забыли пароль?»

Кнопка уже на форме входа (`resetPasswordAllowed`). Письмо не уходило, пока у realm не было `smtpServer`. На стенде это Mailpit в том же compose (SMTP только внутри сети, UI на `127.0.0.1:8025`, не стенд из реестра). Секрет — `KC_SMTP_PASSWORD` в `.env` (600). Сброс **по username**: `loginWithEmailAllowed` не включать.

```bash
python scripts/apply-smtp.py                 # патч живого realm, без down -v
node scripts/reset-password-check.mjs        # username → письмо → новый пароль → вход
python scripts/apply-theme.py                # loginTheme=qaguru на qaguru; master — стоковый keycloak.v2
node scripts/theme-login-check.mjs           # CSS + форма + student-demo
```

`--import-realm` подставляет `${KC_SMTP_*}` только на чистом volume. Живой стенд патчит `apply-smtp.py`, как GitHub — `apply-github-idp.py`. На прод тот же паттерн: `deploy/smtp.py`, секрет в `/etc/keycloak/keycloak.env` root 600. Не рассылать на весь realm — скрипт принимает ровно один username.

## SSO-сессия

Дефолт Keycloak — idle **30 мин**, max **10 ч**, Remember Me наследует idle (0). На живом realm без down -v:

```bash
python scripts/apply-session.py                 # стенд :8543, qaguru и master
python scripts/apply-session.py --prod          # https://auth.qa.guru
python scripts/apply-session.py --prod --verify-only
```

SSOT — поля `ssoSession*` в [`realm/qaguru-realm.json`](realm/qaguru-realm.json): idle 2 суток, max 7 суток; Remember Me 7 / 14. Access token не трогаем (5 мин).

## User Profile: `selenoidToken`

Атрибут не входит в `--import-realm`. Живой PUT, без down -v и без PUT клиентов:

```bash
python scripts/apply-user-profile.py                 # стенд :8543
node scripts/account-selenoid-token-check.mjs
python scripts/apply-user-profile.py --prod         # https://auth.qa.guru
node scripts/account-selenoid-token-check.mjs --prod  # staff-pilot, не svasenkov
```

SSOT — [`realm/user-profile-selenoid-token.json`](realm/user-profile-selenoid-token.json). `unmanagedAttributePolicy` не задаём. Ферму, nginx и htpasswd скрипт не трогает.

## Что осталось за границей окна

Прод-конфиг любого сервиса, DNS, заказ VM (**P2**), LDAP, Jira / Confluence, Vault, `/signup` Jenkins. Клиенты `grafana` / `jenkins` / `gitlab` / `testops` / `sonar` здесь — **заготовки** с прод-URL редиректов и стендовыми секретами; `oauth2-proxy` включён в P3 (Selenoid UI). Настоящие секреты — на VM, не в git.

Клиент `learn` (P9) — OIDC + PKCE, smoke RS на [:3040](http://localhost:3040/). Это стык с LMS, не продукт: курсов нет, живой `qa-guru-backend` сюда не подключали. Break-glass заглушки — `POST /login/system`, проверяется `python scripts/learn.py break-glass` (`docker compose stop keycloak`, не `ensure.py --stop`).

Клиент `autotests-ai` — dest cloud на [autotests.ai](https://autotests.ai), не LMS. Confidential, secret на бэкенде Home (`IDP_CLIENT_SECRET`), не `VITE_`. Redirect `/oauth/idp/callback`. `python scripts/ensure-autotests-ai-client.py` (стенд) и `--prod`. Прод-секрет: `~/.config/auth-qa-guru/autotests-ai.env` (600), не git.

Что ещё не проверено стендом и всплывёт на живых учётках: у Sonar синхронизация групп работает только с JIT-provisioning и перечитывает группы при каждом входе, у GitLab правка group links может выкинуть людей из группы. Поэтому инвентарь до переключения снимается и по пользователям, и по составу групп.
