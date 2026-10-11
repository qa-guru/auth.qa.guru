# auth.qa.guru

Production **Keycloak** IdP — [https://auth.qa.guru](https://auth.qa.guru)

Единый вход школы (фаза `11.qa-guru-identity`, ADR 017 в монорепе). **Всё про IdP — в этом репозитории:** деплой-кит, стенд, realm as code, login-тема, скрипты и сводки. Других копий нет.

| | |
|--|--|
| URL | [https://auth.qa.guru](https://auth.qa.guru) — корень → кабинет realm `qaguru` (тот же вход, что в Jenkins). Админка master — только `./deploy/admin.sh` (SOCKS через VM); снаружи `/realms/master/` и `/admin/` — 403 |
| Репозиторий | [qa-guru/auth.qa.guru](https://github.com/qa-guru/auth.qa.guru) |
| Status | **P0–P9 ✓**. Живая сверка 2026-09-08 — [`dev/summaries/qaguru-live-matrix.md`](dev/summaries/qaguru-live-matrix.md). Приёмка — `staff-pilot` / `mentor-pilot` / `student-pilot`, не `svasenkov` |
| Движок | Keycloak **26.7.3** (`start --optimized`, не start-dev; passkeys штатно с 26.4) |
| Realm | `qaguru` — as code, [`dev/realm/qaguru-realm.json`](dev/realm/qaguru-realm.json). SSO: idle **2 суток**, max **7 суток**; Remember Me — 7 / 14. Access token 5 мин не удлиняем |
| Стенд | [`dev/`](dev/README.md) — из корня монорепы `python scripts/stands/ensure.py auth-keycloak` → **:8543** |
| Dest cloud | клиент **`autotests-ai`** (не `learn`). Secret `KC_CLIENT_SECRET_AUTOTESTS_AI` / Home `IDP_CLIENT_SECRET`, не `VITE_`. Redirect `/oauth/idp/callback`. [`dev/scripts/ensure-autotests-ai-client.py`](dev/scripts/ensure-autotests-ai-client.py) · `--prod` |
| Хост | отдельная облачная VM Selectel `SL1.2-4096-32` ru-1c, IP `95.213.181.139` — **не** Box2 / Box3 / Box4 |
| Path | `/opt/auth.qa.guru` |
| БД | PostgreSQL **нативный пакет + systemd** (не контейнер: docker-published порты обходят ufw) |
| Секреты | прод — `/etc/keycloak/keycloak.env` root 600 и `~/.config/auth-qa-guru/keycloak.env`, **не Vault** (ADR 017 п. 12); стенд — `dev/.env` (600) |
| SMTP | сброс по username, From `noreply@qaguru.ru` (Beget). `deploy/smtp.py`. Стенд — Mailpit. `loginWithEmailAllowed` выкл. |
| Login theme | `qaguru` — [`dev/themes/qaguru/`](dev/themes/qaguru/); прод `deploy/theme.py apply` (rsync в `:ro` bind mount, без stop IdP). Вход в `master` — стоковый `keycloak.v2` |
| Passkey RP ID | `qa.guru` (общий родитель) |
| Кэш | **`KC_CACHE=local`** — один узел не кластеризуется, а `ispn` при `network_mode: host` открывал JGroups 7800 / 57800 на публичном IP |
| Бэкап | суточный `pg_dump` → Selectel S3 (`deploy/offbox.py`); off-box копия обязательна, её отсутствие валит юнит |
| SSH | `ssh auth-qa-guru` |

## Структура

| Путь | Назначение |
|------|------------|
| [`Dockerfile`](Dockerfile) | `kc.sh build --db=postgres` → образ для `start --optimized` |
| [`docker-compose.yml`](docker-compose.yml) | прод: только Keycloak, `network_mode: host`, HTTP `127.0.0.1:8080` |
| [`nginx/auth.qa.guru.nginx`](nginx/auth.qa.guru.nginx) | TLS vhost → loopback |
| [`deploy/`](deploy/README.md) | bootstrap, DNS, TLS, backup, smoke, `verify-prod.py`, `admin.sh` |
| [`deploy/offbox.py`](deploy/offbox.py) | off-box приёмник: provision / status / verify / restore-check |
| [`deploy/smtp.py`](deploy/smtp.py) | SMTP сброса пароля: apply / test / probe-reset / send-reset (один username) |
| [`deploy/theme.py`](deploy/theme.py) | Login-тема `qaguru`: apply / status / login-check. Без compose up |
| [`dev/`](dev/README.md) | стенд (compose + Mailpit), realm, тема, скрипты (`--prod` — живой IdP), сводки |
| `realm/`, `themes/` | промежуточные папки деплоя: `install.sh` / `theme.py` заполняют их из `dev/` перед rsync; в git не лежат |

Секреты (не в git): `~/.config/auth-qa-guru/keycloak.env`, `dev/.env`.

## Что уже решено (не переоткрывать)

- **Один IdP на всё**, включая будущий `learn.qa.guru`. `niffler-auth` остаётся учебным Spring AS.
- **`username` = handle контура**, email вторичен: в data room 183 email-коллизии. Realm поэтому разрешает дубли email и запрещает вход по email.
- **У существующих учёток побеждает текущий username** — на нём висят права и авторство в Jenkins и Jira.
- **Keycloak обязан отдавать оба протокола**: Sonar Community Build умеет только SAML, остальные OIDC.
- **Break-glass остаётся в каждом сервисе** — падение IdP не должно забирать доступ к проду.
- **LDAP не будет.** Jira SAML ✓ и Confluence SAML ✓ 2026-09-07 (клиенты раздельные, не фаза 11). Директорию не поднимать.
- **Вход через GitHub** — brokering на проде закрыт в P5. На стенде по-прежнему тестовый OAuth App, не школьный. Mapper `github-to-students` кладёт GitHub-вход в `/students`. Дубли handle↔GitHub собирает [`dev/scripts/relink-github-duplicates.py`](dev/scripts/relink-github-duplicates.py). Join (тиры A–D) — [`join-github-handles.py`](dev/scripts/join-github-handles.py) только `--dry-run`. Apply A+B — `relink --prod --from-join` ([сводка](dev/summaries/github-handle-join-apply.md)).
- **Login-тема `qaguru`** — общее лицо входа (P3 leftover). CSS overlay на `keycloak.v2`, клиенты не трогаем. Прод: `python3 deploy/theme.py apply` без stop IdP. Вход в admin (`master`) — стоковый `keycloak.v2`; корень auth.qa.guru → кабинет `qaguru` (2026-10-11).
- **SSO-сессия** — idle 2 суток, max 7 суток; Remember Me 7 / 14. Не дефолт Keycloak (30 мин). Живой PUT: `python dev/scripts/apply-session.py --prod`. Access token 5 мин не удлиняем.
- **Админка только через VM** — `/realms/master/` и консоли снаружи 403, вход `deploy/admin.sh` (SOCKS). Открыты API `/admin/realms/…` и токен master — для сервисов и deploy-скриптов (2026-10-11, ADR 017 п. 19).
- **`selenoidToken`** — атрибут User Profile, поле в Account Console. Окно 1 плана персональных токенов (монорепа, `docs/plans/selenoid-personal-tokens.md`). Окно 2: клиент `svc-selenoid-token` (`view-users`), валидатор на Box1. User Profile и роли `svc-provisioning` не режем.
- **Один репозиторий** — стенд, realm, тема и скрипты живут здесь, в `dev/`; копий в монорепе нет (2026-10-11, ADR 017 п. 20).

P2b завёл живого staff `svasenkov` и `student-pilot`. Приёмка staff с 2026-09-07 — `staff-pilot` (как `mentor-pilot` / `student-pilot`), не пароль живого человека. P3 добавил клиент `oauth2-proxy`. P4 смигрировал людей Jenkins. Пол `AUTH_PEOPLE_FLOOR=99`. Стендовых demo (`seed-stand-users.py`) на проде нет.

`verify-prod.py` считает людей **полом** (`AUTH_PEOPLE_FLOOR`, сейчас 99), а не allowlist-ом: проверка «в realm только пилоты» покраснела в тот же момент, когда фаза поехала дальше, и перестала ловить реальную потерю учёток. Гейт ADR 017 спрашивает обратное — не потеряли ли мы кого-то.

Монорепа: указатель `projects/services-home/auth-qa-guru-home/README.md`, ADR 017, план `docs/plans/qa-guru-identity.md`.
