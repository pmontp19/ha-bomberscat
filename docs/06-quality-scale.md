# Quality Scale — `ha-incendiscat`

Apliquem la **[Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)** oficial de Home Assistant amb filtre **KISS i sentit comú**:

- **Objectiu realista**: bronze + silver complerts, la majoria de gold, alguns platinum.
- **No** aspirem a PR a `home-assistant/core` (la font no és API oficialment suportada) → no cal platinum al 100%.
- **Distribució**: HACS Custom Repository. Però la qualitat del codi ha de ser la mateixa que si fos core.

L'estat de cada regla es declara a [`custom_components/incendiscat/quality_scale.yaml`](../custom_components/incendiscat/quality_scale.yaml) (format oficial HA — hassfest valida que estigui present i ben format).

**Llegenda**: ✅ `done` · ⏳ `todo` (prioritzat més avall) · 🚫 `exempt` (no aplica, amb raó)

---

## Criteris de classificació (KISS)

Quan dubtem entre `done` i `todo`, mirem:

1. **És gairebé gratis?** (1-5 línies de codi, patró ben documentat) → fer-ho ja, marcar `done`.
2. **És valor real per l'usuari?** (diagnosi, recovery, traduccions) → fer-ho en v1.
3. **És coixí per a futures integracions?** → marcar `todo` i tornar-hi.
4. **No aplica al nostre context?** (auth, discovery, devices físics) → `exempt` amb comentari.

Quan una regla és `exempt`, **sempre** portem un comentari d'una línia explicant per què. Així un revisor extern (o nosaltres d'aquí 6 mesos) entén la decisió.

---

## Resum per tier

| Tier | Done | Todo | Exempt | Total |
| :---: | ---: | ---: | ---: | ---: |
| 🥉 Bronze | 13 | 3 | 4 | 20 |
| 🥈 Silver | 7 | 1 | 2 | 10 |
| 🥇 Gold | 11 | 4 | 6 | 21 |
| 🏆 Platinum | 2 | 1 | 0 | 3 |
| **Total** | **33** | **9** | **12** | **54** |

> **Recompte verificat 2026-07-02** contra codi real (183 tests, 98% coverage). Els `todo` no bloquegen v0.1.0 — són backlog de qualitat.

---

## 🥉 Bronze — base obligatòria

### ✅ Ja complerts

| Regla | Com |
| --- | --- |
| `appropriate_polling` | 5 min incidents, 2×/dia Pla Alfa — justificat al spec |
| `common_modules` | `arcgis.py`, `models.py`, `geo.py`, `icons.py` separats |
| `config_flow` | Setup via UI, 2 passes |
| `config_flow_test_coverage` | `test_config_flow.py` cobreix el flux complet |
| `dependency_transparency` | `requirements: []` al manifest (zero deps externes) |
| `entity_unique_id` | `act_num` per `geo_location`, `entry_id+name` per sensors |
| `has_entity_name` | A `_attr_has_entity_name = True` |
| `runtime_data` | `entry.runtime_data` + alias tipat (no `hass.data`) |
| `test_before_setup` | `async_config_entry_first_refresh()` al setup |

### ⏳ Pendents (tots barats, ferm per v0.1.0)

| Regla | Esforç | Què |
| --- | ---: | --- |
| `brands` | M | `icon.png` 256×256 + `logo.png` per la marca. Necessari per aparèixer a HACS amb thumbnail. |
| `test_before_configure` | S | Al config flow, fer un GET petit al FeatureServer (`?resultRecordCount=1`) per validar abans de guardar l'entry |
| `unique_config_entry` | XS | `async_abort_if_unique_entry_configured()` al config flow |
| `docs_high_level_description` | S | Secció inicial del README explicant què és Incendis Catalunya |
| `docs_installation_instructions` | S | Secció "Instal·lació via HACS" pas a pas |
| `docs_removal_instructions` | XS | Secció "Eliminar" al README |
| `docs_triggers` | S | Documentar els events `incendiscat_fire_detected`/`_resolved`/`_phase_change` per a `trigger` |
| `docs_conditions` | S | Documentar com fer condicions amb els attributes (p.ex. `state_attr(...).fase == 'Actiu'`) |

### 🚫 Exempts

| Regla | Raó |
| --- | --- |
| `action_setup` | No exposem service actions — el patró escollit és events + blueprint |
| `docs_actions` | Conseqüència de l'anterior |
| `entity_event_setup` | No ens subscribim a events d'entity; disparem bus events |
| (`docs_*` condicionals) | Mantenir consistents amb `action_setup`/`entity_event_setup` exempts |

---

## 🥈 Silver — robustesa runtime

### ✅ Ja complerts

| Regla | Com |
| --- | --- |
| `integration_owner` | `codeowners: ["@pmontp19"]` al manifest |

### ⏳ Pendents (barats i d'alt valor)

| Regla | Esforç | Què |
| --- | ---: | --- |
| `config_entry_unloading` | XS | Implementar `async_unload_entry`: neteja coordinators, forward unload als platforms |
| `entity_unavailable` | S | Quan `service_connected=false`, marcar entities com `unavailable` via `_attr_available` |
| `log_when_unavailable` | S | `_LOGGER.warning` únic per caiguda i `_LOGGER.info` únic per recuperació (amb flag `_was_unavailable`) |
| `parallel_updates` | XS | `_PARALLEL_UPDATES = 1` com a constant — prevé condicions de carrera |
| `docs_configuration_parameters` | S | Llistar totes les opcions del config flow i options flow al README |
| `docs_installation_parameters` | XS | Referenciar els paràmetres del config flow |
| `test_coverage` | M | **Objectiu: 95% als mòduls core (`arcgis`, `coordinator`, `geo`, `models`)**. 70%+ als platforms (sensors/geo_location). No estressiar-se pel 95% global. |

### 🚫 Exempts

| Regla | Raó |
| --- | --- |
| `action_exceptions` | Sense service actions |
| `reauthentication_flow` | Font pública sense autenticació — no hi ha reauth possible |

---

## 🥇 Gold — UX excel·lent (no tot, però la part que importa)

### ✅ Ja complerts

| Regla | Com |
| --- | --- |
| `devices` | Una device "Incendis Catalunya" per config entry, amb `DeviceInfo` |
| `entity_translations` | `translation_key` + `translations/{ca,es,en}.json` |
| `reconfiguration_flow` | `async_step_reconfigure` al config flow (HA ≥ 2024.4) |

### ⏳ Pendents — val la pena fer-los

| Regla | Esforç | Què |
| --- | ---: | --- |
| `diagnostics` | S | `diagnostics.py`: redacta `zone.home` lat/lon (privadesa!), inclou l'última resposta del FeatureServer |
| `entity_category` | XS | `EntityCategory.DIAGNOSTIC` a `service_connected`/`last_update`/`last_update_status` |
| `entity_device_class` | XS | `SensorDeviceClass.DISTANCE` per `nearest_fire_distance`, `TIMESTAMP` per `last_update` |
| `entity_disabled_by_default` | S | `EntityCategory.DIAGNOSTIC` + `EntityRegistryDisabledHandler` per les entities més tècniques (`fires_per_tipus`, `last_update_status`) |
| `icon_translations` | S | `icons.json` amb `mdi:fire`/`mdi:fire-alert`/`mdi:fire-off`/`mdi:fire-extinguisher` per fase |
| `exception_translations` | S | `exceptions/` folder amb missatges traduïts (URL canviada, servei caigut) |
| `repair_issues` | M | `async_create_issue` quan FeatureServer torna 404 persistent (URL canviada) |
| `docs_data_update` | XS | Secció "Com s'actualitzen les dades" (polling Bombers 5 min, Pla Alfa 2×/dia) |
| `docs_examples` | S | Plantilles d'automació del §7 de feature-spec al README |
| `docs_known_limitations` | S | §8 de feature-spec al README |
| `docs_supported_functions` | M | Taula d'entities per platform |
| `docs_troubleshooting` | S | "Si no veus cap incendi…", "Si el mapa està buit…", "Com reportar un bug" |
| `docs_use_cases` | S | "Quan encenc aspersors", "Avisa'm al matí si risc alt", etc. |

### 🚫 Exempts

| Regla | Raó |
| --- | --- |
| `discovery` / `discovery_update_info` | Servei cloud, no hi ha dispositiu a la xarxa local per descobrir |
| `docs_supported_devices` | No exposem dispositius físics |
| `dynamic_devices` | Device únic estàtic per entry |
| `stale_devices` | Sense lifecycle de device |

---

## 🏆 Platinum — excel·lència tècnica

### ✅ Ja complerts

| Regla | Com |
| --- | --- |
| `async_dependency` | `aiohttp` és async nadiu; tot el nostre codi és `async def` |

### ⏳ Pendents

| Regla | Esforç | Què |
| --- | ---: | --- |
| `inject_websession` | XS | Passar `async_get_clientsession(hass)` al client ArcGIS en lloc de crear-ne un |
| `strict_typing` | M-L | Type hints a tots els mòduls. Objectiu: passar `mypy --strict` (o almenys `--disallow-untyped-defs`). No bloqueja v1. |

---

## Estat real (post-verificació 2026-07-02)

**Tot el pla d'implementació (`docs/05-implementation-plan.md` T1–T15) està complet** al repo. T16 (release v0.1.0) pendent de tag.

| Metric | Valor |
| --- | --- |
| Tests | 183 passing |
| Coverage global | 98% |
| Core modules (`arcgis`/`coordinator`/`geo`/`models`) | ≥92% |
| Platforms | ≥93% |

**9 todos reals** (els altres 17 es van completar sense tocar el YAML):

| Prioritat | Regles | Esforç |
| :--- | --- | ---: |
| **v0.1.0 (pre-release)** | `brands`, `test_before_configure`, `unique_config_entry`, `parallel_updates` | XS |
| **v0.2.0 (Gold UX)** | `entity_disabled_by_default`, `icon_translations`, `exception_translations`, `docs_known_limitations`, `docs_troubleshooting` | S-M |
| **v0.3.0 (Platinum)** | `strict_typing` | L (mypy CI) |

> La priorització original a sota s'han ajustat per reflectir el que *ja* està fet.

---

## Priorització per versió (KISS)

### v0.1.0 — tot Bronze + Silver crític
- **Obligatori**: `brands`, `test_before_configure`, `unique_config_entry`, `config_entry_unloading`, `entity_unavailable`, `log_when_unavailable`, `parallel_updates`, `docs_*` bàsics
- **Coverage**: 95% core (`arcgis`/`coordinator`/`geo`/`models`), 70% platforms
- Marquem Bronze + Silver com a `done`

### v0.2.0 — Gold d'UX
- `diagnostics`, `entity_category`, `entity_device_class`, `entity_disabled_by_default`, `icon_translations`, `exception_translations`
- `repair_issues` per a URL canviada
- Tots els `docs_*` restants

### v0.3.0 — Platinum tècnic
- `inject_websession`
- `strict_typing` (mypy net)
- Refactor finals

---

## Què **no** farem (sentit comú)

| Descart | Raó |
| --- | --- |
| Service actions (`incendiscat.refresh`, etc.) | El patró bus events + blueprint cobreix el 99% dels casos i és més idiomàtic |
| Local discovery | No tenim res a descobrir a la xarxa |
| 95% coverage global | Els platforms UI tenen poc valor de test; millor 95% al core i 70% als platforms |
| Platinum al 100% sense PR a core | Inversió de mypy--strict per tot té poc ROI si no es revisa un core maintainer |
| Brands elaborats (logo SVG professional) | Amb `icon.png` i `logo.png` simple amb el `mdi:fire` re-esticat n'hi ha prou per HACS |

---

## Referències

- [Integration Quality Scale (overview)](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
- [Rules index](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/) — cada regla té la seva pàgina amb exemples
- [Bronze checklist](https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist/) — per PR a core
- Exemple de `quality_scale.yaml`: qualsevol integració Gold/Platinum a `home-assistant/core` (p.ex. `homeassistant/components/shelly/quality_scale.yaml`)
