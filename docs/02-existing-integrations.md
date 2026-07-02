# Integracions de referència

Anàlisi de les dues integracions HACS existents per a seguiment d'incendis. En treurem el millor cadascuna per al disseny de `ha-incendiscat`.

**Recerca prèvia**: no existeix cap integració al nucli de Home Assistant per a incendis forestals. L'única integració "fire" al core és `intellifire`, que és per a **xemeneies connectades**, no boscos.

---

## 1. `johnbr/ha-wildfire-monitor` — Califòrnia

- Repo: <https://github.com/johnbr/ha-wildfire-monitor>
- Llengua: Python
- Llicència: MIT
- Tòpics: `california`, `homeassistant`, `hacs`, `wildfire`, `calfire`
- Estat: 0 ⭐, actualitzat fa 1 dia (molt actiu)
- Commits: 5 (acabat de crear)

### Font de dades

- **CAL FIRE public incident feed**: <https://www.fire.ca.gov/incidents/>
- **API REST sense clau** (keyless).
- Només Califòrnia.
- Retorna **punts** (no polígons de perímetre cremat — veuen al roadmap integrar NIFC WFIGS).

### Entities que crea

| Entity | Descripció |
| --- | --- |
| `geo_location.*` | Un marker per incendi actiu dins l'àrea monitorada. State = distància a casa (km). Attributes: `acres`, `containment`, `county`, `location`, `started`, `updated`, `url`. |
| `binary_sensor.wildfire_alert` | `on` quan hi ha foc dins el llindar d'alerta. Attributes descriuen el foc més proper que hi és a dintre. |
| `sensor.*_wildfires_in_range` | Comptador d'incendis en seguiment. |
| `sensor.*_nearest_wildfire_distance` | Distància (km) al més proper. |
| `sensor.*_largest_wildfire_size` | Hectàrees del més gran. |

### Config flow

- **Setup amb mapa**: arrossegues marcador i radi per definir l'àrea a monitorar. Pre-centrat a `zone.home`.
- Distàncies i alerta es mesuren sempre des de `zone.home` (llegit en calent, no guardat).
- Opcions post-setup (via Configure):
  - `Alert distance (km)` — default 30
  - `Minimum fire size (acres)` — default 0
  - `Update interval (minutes)` — default 10

### Event per a automacions

Dispara `wildfire_monitor_alert` amb `name`, `distance_km`, `acres`, `containment`, `url` perquè l'usuari hi enllaci notificacions.

### Dashboard (ve com a exemple al README)

- `type: map` amb `geo_location_sources: [wildfire_monitor]` → marca tots els focs a la Map card nativa sense components extra.
- Markdown card amb taula ordenada per distància (template Jinja).

### Punts forts (per copiar)

1. **Setup basat en mapa** — molt intuïtiu, pre-centrat a casa.
2. **`zone.home` llegit en calent** — tolerant a canvis de casa sense reconfig.
3. **3 sensors agregats** (comptador, proper, més gran) — els tres númeris que tothom vol veure.
4. **Ús de `geo_location` entities** — s'integren amb la Map card nativa sense dependències frontend.
5. **Event + binary sensor dobles** — cobreix els dos patrons d'automació habituals.
6. **Plantilla de dashboard inclosa al README** — adopció zero-friction.
7. **Plantilla d'automació de notificació** — exemple concret.
8. **`auto_fit: true`** a la Map card — s'auto-encuadra a tots els focs.

### Punts febs (a millorar)

1. **Sense tracking de recursos** — no sap quants bombers/vehicles/aeronaus hi treballen (CAL FIRE tampoc ho publica per punt).
2. **Sense risc previst** — no hi ha capa de "risc d'incendi avui".
3. **Sense filtre per tipus** — tot és "wildfire" genèric.
4. **Sense tracking d'evolució/fase** — només `containment %`, sense història de canvis de fase.
5. **Distàncies només en milles als exemples** (cosmètic).
6. **Sense blueprint**: l'usuari ha de copiar YAML.

---

## 2. `Duarte-Mercedes-Santos/ha-pyrovigil` — Portugal ⭐

- Repo: <https://github.com/Duarte-Mercedes-Santos/ha-pyrovigil>
- Llengua: Python
- Llicència: MIT
- Tòpics: `portugal`, `home-assistant`, `homeassistant`, `hacs`, `wildfire`, `ipma`, `fire-monitoring`, `anepc`
- Estat: 0 ⭐, 2 releases (v0.1.2 fa una setmana), 13 commits
- CI: GitHub Actions

### Fonts de dades (les 3 són públiques i sense clau)

| Font | Dades | Cadència |
| --- | --- | --- |
| [ANEPC ArcGIS](https://services-eu1.arcgis.com/VlrHb7fn5ewYhX6y/arcgis/rest/services/OcorrenciasSite/FeatureServer) | Incidents, recursos desplegats | Configurable (def. 5 min) |
| [IPMA RCM](https://api.ipma.pt/open-data/forecast/meteorology/rcm/) | Risc d'incendi diari (escala 1-5) | Horària |
| [IPMA Warnings](https://api.ipma.pt/open-data/forecast/warnings/) | Avisos meteorològics | 30 min |

> **Observació crítica**: ANEPC és l'equivalent portuguès dels Bombers+Protecció Civil catalans i **fa servir el mateix patró ArcGIS FeatureServer**. Aquesta és la base arquitectural que reutilitzarem directament.

### Config flow

- UI recomanat: pre-omple coordenades des de `zone.home`.
- Inputs: coordenades, **radi d'alerta (km)**, **polling interval (min)**.
- YAML alternatiu:

  ```yaml
  pyrovigil:
    latitude: 38.7223
    longitude: -9.1393
    radius: 25          # km (default: 25)
    scan_interval: 5    # minuts (default: 5)
  ```

- Options (post-setup):
  - **Alert radius** 5–100 km
  - **Polling interval** 1–30 min
  - **High risk threshold** RCM 2–5 (default 4)

### Entities que crea

#### Sensors

| Entity | Descripció | Default |
| --- | --- | --- |
| `sensor.pyrovigil_active_fires` | Nre. focs actius dins radi | ✅ |
| `sensor.pyrovigil_nearest_fire` | Distància (km) al més proper | ✅ |
| `sensor.pyrovigil_fire_risk` | Risc avui (1-5) | ✅ |
| `sensor.pyrovigil_total_personnel` | Bombers desplegats a la vora | ⚠️ disabled |
| `sensor.pyrovigil_total_ground_vehicles` | Vehicles desplegats | ⚠️ disabled |
| `sensor.pyrovigil_total_aircraft` | Aeronaus desplegades | ⚠️ disabled |
| `sensor.pyrovigil_fire_risk_tomorrow` | Risc demà | ⚠️ disabled |
| `sensor.pyrovigil_weather_warnings` | Avisos meteorològics actius | ⚠️ disabled |

#### Binary sensors

| Entity | ON quan | Default |
| --- | --- | --- |
| `binary_sensor.pyrovigil_fire_nearby` | Hi ha foc dins radi | ✅ |
| `binary_sensor.pyrovigil_high_fire_risk` | Risc ≥ llindar | ✅ |

#### Events

| Event | Quan | Data |
| --- | --- | --- |
| `pyrovigil_fire_detected` | Foc nou dins radi | `fire_id`, `distance_km`, `nature`, `concelho`, `latitude`, `longitude` |

### Blueprint ⭐

**El gran avantatge sobre wildfire-monitor**: inclou un blueprint a `blueprints/automation/fire_notification.yaml`:

- Servei de notificació a escollir.
- **Filtre per severitat** (low/moderate/high/extreme).
- **Filtre per distància màxima** addicional.
- **Critical alert** (by-passa Do Not Disturb) opcional.
- Botó "Open Map" que obre Google Maps amb el punt del foc.

La notificació inclou tipus, ubicació, distància, severitat.

### Patrons d'automació documentats

- `fire_nearby` → activar aspersors (`switch.garden_sprinklers`).
- `high_fire_risk` → alerta diària al matí.

### Punts forts (per copiar)

1. **Multi-font agregada** (incidents + risc + avisos meteo) — visió completa.
2. **Tracking de recursos desplegats** (personal/vehicles/aeronaus) — dada clau que wildfire-monitor no té.
3. **Escalat de risc 1-5** estructurat (IPMA RCM) — equivalent al que volem amb Pla Alfa.
4. **`high_fire_risk` binary sensor** — un disparador d'automació natural pel matí.
5. **Entitats toggleable** individualment (enabled/disabled per defecte) — neteja el registre d'entitats per a usuaris casuals.
6. **Blueprint inclòs** — zero-YAML per a la majoria.
7. **Selecció per severitat** al blueprint — evita soroll de focs petits.
8. **CI, ruff, pytest, coverage** — enginyeria seriosa.

### Punts febs (a millorar)

1. **No usa `geo_location` entities** — no hi ha markers al mapa nadiu de HA. ⚠️ Gran forfeit vs wildfire-monitor. Necessitem els dos.
2. **Cap mapa al README** — la visió "on és el foc" es perd.
3. **No hi ha tracking d'evolució/fase** — `pyrovigil_fire_detected` només és de "nou", no de canvi d'estat. Catalunya té `COM_FASE` — podem fer-ho millor.
4. **Sense event de "resolució"** — no saps quan un foc ha passat a extingit.
5. **`fire_id` opac** — a diferència de `ACT_NUM_ACTUACIO` que és llegible.
6. **Sense plantilla de dashboard** — l'usuari ha de muntar-se les cards.

---

## 3. Comparativa directa

| Feature | wildfire-monitor | pyrovigil | **ha-incendiscat (objectiu)** |
| --- | :---: | :---: | :---: |
| Font | REST | ArcGIS FS | **ArcGIS FS** (com pyrovigil) |
| Sense clau | ✅ | ✅ | ✅ |
| Setup per mapa | ✅ | ❌ form coord | ✅ |
| `zone.home` llegit en calent | ✅ | ❌ guardat | ✅ |
| `geo_location` markers | ✅ | ❌ | ✅ |
| Comptador actius | ✅ | ✅ | ✅ |
| Més proper | ✅ | ✅ | ✅ |
| Més gran (mida) | ✅ | ❌ | ⚠️ (no tenim mida per punt — millorem amb recursos) |
| Recursos desplegats | ❌ | ✅ | ✅ (`ACT_NUM_VEH`) |
| **Per fase** | ❌ (només containment %) | ❌ | ✅ (`COM_FASE`) |
| **Filtre per tipus** (VF/VA/VU) | ❌ | ❌ | ✅ |
| Risc previst (avui) | ❌ | ✅ (IPMA) | ⚠️ (Pla Alfa) |
| Risc demà | ❌ | ✅ | ⚠️ |
| `binary_sensor.fire_nearby` | ✅ (`wildfire_alert`) | ✅ | ✅ |
| `binary_sensor.high_risk` | ❌ | ✅ | ✅ |
| Event "foc nou" | ✅ | ✅ | ✅ |
| Event "foc extingit/resolt" | ❌ | ❌ | ✅ |
| Event "canvi de fase" | ❌ | ❌ | ✅ |
| Blueprint notificacions | ❌ | ✅ | ✅ (ampliat) |
| Filtre per severitat al blueprint | ❌ | ✅ | ✅ |
| Botó "Open Map" | ❌ | ✅ | ✅ |
| Entitats toggleable | ❌ | ✅ | ✅ |
| Dashboard card d'exemple | ✅ | ❌ | ✅ |
| Perímetre cremat (polígon) | ⚠️ roadmap | ❌ | ❌ (no publicat) |
| CI + ruff + pytest | ✅ | ✅ | ✅ |

### Resum

- **De wildfire-monitor agafem**: setup per mapa, `zone.home` live, `geo_location` entities, plantilla de dashboard, plantilla de notificació.
- **De pyrovigil agafem**: patró multi-font, tracking de recursos, escala de risc, `high_risk` binary, entitats toggleable, blueprint, enginyeria (CI/ruff/pytest).
- **Afegim nosaltres** (no hi ha a cap): tracking per **fase** catalana, event de **resolució**, event de **canvi de fase**, filtre per **subtipus** (VF/VA/VU).

Aquesta taula alimenta directament el [`03-feature-spec.md`](03-feature-spec.md).
