# Contribuir

## Entorn de desenvolupament

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements_dev.txt

.venv/bin/pytest --cov=custom_components/incendiscat --cov-fail-under=95
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Els quatre han de passar en verd abans d'obrir una PR — són exactament les comprovacions de `ci.yml` i `validate.yml` (hassfest + HACS).

## Missatges de commit — Conventional Commits (obligatori)

`release-please` llegeix l'historial de commits per calcular la següent versió i generar el changelog; un missatge mal format no es compta i queda fora del release.

```
<tipus>[!]: <descripció>

[cos opcional]

[BREAKING CHANGE: ... ]
```

Tipus habituals: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`. Un `!` després del tipus (`fix!:`) o un peu `BREAKING CHANGE:` marca un canvi incompatible.

No referenciïs números de tasca del pla d'implementació (`T14`, `Task 5`...) al missatge ni als comentaris de codi — el pla evoluciona i la referència queda desactualitzada; explica el *perquè* directament.

## Cicle de release

El projecte és pre-1.0 (`bump-minor-pre-major` actiu a `release-please-config.json`): mentre no arribem a `1.0.0` expressament, un `fix!`/`BREAKING CHANGE` bumpeja **minor**, no major.

1. Mergeja PRs normals a `main` amb Conventional Commits.
2. `release-please` manté (i actualitza) automàticament una PR `chore(main): release vX.Y.Z` amb el changelog i el bump de versió a `pyproject.toml` + `custom_components/incendiscat/manifest.json`.
3. No editis aquests dos camps de versió a mà — `release-please` és l'única font de veritat.
4. Mergejar aquesta PR crea el tag `vX.Y.Z` i la Release de GitHub automàticament.

Dependabot obre PRs setmanals per `github-actions` i `pip` (exclou `homeassistant`/`pytest-homeassistant-custom-component`, que es bumpegen a mà junt amb el core de HA).

## Tests

- `pytest-homeassistant-custom-component` + `aioresponses` — cap test fa xarxa real.
- Lògica dependent del rellotge: fes servir el fixture `clock` (`FakeClock` a `tests/conftest.py`), no `sleep()` real ni `freezegun`.
- Fixtures de resposta ArcGIS reals a `tests/fixtures/`; si canvies el parsing, actualitza-les amb dades capturades, no inventades.
