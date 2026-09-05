# auth.qa.guru

Production **Keycloak** IdP — **https://auth.qa.guru**

Единый вход школы (фаза `11.qa-guru-identity`, [ADR 017](https://github.com/qa-guru)). Realm as code живёт в monorepo wrapper `auth-qa-guru-home/dev/realm/` и копируется на хост при install — **не** править копию в этом репозитории.

| | |
|--|--|
| URL | https://auth.qa.guru |
| Движок | Keycloak **26.7.3** (`start --optimized`, не start-dev) |
| Хост | отдельная облачная VM Selectel `SL1.2-4096-32` ru-1c — **не** Box2 / Box3 / Box4 |
| Path | `/opt/auth.qa.guru` |
| БД | PostgreSQL **нативный пакет + systemd** (не контейнер: docker-published порты обходят ufw) |
| Секреты | `/etc/keycloak/keycloak.env` root 600 — **не Vault** |
| Passkey RP ID | `qa.guru` (общий родитель) |
| SSH | `ssh auth-qa-guru` |

## Структура

| Путь | Назначение |
|------|------------|
| [`Dockerfile`](Dockerfile) | `kc.sh build --db=postgres` → образ для `start --optimized` |
| [`docker-compose.yml`](docker-compose.yml) | только Keycloak, `network_mode: host`, HTTP `127.0.0.1:8080` |
| [`nginx/auth.qa.guru.nginx`](nginx/auth.qa.guru.nginx) | TLS vhost → loopback |
| [`deploy/`](deploy/) | bootstrap, DNS, TLS, backup, smoke |

Секреты (не в git): `~/.config/auth-qa-guru/keycloak.env`.

Monorepo wrapper: `projects/services-home/auth-qa-guru-home/`.
