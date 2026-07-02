# Instruccions per a agents

Regles operatives per treballar en aquest repositori. Descripció del projecte, entitats, config flow, etc. → `README.md`. Detall arquitectural → `docs/`.

## Commits

- Conventional Commits estricte (`feat:`, `fix:`, `fix!:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`) — `release-please` en depèn per calcular versió i changelog; un subjecte mal format queda fora del release. Detall a `CONTRIBUTING.md`.
- No referenciïs números de tasca del pla (`T14`, `Task 5`) ni a commits ni a comentaris de codi — expliquen el *perquè*, no l'origen; el pla evoluciona i la referència queda òrfena.
- Mai editis a mà `version` a `pyproject.toml` o `custom_components/incendiscat/manifest.json` — només `release-please` els toca.

## Codi

- Comentaris/identificadors en anglès. Strings de cara a l'usuari (config flow, noms d'entity) via `_attr_translation_key` + `translations/{ca,es,en}.json` — català és la llengua de referència. Qualsevol entity o camp de config flow nou necessita clau a **els tres** fitxers o `hassfest` falla.
- Estat de la integració a `entry.runtime_data` (alias tipat `IncendiscatConfigEntry`), mai a `hass.data[DOMAIN]`.
- `DeviceInfo.entry_type=SERVICE` — és un servei cloud, no un dispositiu físic.
- Dades del FeatureServer ArcGIS (Bombers/Pla Alfa) no són d'una API oficial i poden canviar sense avís: accedeix a camps amb `.get()` + valor per defecte, mai indexació directa; qualsevol canvi ha de mantenir aquesta tolerància (docs/04-architecture.md §9).
- Camps de text externs (`municipi`, `tipus_desc`, etc.) són no fiables: mai `allow_html` ni interpolació HTML directa. Diagnostics ha de seguir redactant `latitude`/`longitude` abans d'exportar.
- Integració config-flow-only — no reintrodueixis suport YAML (`configuration.yaml`).

## Tests

- `pytest-homeassistant-custom-component` + `aioresponses`; zero xarxa real en tests.
- Lògica dependent del rellotge → fixture `clock` (`FakeClock` a `tests/conftest.py`), no `sleep()` real ni `freezegun`.
- Cobertura mínima 95% (`--cov-fail-under=95`, igual que CI) — comprova-ho abans de fer push.
- Fixtures ArcGIS a `tests/fixtures/` han de ser respostes reals capturades, no inventades.

## Abans d'obrir/actualitzar una PR

`ruff check .`, `ruff format --check .` i `pytest --cov=custom_components/incendiscat --cov-fail-under=95` en verd — són exactament les gates de `ci.yml`/`validate.yml`.
