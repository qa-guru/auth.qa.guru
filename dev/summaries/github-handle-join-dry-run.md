# GitHub → handle join — dry-run 2026-09-08

**Realm:** [auth.qa.guru](https://auth.qa.guru) · ADR 017 §5 `duplicateEmailsAllowed`  
**Script:** [`../scripts/join-github-handles.py`](../scripts/join-github-handles.py) (`--prod --dry-run` only)  
**JSON:** `~/.config/auth-qa-guru/github-handle-join-dry-run.json` (mode 600)

GitHub → school handle, not the reverse. Ghosts not deleted. Staff SID on a Jenkins job is not a student handle.

## Counts

| Tier | n | Apply this window |
|------|--:|-------------------|
| A login == handle, unique in ДЗ or SCM | 21 | no |
| B unique email-relink, maps do not contradict | 0 | no |
| C unique xor / review | 1251 | no |
| D conflict / skip | 8 | no |

A: **20** handle in realm without GitHub federated identity (`attach`), **1** already linked, **0** missing in Keycloak.  
Email-relink (same `plan_actions` as [`relink-github-duplicates.py`](../scripts/relink-github-duplicates.py)): merge **0**, join_students **0**, skip **0**.

### C reasons

| reason | n |
|--------|--:|
| xor_hw_only (unique GitHub in ДЗ, not in SCM, not a live handle) | 1199 |
| both_seen_no_handle (in ДЗ unique and SCM, no live-student handle) | 43 |
| xor_scm_only_no_handle | 5 |
| kc_only | 4 |

### D reasons

| reason | n |
|--------|--:|
| hw_shared (one GitHub, 2+ students in ДЗ) | 7 |
| skip_staff_handle | 1 |

## Realm gate

Federated GitHub **before 11 users / 11 links**, **after 11 / 11**. No POST, no disable, no ghost email rewrite.

People in realm: **113**. GitHub IdP users: **11** (4 live-student username, 1 staff, 6 username ≠ live handle — all six already in `/students` via mapper). Even ignoring `/students`, unique email → one live-student is still **0**.

## Apply (this window, 2026-09-08)

A+B only via [`relink-github-duplicates.py --prod --from-join`](../scripts/relink-github-duplicates.py). Join stays `--dry-run` only. **POST 0**: 20 A_attach have no GitHub `userId`, B empty, 1 already linked. Federated GitHub **11 / 11** before and after. Ghosts not deleted. C/D not touched.

Сводка apply: [`github-handle-join-apply.md`](github-handle-join-apply.md). JSON 600: `~/.config/auth-qa-guru/github-handle-join-apply.json`.

## C review (парковка 2026-09-08)

Review only. No POST, no `--from-join`, no D / GC / LDAP / P9.

`C_handles=[]`: `xor_scm_only` = 0; `xor_hw_only` с живым handle уже A. Строки C в JSON — только `{tier, reason}`.

Уникальный GitHub в ДЗ → один GetCourse `student_id`, не Jenkins handle. Из 1199 `xor_hw_only` почта L0 = ровно один live-student: **5**. Остальные 1191 в живом Jenkins нет. Handle из github.com не заводим (ADR 017: username = handle контура).

Пять (handle ← GitHub ДЗ): `026_bugaev_d` ← `bugaevd`, `alina999` ← `alina0698`, `arteml` ← `artl-hub`, `akinakaoin` ← `elenakamaleeva` (только почта), `zolotovskiy` ← `konstantinzolotovskiy` (+ второй unique `zolotovskiik`). Все в `/students`, GitHub пустой, `userId` нет — POST нечем. Следить, пока кто-то войдёт брокером.
