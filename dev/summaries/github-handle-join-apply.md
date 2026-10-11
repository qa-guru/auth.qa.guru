# GitHub → handle join — apply A+B 2026-09-08

**Статус:** **парковка 2026-09-08.** A+B ✓ (POST 0). 20 A_attach ждут GitHub-вход. C review уже в [dry-run](github-handle-join-dry-run.md). D skip. Потом, не этот чат: не C/D apply, не GC, не LDAP, не P9.

**Realm:** [auth.qa.guru](https://auth.qa.guru) · ADR 017 §5 `duplicateEmailsAllowed`  
**Join:** [`../scripts/join-github-handles.py`](../scripts/join-github-handles.py) (`--prod --dry-run` only)  
**Apply:** [`../scripts/relink-github-duplicates.py`](../scripts/relink-github-duplicates.py) `--prod --from-join`  
**JSON:** `~/.config/auth-qa-guru/github-handle-join-dry-run.json` (allowlist) · `~/.config/auth-qa-guru/github-handle-join-apply.json` (mode 600)

GitHub → school handle, not the reverse. Ghosts not deleted. C/D not applied. C review parked 2026-09-08 — [`github-handle-join-dry-run.md`](github-handle-join-dry-run.md) § C review.

## `--only` (A+B allowlist)

21 handles, B empty:

`anna1-89`, `artemaqa07`, `artemchurkin`, `aveanip`, `blankflyleaf`, `corvuscorvx`, `dashaizvekova`, `denor1999`, `elenayacenko`, `imvnbk`, `iskander-d`, `lazval`, `ma1m2`, `matuation`, `neoniore`, `originalba`, `serovaliz`, `slavafetisov`, `steppenwolf8777`, `thecatlucky`, `tumenbaevaj`

## Counts

| Action | n |
|--------|--:|
| POST federated-identity | **0** |
| merge (B) | 0 |
| join_students | 0 |
| skip_no_userid (A_attach) | 20 |
| skip_already_linked (`originalba`) | 1 |
| skip_missing_kc | 0 |
| ghosts deleted | 0 |
| C / D | not in `--only` |

A_attach has no GitHub `userId` in realm — no POST until a broker login exists. Login == handle: Create User If Unique should hit the existing username.

## Realm gate

Federated GitHub **before 11 users / 11 links**, **after 11 / 11**. Realm people **113** (unchanged). Join `--prod` without `--dry-run` still exits.
