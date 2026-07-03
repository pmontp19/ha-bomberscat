# Feature spec — `ha-incendiscat`

Especificació funcional de la integració. Pren el **millor de `ha-wildfire-monitor` i `ha-pyrovigil`** i hi afegeix particularitats catalanes (fases `COM_FASE`, subtipus `VF/VA/VU`).

Domain: **`incendiscat`** (en minúscules, convenció HA).
Nom de marca a la UI: **"Incendis Catalunya"**.
Icona: foc (`mdi:fire`) o cascos de bombers (`mdi:fire-hydrant`).

---

## 1. Visió general

Una integració dona d'**un dispositiu** per ubicació monitorada, amb un conjunt d'entities agregades i **una `geo_location` entity per cada incindi** actiu dins del radi.

```
Dispositiu: Incendis Catalunya (casa)
├── sensor.incendiscat_active_fires          ← comptador total actius al radi
├── sensor.incendiscat_nearest_fire_distance ← km
├── sensor.incendiscat_nearest_fire_municipi ← nom municipi
├── sensor.incendiscat_fires_per_fase        ← atributs: actiu/estabilitzat/controlat/extingit
├── sensor.incendiscat_fires_per_tipus       ← atributs: VF/VA/VU
├── sensor.incendiscat_total_vehicles        ← recursos desplegats al radi
├── sensor.incendiscat_fire_risk             ← Pla Alfa (0-4) [si available]
├── binary_sensor.incendiscat_fire_nearby    ← hi ha foc dins radi d'alerta
├── binary_sensor.incendiscat_high_risk      ← perill d'incendi alt avui [si available]
├── geo_location.incendiscat_<act_num>       ← un per incendi
└── (events)
```

---

## 2. Config flow

### Setup inicial (UI)

Dues passes:

#### Passa 1 — Ubicació i àrea

Pre-omplert amb `zone.home` (lat/lon/radius). Mapa amb marcador arrossegable + selector de radi (amb slider visual de 5–200 km).

- **Àrea de seguiment** (radi gran) → quins incendis es trackegen (generen `geo_location` + apareixen al sensor comptador).
- **Radi d'alerta** (més petit) → què dispara `binary_sensor.incendiscat_fire_nearby` i l'event `fire_nearby`.

Distingir els dos radis és clau: pots voler veure tots els focs de Catalunya (seguiment) però només alertar dels que estan a <30 km (alerta). Aquest patró ve de wildfire-monitor.

#### Passa 2 — Filtres i freqüència

- **Subtipus a incloure**: checkboxes `[✓] Forestal (VF) [ ] Agrícola (VA) [ ] Urbana (VU)` — per defecte només VF.
- **Polling interval** (minuts): default 5, rang 1–60.
- **Fases a considerar "actives"**: `[✓] Actiu [✓] Estabilitzat [ ] Controlat [ ] Extingit` — per defecte les dues primeres.
- **Mida mínima (vehicles)**: ignorar serveis amb menys de N vehicles assignats. Default 0 (tots).

### Opcions reconfigurables (Configure post-setup)

Tots els anteriors. A més:

- **Llindar de risc alt** (0–4) pel Pla Alfa — default 2 (Alt).
- **Notificació crítica** (by-passa DND) — checkbox.

> **Idioma**: no hi ha opció manual de llengua. Els noms d'entities es tradueixen amb el mecanisme natiu de HA (`has_entity_name` + `translation_key` + `translations/{ca,es,en}.json`), segons l'idioma del sistema. Una opció pròpia seria un anti-patró ([entity-translations](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/entity-translations/)).

### Reconfiguració

La ubicació i els radis (dades de setup) es canvien via `async_step_reconfigure` (HA ≥ 2024.4), no via options flow. Les opcions (filtres, polling, llindars) van a l'options flow.

> **Sense YAML**: la integració és config-flow-only. [ADR-0010](https://github.com/home-assistant/architecture/blob/master/adr/0010-integration-configuration.md) prohibeix la configuració YAML per a integracions noves.

---

## 3. Entities — especificació completa

### 3.1 `geo_location.incendiscat_<act_num>` (per incendi)

Una entity per cada incendi que compleix els filtres dins del radi de **seguiment**. L'estat és la distància a `zone.home` en km (convenció HA per `geo_location`).

**Attributes:**

| Attribute | Tipus | Descripció |
| --- | --- | --- |
| `source` | string | `"incendiscat"` (per filtrar a la Map card / templates) |
| `latitude`, `longitude` | float | Coordenades WGS84 |
| `act_num` | string | `ACT_NUM_ACTUACIO` |
| `fase` | string | `Actiu`/`Estabilitzat`/`Controlat`/`Extingit` (`COM_FASE` null → `Actiu`, com el visor oficial) |
| `tipus` | string | `VF`/`VA`/`VU` |
| `tipus_desc` | string | Descripció llegible (`Incendi vegetació forestal`) |
| `municipi` | string | `MUNICIPI_SIG` |
| `data_inici` | datetime | `ACT_DAT_INICI` localitzat |
| `data_fi` | datetime\|null | `ACT_DAT_FI` |
| `vehicles` | int | `ACT_NUM_VEH` |
| `situacio` | string | `ACT_SITUACIO` codi cru (`A`/`I`/`N`/`P`, sense domini oficial — no es tradueix) |
| `updated_at` | datetime | `EditDate` |
| `url` | string | Enllaç al detall al visor dels Bombers |

**Cicle de vida**:
- Es crea quan apareix un incendi nou que compleix filtres + radi.
- S'elimina quan l'incendi té `COM_FASE = Extingit` **i** han passat N minuts (configurable, default 60) — per donar temps a veure'l resolt.

### 3.2 `sensor.incendiscat_active_fires`

Comptador d'incendis actius (definits per `active_phases`) dins del radi de seguiment i que compleixen el filtre de subtipus.

State: enter ≥ 0.
Attributes: `last_updated`, `total_in_track_radius`, `total_in_alert_radius`.

### 3.3 `sensor.incendiscat_nearest_fire_distance`

State: km (float, 1 decimal) a l'incendi actiu més proper dins del radi de seguiment. `-1` si no n'hi ha cap.

### 3.4 `sensor.incendiscat_nearest_fire_municipi`

State: nom del municipi del foc més proper. `"—"` si no n'hi ha cap. Útil per a notificacions parlades ("Hi ha foc a Sant Quirze Safaja").

### 3.5 `sensor.incendiscat_fires_per_fase` ⭐ (diferencial)

Comptador per fase. State: enter total. Attributes:

```json
{
  "actiu": 2,
  "estabilitzat": 3,
  "controlat": 1,
  "extingit": 0,
  "unit_of_measurement": "incendis"
}
```

Ideal per a gràfics de barres i templates.

### 3.6 `sensor.incendiscat_fires_per_tipus`

State: enter total. Attributes `vf`, `va`, `vu` amb els comptadors per subtipus.

### 3.7 `sensor.incendiscat_total_vehicles` ⭐ (de pyrovigil)

Sumatori de `ACT_NUM_VEH` dels incendis en seguiment. State: enter ≥ 0. Attributes: per fase si volem detall.

### 3.8 `sensor.incendiscat_fire_risk` ✅ (font confirmada: Pla Alfa)

State: nivell de perill Pla Alfa del municipi de `zone.home`, escala **0–4** (extreta de `PERIL_M` del FeatureServer `Pla_Alfa_Municipal_Avui_FL_2_view`).

Attributes:
- `nivell_text`: "Baix" / "Moderat" / "Alt" / "Molt alt" / "Extrem" (text oficial del panell "Nivells del Pla Alfa" d'Interior — no hi ha nivell "sense risc")
- `comarca`, `municipi`
- `data_vigencia`, `hora_vigencia` (de `Pla_Alfa_Comarcal_Avui_FL_VW`)
- `perill_demà` (del servei `_Dema_` equivalent)

Classificació: `0`=blanc/baix, `1`=groc/moderat, `2`=taronja/alt, `3`=vermell/molt alt, `4`=vermell fosc/extrem.

Polling: 2 cops al dia (00:30 i 09:45h, just després de les actualitzacions oficials) — no cal freqüent.

### 3.9 `binary_sensor.incendiscat_fire_nearby`

`on` si hi ha cap incendi que compleix filtres dins del radi **d'alerta**.
Attributes (quan `on`): `nearest_act_num`, `nearest_distance_km`, `nearest_municipi`, `nearest_fase`.

### 3.10 `binary_sensor.incendiscat_high_risk` ✅

`on` si `sensor.incendiscat_fire_risk` ≥ llindar configurat (default 2 = Alt, equivalent a Pla Alfa taronja). Dispara automacions matinals ("avui risc alt, no encenguis foc").

### 3.11 Diagnosi

| Entity | Descripció |
| --- | --- |
| `binary_sensor.incendiscat_service_connected` | `on` si l'última query al FeatureServer ha anat bé |
| `sensor.incendiscat_last_update` | timestamp de l'última sincronització correcta |
| `sensor.incendiscat_last_update_status` | `success` / `error_<codi>` |

Aquests són necessaris perquè la font no és oficialment suportada i pot caure.

---

## 4. Events per a automacions

### 4.1 `incendiscat_fire_detected`

Quan un incendi nou compleix filtres i entra al radi de seguiment (no existia al cicle anterior).

```json
{
  "act_num": "262311630",
  "distance_km": 12.4,
  "municipi": "Sant Quirze Safaja",
  "fase": "Actiu",
  "tipus": "VF",
  "tipus_desc": "Incendi vegetació forestal",
  "vehicles": 4,
  "in_alert_radius": true,
  "latitude": 41.72388,
  "longitude": 2.16657,
  "url": "..."
}
```

### 4.2 `incendiscat_fire_resolved`

Quan un incendi previament tracked passa a `Extingit` o surt del radi.

```json
{
  "act_num": "262311630",
  "municipi": "Sant Quirze Safaja",
  "duration_min": 187,
  "final_fase": "Extingit"
}
```

### 4.3 `incendiscat_phase_change` ⭐ (diferencial)

Quan un incendi canvia de fase (p.ex. `Actiu → Estabilitzat`).

```json
{
  "act_num": "262311630",
  "municipi": "Sant Quirze Safaja",
  "old_fase": "Actiu",
  "new_fase": "Estabilitzat",
  "distance_km": 12.4
}
```

### 4.4 `incendiscat_fire_nearby` (potser redundant amb el binary sensor)

Es dispara quan `binary_sensor.incendiscat_fire_nearby` passa a `on`. Payload amb el detall del foc que l'ha disparat.

---

## 5. Blueprint inclòs

`blueprints/automation/incendiscat_fire_notification.yaml` amb:

| Camp | Tipus | Descripció |
| --- | --- | --- |
| `notification_service` | selector | `notify.notify` per defecte |
| `minimum_fase` | selector | Nivell mínim de fase per notificar (Actiu/Estabilitzat/Controlat/Extingit). Default Actiu. |
| `minimum_vehicles` | int | Filtre de magnitud. Default 0. |
| `maximum_distance` | int | Km màxims (0 = usa alert_radius). |
| `critical_alert` | bool | By-passa DND. Default false. |
| `include_resolved` | bool | Notifica també quan es resolguen. Default false. |
| `include_phase_changes` | bool | Notifica canvis de fase. Default false. |
| `open_map_url` | select | `incendiscat` (visor oficial) / `google_maps` / `osm`. |

La notificació inclou: emoji segons fase 🔥/🟡/🟢, municipi, distància, fase, recursos, **botó "Obrir mapa"** amb l'URL triada.

Exemple de títol cos del missatge:

> 🔥 Foc a **Sant Quirze Safaja** (12 km)
> Fase: **Actiu** · 4 vehicles · Forestal
> Obre el mapa: https://experience.arcgis.com/...

---

## 6. Dashboard d'exemple (al README)

Plantilla YAML completa per replicar el patró de wildfire-monitor però adaptada:

```yaml
type: vertical-stack
cards:
  - type: map
    auto_fit: true
    entities:
      - zone.home
    geo_location_sources:
      - incendiscat
  - type: glance
    entities:
      - sensor.incendiscat_active_fires
      - sensor.incendiscat_nearest_fire_distance
      - sensor.incendiscat_nearest_fire_municipi
      - sensor.incendiscat_total_vehicles
      - binary_sensor.incendiscat_fire_nearby
  - type: markdown
    title: Incendis actius
    content: |
      {% set fires = states.geo_location | selectattr('attributes.source','eq','incendiscat') | list %}
      {% if fires | count == 0 %}_No hi ha incendis actius a la zona._{% else %}
      | # | Municipi | Fase | km | Veh | Tipus |
      |--:|:--|:--|--:|--:|:--|
      {% for f in fires | sort(attribute='state') %}
      | {{ loop.index }} | {{ f.attributes.municipi }} | {{ f.attributes.fase }} | {{ f.state | round(1) }} | {{ f.attributes.vehicles }} | {{ f.attributes.tipus }} |
      {% endfor %}
      {% endif %}
```

---

## 7. Patrons d'automació que suportem

1. **Notificació push a mogudes de nou foc dins 30 km** (blueprint).
2. **Encendre aspersors / tancar persianes** quan `fire_nearby` → `on`.
3. **Avisa al matí si avui hi ha risc alt** (high_risk → on).
4. **Història**: quan `phase_change` → log a notificació silenciosa per a postmortem.
5. **Tancament de finestres** automàtic si `fire_nearby` i qualitat de l'aire dolenta (combinat amb sensor PM2.5).

---

## 8. Escenaris fora d'abast (no farem)

- **Perímetre cremat (polígons)**: la font només publica punts. Caldria NIFC WFIGS-style, no disponible per a Catalunya.
- **Predicció de risc a 7 dies**: el Departament d'Agricultura publica informes setmanals en PDF, no estructurats.
- **Dades històriques anteriors a la data d'alta disponibilitat del FeatureServer**: limitat al que hi hagi actualment al servei.
- **Suport multi-zona**: una integració = una ubicació (com wildfire-monitor). Per diverses cases, installa-la diverses vegades.

---

## 9. Criteris d'acceptació (per tancar v1)

- [ ] Config flow amb dues passes (mapa + filtres).
- [ ] Una `geo_location` entity per incendi actiu amb tots els attributes del §3.1.
- [ ] Els 7 sensors agregats del §3.2–3.7.
- [ ] Els 2 binary sensors del §3.9 + condicionalment 3.10.
- [ ] Els 3 events del §4.1–4.3.
- [ ] 3 entities de diagnose (§3.11).
- [ ] Blueprint funcional (§5).
- [ ] Dashboard d'exemple al README (§6).
- [ ] Polling tolerant: retry amb backoff, no esborra cache si falla.
- [ ] Tests amb `pytest` (mínim: parsing del FeatureServer, càlcul de distàncies, lifecycle d'events).
- [ ] `ruff` net.
- [ ] CI: hassfest + HACS validation + pytest.
- [ ] README en català amb badges HACS.
