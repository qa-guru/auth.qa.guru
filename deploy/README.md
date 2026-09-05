# Deploy auth.qa.guru

Прод-хост Keycloak. Стенд `dev/` в wrapper **не** трогать. Демо-людей (`seed-stand-users.py`) на прод не возить.

## Prerequisites

- SSH alias `auth-qa-guru` → VM из `provision-auth-qa-guru.py`
- `~/.config/auth-qa-guru/keycloak.env` — `./deploy/init-env.sh`
- Realm SSOT: `../dev/realm/qaguru-realm.json` (wrapper, не этот клон)

## Install order

Из корня клона, **с ноутбука**:

```bash
./deploy/init-env.sh
./deploy/bootstrap-host.sh
./deploy/install.sh
python3 ./deploy/configure-dns.py          # dry-run
python3 ./deploy/configure-dns.py --apply
./deploy/configure-tls.sh
./deploy/backup-restore-test.sh
./deploy/smoke.sh
```

`bootstrap-host.sh` ставит ufw, PostgreSQL (пакет), Docker, nginx, certbot.
`install.sh` кладёт env 600, создаёт роль БД, копирует realm из wrapper, поднимает Keycloak.
`configure-tls.sh` ставит LE и **обязательный** deploy-hook `nginx -t && systemctl reload nginx`.
