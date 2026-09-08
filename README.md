# auth.qa.guru

Production **Keycloak** IdP — [https://auth.qa.guru](https://auth.qa.guru)

Единый вход школы (фаза `11.qa-guru-identity`). Realm as code живёт в monorepo wrapper `auth-qa-guru-home/dev/realm/` и копируется на хост при install — **не** править копию в этом репозитории.

| | |
|--|--|
| URL | [https://auth.qa.guru](https://auth.qa.guru) |
| Репозиторий | [qa-guru/auth.qa.guru](https://github.com/qa-guru/auth.qa.guru) |
| Движок | Keycloak **26.7.3** (`start --optimized`, не start-dev) |
| Хост | отдельная облачная VM Selectel `SL1.2-4096-32` ru-1c, IP `95.213.181.139` — **не** Box2 / Box3 / Box4 |
| Path | `/opt/auth.qa.guru` |
| БД | PostgreSQL **нативный пакет + systemd** (не контейнер: docker-published порты обходят ufw) |
| Секреты | `/etc/keycloak/keycloak.env` root 600 — **не Vault** |
| SMTP | сброс по username, From `noreply@qaguru.ru` (Beget). `deploy/smtp.py`. `loginWithEmailAllowed` выкл. |
| Login theme | `qaguru` — `deploy/theme.py apply` (rsync + docker cp, без stop IdP) |
| Passkey RP ID | `qa.guru` (общий родитель) |
| Кэш | **`KC_CACHE=local`** — один узел не кластеризуется, а `ispn` при `network_mode: host` открывал JGroups 7800 / 57800 на публичном IP |
| Бэкап | суточный `pg_dump` → Selectel S3 (`deploy/offbox.py`); off-box копия обязательна, её отсутствие валит юнит |
| SSH | `ssh auth-qa-guru` |

## Структура

| Путь | Назначение |
|------|------------|
| [`Dockerfile`](Dockerfile) | `kc.sh build --db=postgres` → образ для `start --optimized` |
| [`docker-compose.yml`](docker-compose.yml) | только Keycloak, `network_mode: host`, HTTP `127.0.0.1:8080` |
| [`nginx/auth.qa.guru.nginx`](nginx/auth.qa.guru.nginx) | TLS vhost → loopback |
| [`deploy/`](deploy/) | bootstrap, DNS, TLS, backup, smoke |
| [`deploy/offbox.py`](deploy/offbox.py) | off-box приёмник: provision / status / verify / restore-check |
| [`deploy/smtp.py`](deploy/smtp.py) | SMTP сброса пароля: apply / test / probe-reset / send-reset (один username) |
| [`deploy/theme.py`](deploy/theme.py) | Login-тема `qaguru`: apply / status / login-check. Без compose up |

Секреты (не в git): `~/.config/auth-qa-guru/keycloak.env`.

P2b завёл живого staff `svasenkov` и `student-pilot`. Приёмка staff с 2026-09-07 — `staff-pilot` (как `mentor-pilot` / `student-pilot`), не пароль живого человека. P3 добавил клиент `oauth2-proxy`. P4 смигрировал людей Jenkins. Пол `AUTH_PEOPLE_FLOOR=99`.

`verify-prod.py` считает людей **полом** (`AUTH_PEOPLE_FLOOR`, сейчас 99), а не allowlist-ом: проверка «в realm только пилоты» покраснела в тот же момент, когда фаза поехала дальше, и перестала ловить реальную потерю учёток. Гейт ADR 017 спрашивает обратное — не потеряли ли мы кого-то.

Monorepo wrapper: `projects/services-home/auth-qa-guru-home/`.
