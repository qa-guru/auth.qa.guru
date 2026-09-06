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
python3 ./deploy/smtp.py apply
```

`bootstrap-host.sh` ставит ufw, PostgreSQL (пакет), Docker, nginx, certbot.
`install.sh` кладёт env 600, создаёт роль БД, копирует realm из wrapper, поднимает Keycloak.
`configure-tls.sh` ставит LE и **обязательный** deploy-hook `nginx -t && systemctl reload nginx`.

`backup-restore-test.sh` **дропает живую БД** — он для пустого realm (критерий P2a). На
заполненном realm он refuse; restorability проверяется неразрушающе через `offbox.py restore-check`.

## SMTP (сброс пароля)

Кнопка «Забыли пароль?» уже в realm. Секрет — `~/.config/auth-qa-guru/smtp.env` (600), его `apply` вливает в `keycloak.env` и на хост. Сброс **по username**; `loginWithEmailAllowed` не включать. Не рассылать на realm — только один username.

From — `noreply@qaguru.ru`: Beget SMTP требует MX Beget, а у `qa.guru` MX на Яндексе.

```bash
python3 deploy/smtp.py apply
python3 deploy/smtp.py test --email <один-свой-ящик>
python3 deploy/smtp.py probe-reset    # одна учётка, IMAP, смена пароля, удаление
python3 deploy/smtp.py send-reset <username>
```

## Off-box бэкап

Off-box копия — **не опция**. До 2026-09-05 `keycloak-pg-dump.sh` молча пропускал S3, если
`/etc/keycloak/s3.env` нет, и выходил с кодом 0: таймер выглядел здоровым, а единственные копии
realm с 99 живыми людьми лежали на диске той же VM. Теперь отсутствие приёмника **валит юнит**
(локальный dump при этом всё равно снимается).

Приёмник — Selectel Object Storage, пул `ru-1`, path-style. Писатель — отдельный сервисный
пользователь с ролью **`s3.admin`, scoped на один проект**: утёкший ключ достаёт эти бакеты и
ничего больше в аккаунте. `s3.user` / `s3.bucket.user` тоже подошли бы, но только вместе с
bucket policy — второе место, где можно ошибиться, без выигрыша.

```bash
python3 deploy/offbox.py provision            # dry-run
python3 deploy/offbox.py provision --apply    # сервис-юзер + ключ + бакет + s3.env + ретеншн
python3 deploy/offbox.py status               # настроен ли, насколько свеж последний объект
python3 deploy/offbox.py verify               # запустить РЕАЛЬНЫЙ юнит и убедиться, что объект лёг
python3 deploy/offbox.py restore-check        # restore новейшего OFF-BOX объекта в scratch-БД
python3 deploy/offbox.py set-retention --days 30
```

`provision --apply` требует у identity в `~/.config/selectel-iam.json` роль **`iam.admin`**
(или Account Owner): выдача S3-ключей — операция IAM, роль `member` её не даёт. Ключ пишется
сразу в `/etc/keycloak/s3.env` (root 600) **через stdin** — не через argv, чтобы не светиться
в `ps` и history.

Ретеншн двухуровневый: локально 14 дней (`find -mtime`), off-box 30 (bucket lifecycle) —
off-box живёт дольше, потому что его смысл в переживании потери хоста.

Ключи Hetzner из аудита 2026-07 отозваны (`InvalidAccessKeyId`) — не возвращать.
