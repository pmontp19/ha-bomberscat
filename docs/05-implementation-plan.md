# Pla d'implementació — `ha-incendiscat`

Descomposició en tasques implementables derivada de [`03-feature-spec.md`](03-feature-spec.md) i [`04-architecture.md`](04-architecture.md). Cada tasca és S/M (1–5 fitxers), deixa el sistema en estat verd i té criteris d'acceptació verificables.

## Graf de dependències

```
T1 Scaffold + CI
    ├── T2 Models + geo (pur Python)
    │       ├── T3 Client ArcGIS Bombers
    │       │       ├── T5 Coordinator
    │       │       │       ├── T6 Sensors bàsics
    │       │       │       ├── T7 geo_location
    │       │       │       ├── T8 binary_sensor fire_nearby
    │       │       │       ├── T9 Events
    │       │       │       └── T12 Sensors agregats
    │       │       └── T13 Diagnosi + resiliència
    │       └── T10 Client Pla Alfa + sensors de risc
    └── T4 Config flow (pas 1)
            └── T11 Config flow pas 2 + options flow
T14 Traduccions ← (T4–T12)
T15 Blueprint ← (T8, T9)
T16 README + release HACS ← (tot)
```

**Ordre de risc**: T3 i T10 (les úniques dependències externes, FeatureServers no oficials) van al principi — si l'esquema real no quadra amb la doc, ho sabem abans d'escriure entities.

---

## Fase 1 — Fonament (sense HA runtime)

### Task 1: Scaffold del repositori i CI

**Descripció:** Crear l'esquelet complet del §1 de `04-architecture.md`: `custom_components/incendiscat/` amb `manifest.json` (§2), `const.py` (domain, defaults, URLs dels endpoints del §9 de `01-data-sources.md`), `hacs.json`, `pyproject.toml` (ruff + pytest config), LICENSE MIT, i els dos workflows (`ci.yml`: ruff+pytest; `validate.yml`: hassfest + HACS validation).

**Criteris d'acceptació:**
- [ ] `manifest.json` vàlid amb `domain: incendiscat`, `iot_class: cloud_polling`, `requirements: []`, `config_flow: true`
- [ ] Tots els endpoints (Bombers + 6 Pla Alfa + 3 Socrata) com a constants a `const.py`
- [ ] CI executa en push i falla si ruff/hassfest fallen

**Verificació:** `ruff check .` net; workflow `validate.yml` verd (hassfest accepta el manifest).
**Dependències:** Cap. **Mida:** M (5 fitxers de config)

### Task 2: Models i geometria

**Descripció:** `models.py` (dataclasses `Incident`, enums `Fase` amb `severity`, `Tipus`, `from_feature()` tolerant a nulls — §4 architecture) i `geo.py` (haversine pur, filtre per radi). Cap import de HA: testable aïlladament.

**Criteris d'acceptació:**
- [ ] `Incident.from_feature()` parseja el GeoJSON d'exemple del §2 de `01-data-sources.md` (timestamps ms UTC, `COM_FASE` null → `ACTIU` com el visor oficial, `TAL_COD_ALARMA2` null → `VF`)
- [ ] Haversine correcte (±0.1 km vs valors coneguts); casos límit: mateix punt, antípodes

**Verificació:** `pytest tests/test_models.py tests/test_geo.py` verd.
**Dependències:** T1. **Mida:** S (2 fitxers + 2 tests)

### Task 3: Client ArcGIS dels Bombers ⚠️ risc alt

**Descripció:** `arcgis.py` amb `fetch_incidents()` (§3 architecture): paginació amb `resultOffset` fins `exceededTransferLimit == false`, sync incremental per `DATA_ACT`, retries amb backoff (1s/2s/4s) per timeout/5xx, sense retry per 4xx. Capturar fixtures reals del FeatureServer (`featureserver_sample.json`, `_empty.json`, `_paginated.json`).

**Criteris d'acceptació:**
- [ ] Query real contra el FeatureServer retorna `list[Incident]` amb coordenades WGS84 (`outSR=4326`)
- [ ] **Dedup per `ACT_NUM_ACTUACIO`**: la vista és un log d'snapshots — amb una fixture de 2 files de la mateixa actuació, guanya la de `DATA_ACT` màxim
- [ ] Paginació verificada amb mock de 2+ pàgines; sync incremental genera el `where=DATA_ACT > ...` correcte
- [ ] Errors 5xx → 3 retries; 4xx → excepció immediata

**Verificació:** `pytest tests/test_arcgis.py` verd; script manual d'una query live documentat al PR.
**Dependències:** T2. **Mida:** M (1 fitxer + fixtures + test)

### Checkpoint 1 — Fonament
- [ ] `pytest` + `ruff` + hassfest verds
- [ ] Query live al FeatureServer funciona i el schema real coincideix amb `01-data-sources.md` (si no: aturar i revisar la doc)

---

## Fase 2 — Slice vertical principal (incendis en temps real)

### Task 4: Config flow mínim (pas 1: ubicació i radis)

**Descripció:** `config_flow.py` amb el pas d'ubicació (§2 feature-spec): `LocationSelector(radius=True)` pre-omplert amb `zone.home`, `track_radius` (5–200 km) i `alert_radius`. Radi com a `vol.Optional` amb default + validació defensiva (el frontend pot ometre'l — core#108960). `strings.json` en anglès (base). Crea la config entry; encara sense coordinator.

**Criteris d'acceptació:**
- [ ] La integració apareix a "Afegeix integració" i crea una entry amb lat/lon/radis
- [ ] Validació: `alert_radius ≤ track_radius`; radi absent → default aplicat

**Verificació:** `pytest tests/test_config_flow.py`; setup manual en una instància HA dev.
**Dependències:** T1. **Mida:** S

### Task 5: Coordinator

**Descripció:** `coordinator.py` (`IncendiscatDataUpdateCoordinator`, §5 architecture): estat `IncendiscatState`, altes/baixes/modificacions per sync incremental, `_passa_filts()` (subtipus/fases/min_vehicles), filtre per radi, cache de distàncies, conservació d'estat si el fetch falla, `_cleanup_resolved()` amb grace period. `__init__.py` amb `async_setup_entry` que arrenca el coordinator, el guarda a `entry.runtime_data` (alias tipat, no `hass.data`) i fa `async_config_entry_first_refresh()`.

**Criteris d'acceptació:**
- [ ] Cicle complet: alta nova, modificació, baixa per fase/radi — verificat amb fixtures
- [ ] Fetch fallit conserva `incidents` anteriors i registra `last_error`
- [ ] Extingits eliminats només després del grace period (default 60 min)

**Verificació:** `pytest tests/test_coordinator.py tests/test_lifecycle.py` verd.
**Dependències:** T3, T4. **Mida:** M

### Task 6: Sensors bàsics

**Descripció:** `sensor.py` amb la classe base `IncendiscatEntity` (§7 architecture, `DeviceInfo` SERVICE) i 3 sensors: `active_fires`, `nearest_fire_distance`, `nearest_fire_municipi` (§3.2–3.4 feature-spec).

**Criteris d'acceptació:**
- [ ] Els 3 sensors apareixen sota el dispositiu "Incendis Catalunya" amb estats correctes segons fixtures
- [ ] Sense incendis: `active_fires=0`, `distance=-1`, `municipi="—"`

**Verificació:** `pytest tests/test_sensor.py`; comprovació manual a HA dev.
**Dependències:** T5. **Mida:** S

### Task 7: Entities `geo_location`

**Descripció:** `geo_location.py` (§3.1 feature-spec, §7 architecture): una entity per incendi tracked, estat = distància km, tots els attributes especificats (inclòs `source: incendiscat` i `url` al visor), alta/baixa dinàmica seguint el lifecycle del coordinator.

**Criteris d'acceptació:**
- [ ] Cada incendi de la fixture genera una entity amb tots els attributes del §3.1
- [ ] La Map card amb `geo_location_sources: [incendiscat]` mostra els markers
- [ ] Entity eliminada quan el coordinator descarta l'incendi (sense entitats òrfenes al registre)

**Verificació:** `pytest tests/test_geo_location.py`; Map card manual a HA dev.
**Dependències:** T5. **Mida:** M

### Task 8: `binary_sensor.fire_nearby`

**Descripció:** `binary_sensor.py` amb `fire_nearby` (§3.9): `on` si cap incendi filtrat dins del radi d'**alerta**; attributes del foc més proper quan `on`.

**Criteris d'acceptació:**
- [ ] `on`/`off` correcte segons `alert_radius` (no `track_radius`)
- [ ] Attributes `nearest_*` presents quan `on`

**Verificació:** `pytest tests/test_binary_sensor.py`.
**Dependències:** T5. **Mida:** S

### Task 9: Events al bus

**Descripció:** `_emit_events()` al coordinator (§6 architecture): `incendiscat_fire_detected`, `incendiscat_fire_resolved`, `incendiscat_phase_change` amb els payloads exactes del §4 de feature-spec. Snapshots `prev_*` per detectar transicions.

**Criteris d'acceptació:**
- [ ] Foc nou dins radi → `fire_detected` (amb `in_alert_radius` correcte); cap event en cicles següents sense canvis
- [ ] `Actiu → Estabilitzat` → `phase_change` amb `old_fase`/`new_fase`
- [ ] Pas a Extingit o sortida de radi → `fire_resolved` amb `duration_min`

**Verificació:** `pytest tests/test_events.py` (captura del bus amb `async_capture_events`).
**Dependències:** T5. **Mida:** M

### Checkpoint 2 — Slice principal end-to-end
- [ ] En una instància HA dev: setup per UI → markers al mapa + sensors + binary sensor + events visibles a Developer Tools
- [ ] Polling continuat ≥24 h sense throttling observat (valida el "pròxim pas" 1 del README)
- [ ] Revisió humana abans de la fase 3

---

## Fase 3 — Risc (Pla Alfa) i configuració completa

### Task 10: Client Pla Alfa + sensors de risc

**Descripció:** Ampliar `arcgis.py` amb query point-in-polygon a `Pla_Alfa_Municipal_Avui` (lookup del municipi de `zone.home`, §3 de `01-data-sources.md`), consulta a `_Dema_` i a la capa comarcal per `DATA`/`HORA` de vigència. Afegir `sensor.fire_risk` (§3.8) i `binary_sensor.high_risk` (§3.10). Polling propi 2×/dia (00:30, 09:45), independent del polling d'incendis.

**Criteris d'acceptació:**
- [ ] `fire_risk` = `PERIL_M` (0–4) del municipi real de l'usuari, amb attributes `nivell_text`, `comarca`, `data_vigencia`, `perill_demà`
- [ ] `high_risk` = `on` quan `fire_risk ≥ llindar`
- [ ] Pla Alfa caigut no afecta el polling d'incendis (fonts independents)

**Verificació:** `pytest tests/test_pla_alfa.py` amb fixtures; query live manual amb el municipi de casa.
**Dependències:** T2 (client base), T6 (patró sensor). **Mida:** M

### Task 11: Config flow pas 2 + options flow

**Descripció:** Pas 2 del config flow (§2 feature-spec): subtipus (default `[VF]`), fases actives (default `[Actiu, Estabilitzat]`), polling interval (1–60 min, default 5), min_vehicles. Options flow per a les opcions (patró nou: sense assignar `self.config_entry`, sense `OptionsFlowWithConfigEntry`) + `async_step_reconfigure` al ConfigFlow per canviar ubicació/radis.

**Criteris d'acceptació:**
- [ ] Setup en 2 passes amb els defaults de la spec
- [ ] Canvi d'opcions recarrega el coordinator sense reiniciar HA
- [ ] "Reconfigura" permet moure la ubicació sense esborrar la integració

**Verificació:** `pytest tests/test_config_flow.py` (flux complet + options).
**Dependències:** T4, T5. **Mida:** M

### Task 12: Sensors agregats restants

**Descripció:** `fires_per_fase` (§3.5), `fires_per_tipus` (§3.6), `total_vehicles` (§3.7). Els dos primers amb attributes de comptadors. `icons.py` amb el mapping mdi per fase.

**Criteris d'acceptació:**
- [ ] Attributes `actiu/estabilitzat/controlat/extingit` i `vf/va/vu` quadren amb les fixtures
- [ ] `total_vehicles` = Σ `ACT_NUM_VEH` dels tracked

**Verificació:** `pytest tests/test_sensor.py` ampliat.
**Dependències:** T6. **Mida:** S

### Task 13: Diagnosi i resiliència completa

**Descripció:** Les 3 entities de diagnosi (§3.11): `service_connected`, `last_update`, `last_update_status`. Implementar la taula de fallades del §9 d'architecture (inclòs event `incendiscat_service_degraded` per 404 persistent) i `diagnostics.py` per al download de diagnòstics de HA.

**Criteris d'acceptació:**
- [ ] FeatureServer caigut (mock) → `service_connected=off`, estat anterior conservat, recuperació neta quan torna
- [ ] 404 persistent → event `service_degraded` + repair issue visible a l'usuari

**Verificació:** `pytest tests/test_resilience.py`.
**Dependències:** T5. **Mida:** M

### Checkpoint 3 — Funcionalitat completa
- [ ] Tots els criteris del §9 de feature-spec coberts excepte blueprint/README
- [ ] Coverage dels mòduls core (arcgis, coordinator, geo, models) > 90%

---

## Fase 4 — Poliment i release

### Task 14: Traduccions

**Descripció:** `translations/ca.json`, `es.json`, `en.json` amb `_attr_translation_key` a totes les entities i strings del config flow. Català com a llengua de referència de la UI.

**Criteris d'acceptació:**
- [ ] Config flow i noms d'entities traduïts als 3 idiomes; cap clau òrfena (hassfest ho valida)

**Verificació:** hassfest verd; canvi d'idioma manual a HA dev.
**Dependències:** T4–T12. **Mida:** S

### Task 15: Blueprint de notificacions

**Descripció:** `blueprints/automation/incendiscat_fire_notification.yaml` amb tots els inputs del §5 de feature-spec (fase mínima, vehicles, distància, critical alert, resolved/phase_changes, tria de mapa) i el format de missatge amb emoji per fase.

**Criteris d'acceptació:**
- [ ] Blueprint importable i funcional a HA dev; notificació amb botó "Obrir mapa" que obre l'URL triada
- [ ] `critical_alert` genera notificació crítica a iOS/Android

**Verificació:** Test manual amb events simulats (`hass.bus.fire` des de Developer Tools).
**Dependències:** T8, T9. **Mida:** S

### Task 16: README, dashboard i release v0.1.0

**Descripció:** README d'usuari en català (instal·lació HACS, badges, captura del mapa, dashboard YAML del §6 de feature-spec, patrons d'automació del §7, descàrrec). Tag `v0.1.0` + release amb `release-please`. Alta com a HACS custom repository.

**Criteris d'acceptació:**
- [ ] Instal·lació des de zero via HACS custom repo funciona seguint només el README
- [ ] Dashboard d'exemple funciona copiant-lo tal qual

**Verificació:** Instal·lació neta en una instància HA fresca.
**Dependències:** Totes. **Mida:** S

### Checkpoint final — v1
- [ ] Tots els criteris d'acceptació del §9 de `03-feature-spec.md` marcats
- [ ] CI complet verd (ruff, pytest+coverage, hassfest, HACS)

---

## Paral·lelització

- **Seqüencial obligatori:** T1 → T2 → T3 → T5 (columna vertebral).
- **Paral·lelitzables després de T5:** T6, T7, T8, T9 (platforms independents sobre el mateix coordinator).
- **Paral·lelitzables en qualsevol moment post-T2:** T10 (Pla Alfa, font independent) i T4 (config flow, només depèn de T1).
- **T14–T16** només al final.

## Riscos i mitigacions

| Risc | Impacte | Mitigació |
| --- | --- | --- |
| FeatureServer no oficial canvia schema/URL | Alt | T3 primer de tot; tolerància `.get()`; diagnosi T13 amb `service_degraded` |
| Throttling desconegut del FeatureServer | Mitjà | Checkpoint 2 inclou soak test 24 h; polling mínim 1 min hard-coded |
| Fora de campanya forestal hi ha pocs incendis per provar | Mitjà | Fixtures capturades ara (juliol = campanya activa); tests no depenen del live |
| `location_selector` amb radi varia entre versions HA | Baix | Fixar `homeassistant` mínim al manifest; fallback a camps numèrics |
| Registre d'entitats `geo_location` amb flickering | Mitjà | Grace period (ja al disseny); test de lifecycle T7 |

## Qüestions obertes (decidir abans de la tasca afectada)

1. ~~**Llindar `high_risk` per defecte**~~ ✅ Resolt: unificat a **3 (Alt)**, escala 0–4, a tot `03-feature-spec.md`.
2. ~~**Opció "Llenguatge" al config flow**~~ ✅ Resolt: **eliminada**. És un anti-patró — HA tradueix nativament via `has_entity_name` + `translation_key` + `translations/*.json` ([entity-translations](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-translations/)). Caveat conegut: els noms es resolen amb l'idioma del *sistema* en el moment de crear l'entity, no per usuari.
3. ~~**Glossari `ACT_SITUACIO`**~~ ✅ Investigat (glossari inferit a `01-data-sources.md` §2): **cap domini oficial existeix** — `A`=Activa (alta confiança, vehicles ≥ 1), `N`=Nova, `P`=Pendent de tancament, `I`=probablement Inactiva (la hipòtesi `I`=inici queda descartada). Decisió: exposar-lo com a **codi cru sense traduir**; l'estat de cara a l'usuari és `COM_FASE` (null → Actiu) i "s'hi treballa ara" = `ACT_NUM_VEH > 0`. Troballa col·lateral crítica: la vista és un **log d'snapshots** → dedup obligatori (incorporat a T3).
4. **Sensor històric Socrata** (§8 de `01-data-sources.md`): fora de v1 → roadmap post-v1, no és a cap tasca.
5. ~~**YAML legacy**~~ ✅ Resolt: **descartat**. [ADR-0010](https://github.com/home-assistant/architecture/blob/master/adr/0010-integration-configuration.md) i la doc de YAML configuration prohibeixen la configuració YAML per a integracions noves — config-flow-only. Secció eliminada de `03-feature-spec.md`.
