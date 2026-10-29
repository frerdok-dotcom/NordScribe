## Summary

<!-- What changed and why? Link the issue if there is one. -->

## Changes

<!-- Bullet list of user-visible and internal changes. -->

## Test plan

- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `mypy`
- [ ] `pytest`
- [ ] `python -m nordscribe --selftest` (if the UI or startup changed)
- [ ] Manual check of affected screen(s)

## Checklist

- [ ] `CHANGELOG.md` updated for user-visible changes
- [ ] No build artefacts, logs, weights or `.codegraph/codegraph.db` committed
- [ ] Architecture rules in `AGENTS.md` respected
- [ ] `codegraph sync .` run if files or public symbols moved
