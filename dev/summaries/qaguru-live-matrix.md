# Живая матрица Keycloak `qaguru` × `*.qa.guru`

**Снято:** 2026-09-08T00:00:44Z · realm [auth.qa.guru](https://auth.qa.guru) · пилоты `staff-pilot, mentor-pilot, student-pilot`, не `svasenkov`.

Не LDAP. Не P9 (клиент `learn` есть, продукт не спавнили). Секреты не в git и не в этом файле.

Источник правды realm-as-code — [`../realm/qaguru-realm.json`](../realm/qaguru-realm.json). Live export — gitignored `dev/export/`.

## Realm live vs git

- Клиенты (без builtin): git **10**, live **10**.
- Только git: `—`
- Только live: `—`
- Группы git: `/contour, /mentors, /staff, /students` · `/contour` live (хвост TestOps USER)
- Группы live extra: `—` (`stream-java-19` / `stream-python-5` сняты 2026-09-08)
- Группы missing live: `—`
- GitHub IdP git.enabled=`False` live.enabled=`True` — в файле **false** + `${ENV}` намеренно (кнопка без credentials).
- Людей в realm: **113**. Пилоты: `staff-pilot` yes, `mentor-pilot` yes, `student-pilot` yes. `svasenkov`: present-live-staff (живой staff, не приёмка).

### Осмысленный drift (не UUID / не masked secret / не default flow)

Расхождений по tracked-полям нет.

## `*.qa.guru` (GoDaddy DNS A/AAAA/CNAME)

| Host | DNS | HTTP | Класс | IdP / break-glass |
|------|-----|------|-------|-------------------|
| [allure-notifications.qa.guru](https://allure-notifications.qa.guru/) | CNAME qa-guru.github.io | 200  | public | GitHub Pages builder |
| [allure.niffler-stage.qa.guru](https://allure.niffler-stage.qa.guru/) | A 212.92.101.15 | 200  | zoo | niffler, own auth on auth.niffler.* |
| [allure.niffler.qa.guru](https://allure.niffler.qa.guru/) | A 212.92.101.15 | 200  | zoo | niffler, own auth on auth.niffler.* |
| [allure.notifications.qa.guru](https://allure.notifications.qa.guru/) | A 89.248.193.83 | 200  | public | 301 → allure-notifications |
| [allure.qa.guru](https://allure.qa.guru/) | A 89.248.193.83 | 200  | SSO A oidc | client `testops` · /login/system |
| [api.niffler-stage.qa.guru](https://api.niffler-stage.qa.guru/) | A 212.92.101.15 | 401  | zoo | niffler, own auth on auth.niffler.* |
| [api.niffler.qa.guru](https://api.niffler.qa.guru/) | A 212.92.101.15 | 401  | zoo | niffler, own auth on auth.niffler.* |
| [app.qa.guru](https://app.qa.guru/) | A 212.92.101.15 | 200  | other |  |
| [argocd.qa.guru](https://argocd.qa.guru/) | CNAME reflector.qa.guru | None URLError  | stale | dismantled K8s / leftover |
| [argocd.testing.qa.guru](https://argocd.testing.qa.guru/) | CNAME reflector.testing.qa.guru | None URLError  | stale | old K8s testing |
| [auth.niffler-stage.qa.guru](https://auth.niffler-stage.qa.guru/) | A 212.92.101.15 | 200  | teaching-auth | niffler-auth stage, not qaguru |
| [auth.niffler.qa.guru](https://auth.niffler.qa.guru/) | A 212.92.101.15 | 200  | teaching-auth | niffler-auth Spring AS (course), not qaguru |
| [auth.qa.guru](https://auth.qa.guru/) | A 95.213.181.139 | 200 → IdP | machine/idp | IdP itself |
| [auth.testing.qa.guru](https://auth.testing.qa.guru/) | CNAME reflector.testing.qa.guru | None URLError  | stale | old K8s testing |
| [autoconfig.qa.guru](https://autoconfig.qa.guru/) | CNAME autoconfig.beget.com | None URLError  | mail | mail autoconfig |
| [autodiscover.qa.guru](https://autodiscover.qa.guru/) | CNAME autodiscover.beget.com | None URLError  | mail | mail autoconfig |
| [book-club.qa.guru](https://book-club.qa.guru/) | A 212.92.101.15 | 200  | public | book club, public |
| [bookclub.qa.guru](https://bookclub.qa.guru/) | CNAME book-club.qa.guru | 200  | public | 301 → book-club |
| [confluence.qa.guru](https://confluence.qa.guru/) | A 212.92.101.15 | 200  | SSO A saml | client `https://confluence.qa.guru` · auth_fallback admin |
| [demo.qa.guru](https://demo.qa.guru/) | A 212.92.101.15 | 200  | public | one-page-form mirror, public |
| [dozzle.cb.qa.guru](https://dozzle.cb.qa.guru/) | A 212.92.101.15 | 200  | zoo | cb_* compose, not school SSO |
| [gitlab.qa.guru](https://gitlab.qa.guru/) | A 212.92.101.15 | 200  | SSO A oidc | client `gitlab` · local root |
| [grafana.qa.guru](https://grafana.qa.guru/) | A 89.248.193.83 | 200  | SSO A oidc | client `grafana` · password form admin |
| [harbor.qa.guru](https://harbor.qa.guru/) | CNAME reflector.qa.guru | None URLError  | stale | dismantled K8s / leftover |
| [harbor.testing.qa.guru](https://harbor.testing.qa.guru/) | CNAME reflector.testing.qa.guru | None URLError  | stale | old K8s testing |
| [hubble.qa.guru](https://hubble.qa.guru/) | CNAME reflector.qa.guru | None URLError  | stale | dismantled K8s / leftover |
| [hubble.testing.qa.guru](https://hubble.testing.qa.guru/) | CNAME reflector.testing.qa.guru | None URLError  | stale | old K8s testing |
| [jenkins.qa.guru](https://jenkins.qa.guru/) | A 89.248.193.83 | 200  | SSO A oidc | client `jenkins` · escapeHatch admin |
| [jira.qa.guru](https://jira.qa.guru/) | A 212.92.101.15 | 200  | SSO A saml | client `https://jira.qa.guru` · auth_fallback admin |
| [lab.qa.guru](https://lab.qa.guru/) | A 212.92.101.15 | 200  | public | course lab, public |
| [learn.qa.guru](https://learn.qa.guru/) | A 212.92.101.15 | 200  | SSO A oidc | client `learn` · parked P9, not this window |
| [lms.qa.guru](https://lms.qa.guru/) | A 212.92.101.15 | 200  | lms | LMS/registry; P9 not this window |
| [medium.cb.qa.guru](https://medium.cb.qa.guru/) | A 212.92.101.15 | 404  | zoo | cb_* compose, not school SSO |
| [minecraft.qa.guru](https://minecraft.qa.guru/) | A 188.68.221.217 | None URLError  | zoo | minecraft |
| [mods.minecraft.qa.guru](https://mods.minecraft.qa.guru/) | A 188.68.221.217 | None URLError  | zoo | minecraft |
| [niffler-stage.qa.guru](https://niffler-stage.qa.guru/) | A 212.92.101.15 | 200  | zoo | niffler, own auth on auth.niffler.* |
| [niffler.qa.guru](https://niffler.qa.guru/) | A 212.92.101.15 | 200  | zoo | niffler, own auth on auth.niffler.* |
| [ollama.qa.guru](https://ollama.qa.guru/) | A 212.41.6.230 | 401  | machine/idp | GPU spike, basic auth, not SSO |
| [panel.minecraft.qa.guru](https://panel.minecraft.qa.guru/) | A 188.68.221.217 | None URLError  | zoo | minecraft |
| [provisioning.qa.guru](https://provisioning.qa.guru/) | A 212.92.101.15 | 401  | lms | LMS/registry; P9 not this window |
| [qa.guru](https://qa.guru/) | A 212.92.101.15 | 200  | public | apex WP + /u/{handle} |
| [rancher.qa.guru](https://rancher.qa.guru/) | CNAME reflector.qa.guru | None URLError  | stale | dismantled K8s / leftover |
| [rancher.testing.qa.guru](https://rancher.testing.qa.guru/) | CNAME reflector.testing.qa.guru | None URLError  | stale | old K8s testing |
| [rare.cb.qa.guru](https://rare.cb.qa.guru/) | A 212.92.101.15 | 404  | zoo | cb_* compose, not school SSO |
| [realworld-stage.qa.guru](https://realworld-stage.qa.guru/) | A 212.92.101.15 | 200  | zoo | realworld teaching app |
| [realworld.qa.guru](https://realworld.qa.guru/) | A 212.92.101.15 | 200  | zoo | realworld teaching app |
| [school.qa.guru](https://school.qa.guru/) | CNAME fwd.getcourse.ru | 404  | public | GetCourse, not school IdP |
| [selenoid.qa.guru](https://selenoid.qa.guru/) | A 89.248.192.30 | 200 UI | public UI · Basic hub | guest hubAuth; `/wd/hub` htpasswd. oauth2-proxy снят 2026-09-08 |
| [sonar.qa.guru](https://sonar.qa.guru/) | A 89.248.193.83 | 200  | SSO A saml | client `sonar` · local admin |
| [vault.qa.guru](https://vault.qa.guru/) | A 95.213.181.141 | 200  | machine/idp | Vault token; Keycloak secrets stay off Vault |
| [welldone.cb.qa.guru](https://welldone.cb.qa.guru/) | A 212.92.101.15 | 404  | zoo | cb_* compose, not school SSO |
| [wp.qa.guru](https://wp.qa.guru/) | CNAME reflector.qa.guru | None URLError  | stale | dismantled K8s / leftover |
| [www.qa.guru](https://www.qa.guru/) | A 87.236.16.18 | 200  | public | Beget WP 301 → apex |
| [www1.qa.guru](https://www1.qa.guru/) | A 176.57.64.67 | 404  | external | www1 leftover — probe |

## Break-glass (IdP **не** гасили)

Форма/вызов локального входа при живом Keycloak. Полный `docker stop` IdP — в окнах P2b–P5 / Jira / Confluence, здесь не повторяли: активный чат, общий IdP.

| Сервис | Проба | HTTP | ok |
|--------|-------|------|----|
| jira.qa.guru | `/login.jsp?auth_fallback` | 200 | yes |
| confluence.qa.guru | `/login.action?auth_fallback` | 200 | yes |
| grafana.qa.guru | `/login` | 200 | yes |
| jenkins.qa.guru | `/login` | 200 | yes |
| gitlab.qa.guru | `/users/sign_in` | 200 | yes |
| sonar.qa.guru | `/sessions/new` | 200 | yes |
| allure.qa.guru | `/login/system` | 200 | yes |
| selenoid.qa.guru | `/wd/hub/status` | 401 | yes |

## Login-check пилотов (не svasenkov)

| Сервис | exit | пилоты в выводе | заметка |
|--------|------|-----------------|--------|
| jenkins | 0 | staff-pilot, mentor-pilot, student-pilot |  |
| gitlab | 0 | staff-pilot, student-pilot | mentor not in GitLab CE check (auth only) |
| grafana | 0 | staff-pilot, mentor-pilot, student-pilot | student denied by role_attribute_strict |
| sonar | 0 | staff-pilot, student-pilot |  |
| jira | 0 | staff-pilot, mentor-pilot, student-pilot |  |
| confluence | 0 | staff-pilot, mentor-pilot, student-pilot |  |
| allure | 0 | staff-pilot, mentor-pilot, student-pilot | parallel staff-pilot collided on Keycloak form; sequential retry OK |
| selenoid | — | — | UI public 2026-09-08; login-check IdP больше не про UI |

## Вне этого окна

- LDAP не поднимали.
- P9 / cut GC не спавнили; клиент `learn` сверён как запись realm, не как продукт.

Пересъём: `python projects/services-home/auth-qa-guru-home/auth.qa.guru/dev/scripts/reconcile-qaguru.py --prod`
