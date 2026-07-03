# ha-incendiscat

> Integració de Home Assistant per al seguiment d'**incendis forestals a Catalunya** en temps real: mapa, sensors agregats, alertes i un blueprint de notificacions, amb dades públiques dels **Bombers de la Generalitat** i el **Pla Alfa** (Agents Rurals).

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
![GitHub release (latest by date)](https://img.shields.io/github/v/release/pmontp19/ha-incendiscat)
![CI](https://github.com/pmontp19/ha-incendiscat/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/github/license/pmontp19/ha-incendiscat)

<!-- TODO: captura de pantalla de la Map card amb els incendis actius -->

## Instal·lació

### Via HACS (recomanat)

1. HACS → **Integrations** → menú (⋮) → **Custom repositories**.
2. Afegiu `https://github.com/pmontp19/ha-incendiscat`, categoria **Integration**.
3. Cerqueu **"Incendis Catalunya"** dins HACS i instal·leu-la.
4. Reinicieu Home Assistant.
5. **Configuració → Dispositius i serveis → Afegeix integració** → cerqueu **"Incendis Catalunya"**.

### Manual

1. Copieu `custom_components/incendiscat/` d'aquest repositori dins la carpeta `custom_components/` de la vostra instal·lació de Home Assistant.
2. Reinicieu Home Assistant.
3. Afegiu la integració des de **Configuració → Dispositius i serveis**.

## Configuració

El flux de configuració té dos passos:

1. **Ubicació i radis** — mapa amb marcador arrossegable, preomplert amb `zone.home`. Dos radis:
   - **Radi de seguiment** (el cercle del mapa, 5–200 km, per defecte 100 km): quins incendis es fan seguir — generen `geo_location` i compten als sensors agregats.
   - **Radi d'alerta** (per defecte 30 km, no pot ser més gran que el de seguiment): quins incendis activen `binary_sensor.incendis_catalunya_fire_nearby` i els events de proximitat.

   Distingir-los permet veure tots els incendis de Catalunya al mapa però rebre alertes només dels que són realment a prop.

2. **Filtres i sondeig** — subtipus a incloure (`VF`/`VA`/`VU`, per defecte només forestal), fases considerades "actives" (per defecte `Actiu`+`Estabilitzat`), interval de sondeig en minuts (1–60, per defecte 5) i nombre mínim de vehicles per considerar un incident.

Un cop configurada, **Configuració → Dispositius i serveis → Incendis Catalunya → Configurar** obre les opcions: els mateixos filtres més el **llindar de risc alt** (0–4 del Pla Alfa, per defecte 2 = Alt).

Per moure la ubicació vigilada (no els filtres) feu servir **Reconfigurar** a la mateixa integració, que reobre el pas 1.

## Entitats

Totes les entitats agregades pengen d'un únic dispositiu **"Incendis Catalunya"**. A més, hi ha una entitat `geo_location` per cada incendi actiu dins del radi de seguiment.

| Entitat | Descripció |
| --- | --- |
| `sensor.incendis_catalunya_active_fires` | Nombre d'incendis actius (fases configurades) dins el radi de seguiment. Atributs: `last_updated`, `total_in_track_radius`, `total_in_alert_radius`. |
| `sensor.incendis_catalunya_nearest_fire_distance` | Distància en km a l'incendi actiu més proper. `-1` si no n'hi ha cap. |
| `sensor.incendis_catalunya_nearest_fire_municipality` | Municipi de l'incendi actiu més proper. `"—"` si no n'hi ha cap. |
| `sensor.incendis_catalunya_fires_per_phase` | Total d'incendis en seguiment; atributs `actiu`, `estabilitzat`, `controlat`, `extingit`. |
| `sensor.incendis_catalunya_fires_per_type` | Total d'incendis en seguiment; atributs `vf`, `va`, `vu`. |
| `sensor.incendis_catalunya_deployed_vehicles` | Suma de vehicles desplegats als incendis en seguiment. |
| `sensor.incendis_catalunya_fire_risk_pla_alfa` | Nivell de risc Pla Alfa (0–4) del municipi de la ubicació configurada. Atributs: `nivell_text`, `comarca`, `municipi`, `data_vigencia`, `hora_vigencia`, `perill_dema`. |
| `binary_sensor.incendis_catalunya_fire_nearby` | `on` si hi ha un incendi actiu dins el radi d'alerta. Atributs (quan `on`): `nearest_act_num`, `nearest_distance_km`, `nearest_municipi`, `nearest_fase`. |
| `binary_sensor.incendis_catalunya_high_fire_risk` | `on` si `fire_risk` ≥ llindar configurat. |
| `geo_location.foc_<municipi>` | Un per incendi en seguiment; estat = distància en km. Atributs: `source` (`"incendiscat"`), `latitude`, `longitude`, `act_num`, `fase`, `tipus`, `tipus_desc`, `municipi`, `data_inici`, `data_fi`, `vehicles`, `situacio`, `updated_at`, `url`. |

Entitats de diagnòstic (necessàries perquè la font no és una API oficial i pot fallar):

| Entitat | Descripció |
| --- | --- |
| `binary_sensor.incendis_catalunya_service_connected` | `on` si l'última consulta al FeatureServer dels Bombers ha anat bé. |
| `sensor.incendis_catalunya_last_update` | Marca de temps de l'última sincronització correcta. |
| `sensor.incendis_catalunya_last_update_status` | `success` o `error_<codi>` (p.ex. `error_timeout`, `error_http_404`). |

## Events

Es disparen a `hass.bus` per fer-los servir en automacions (`trigger: event`):

| Event | Quan es dispara |
| --- | --- |
| `incendiscat_fire_detected` | Un incendi nou compleix els filtres i entra al radi de seguiment. |
| `incendiscat_fire_resolved` | Un incendi en seguiment passa a `Extingit` (o el flux de fases el resol). |
| `incendiscat_phase_change` | Un incendi en seguiment canvia de fase (p.ex. `Actiu → Estabilitzat`). |
| `incendiscat_service_degraded` | El FeatureServer falla de forma persistent (3 errors seguits del mateix tipus, p.ex. 404) — es crea també una incidència de reparació (repair issue). |

Payload de `incendiscat_fire_detected`:

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
  "url": "https://experience.arcgis.com/experience/f6172fd2d6974bc0a8c51e3a6bc2a735"
}
```

Payload de `incendiscat_phase_change`:

```json
{
  "act_num": "262311630",
  "municipi": "Sant Quirze Safaja",
  "old_fase": "Actiu",
  "new_fase": "Estabilitzat",
  "distance_km": 12.4
}
```

Payload de `incendiscat_fire_resolved`:

```json
{
  "act_num": "262311630",
  "municipi": "Sant Quirze Safaja",
  "duration_min": 187,
  "final_fase": "Extingit"
}
```

## Blueprint

La integració inclou un blueprint d'automació a [`blueprints/automation/incendiscat_fire_notification.yaml`](blueprints/automation/incendiscat_fire_notification.yaml) que notifica quan hi ha un incendi nou (o, opcionalment, canvis de fase o resolucions), amb botó per obrir el mapa.

[![Open your Home Assistant instance and show the blueprint import dialog.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Fpmontp19%2Fha-incendiscat%2Fmain%2Fblueprints%2Fautomation%2Fincendiscat_fire_notification.yaml)

Manualment: **Configuració → Automacions i escenes → Blueprints → Importa un blueprint** i enganxeu l'URL anterior, o copieu el fitxer a `blueprints/automation/incendiscat/` de la vostra instal·lació.

Opcions del blueprint:

| Camp | Tipus | Descripció |
| --- | --- | --- |
| `notification_service` | selector | Servei de notificació a fer servir (`notify.notify` per defecte). |
| `minimum_fase` | selector | Fase mínima per notificar (`Actiu`/`Estabilitzat`/`Controlat`/`Extingit`). Per defecte `Actiu`. |
| `minimum_vehicles` | int | Filtre de magnitud. Per defecte 0. |
| `maximum_distance` | int | Km màxims (0 = usa el radi d'alerta configurat). |
| `critical_alert` | bool | Notificació crítica que travessa el mode No molestar. Per defecte fals. |
| `include_resolved` | bool | Notifica també quan un incendi es resol. Per defecte fals. |
| `include_phase_changes` | bool | Notifica els canvis de fase. Per defecte fals. |
| `open_map_url` | select | Mapa a obrir des del botó: visor oficial dels Bombers, Google Maps o OpenStreetMap. |

## Dashboard d'exemple

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
      - sensor.incendis_catalunya_active_fires
      - sensor.incendis_catalunya_nearest_fire_distance
      - sensor.incendis_catalunya_nearest_fire_municipality
      - sensor.incendis_catalunya_deployed_vehicles
      - binary_sensor.incendis_catalunya_fire_nearby
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

## Patrons d'automació

1. **Notificació push quan apareix un foc nou dins X km** — feu servir el blueprint (secció anterior).
2. **Encendre aspersors o tancar persianes** quan `binary_sensor.incendis_catalunya_fire_nearby` passa a `on`.
3. **Avís al matí si avui hi ha risc alt** — `binary_sensor.incendis_catalunya_high_fire_risk` passa a `on`.
4. **Registre per a postmortem** — `incendiscat_phase_change` cap a una notificació silenciosa o un `logbook.log`.
5. **Tancament automàtic de finestres** si `fire_nearby` és `on` i la qualitat de l'aire (sensor PM2.5) és dolenta.

Exemple: avís matinal de risc alt (patró 3):

```yaml
alias: Avisa si avui hi ha risc alt d'incendi
trigger:
  - platform: state
    entity_id: binary_sensor.incendis_catalunya_high_fire_risk
    to: "on"
condition:
  - condition: time
    after: "07:00:00"
    before: "10:00:00"
action:
  - service: notify.notify
    data:
      title: "🔥 Risc alt d'incendi avui"
      message: >
        Nivell {{ state_attr('sensor.incendis_catalunya_fire_risk_pla_alfa', 'nivell_text') }}
        a {{ state_attr('sensor.incendis_catalunya_fire_risk_pla_alfa', 'municipi') }}. Evita fer foc.
```

Exemple: tancar persianes quan hi ha un incendi a prop (patró 2):

```yaml
alias: Tanca persianes si hi ha un incendi a prop
trigger:
  - platform: state
    entity_id: binary_sensor.incendis_catalunya_fire_nearby
    to: "on"
action:
  - service: cover.close_cover
    target:
      entity_id: cover.persianes_exterior
```

## Fonts de dades

| Font | Ús | Cadència |
| --- | --- | --- |
| Bombers de la Generalitat — FeatureServer `ACTUACIONS_URGENTS` | Incendis en temps real (punts, fase, recursos) | Sondeig configurable, 1–60 min (per defecte 5 min) |
| Agents Rurals — Pla Alfa (FeatureServers `Pla_Alfa_*`) | Risc d'incendi diari (0–4) per municipi/comarca | Sondeig fix cada 6 h (l'oficial s'actualitza a les 00:00 i 9:30h) |

Detalls tècnics complets (schemas, glossaris, endpoints) a [`docs/01-data-sources.md`](docs/01-data-sources.md).

**Cap d'aquestes fonts és una API oficialment suportada** — són FeatureServers públics d'ArcGIS que poden canviar d'adreça o d'esquema sense avís. Quan això passa:

- `binary_sensor.incendis_catalunya_service_connected` passa a `off`.
- Després de 3 errors seguits del mateix tipus, es dispara l'event `incendiscat_service_degraded` i s'obre una incidència de reparació (repair issue) a Home Assistant amb enllaç a [GitHub Issues](https://github.com/pmontp19/ha-incendiscat/issues).
- Les dades ja carregades es mantenen (no s'esborren) fins que el servei torna.

## Seguretat i dades

- Les coordenades de la ubicació configurada (`zone.home` o la que triïs) només s'envien als endpoints de *point-in-polygon* del Pla Alfa (Agents Rurals) — és el disseny esperat, cal per determinar el municipi/comarca i el nivell de risc.
- Les entitats de diagnòstic redacten `latitude`/`longitude` (i altres camps identificatius de la configuració) abans d'exportar-se.
- Camps com `municipi` o `tipus_desc` provenen d'un servei extern no oficial i s'han de tractar com a text no fiable: no els renderitzis amb `allow_html: true` en targetes personalitzades (p.ex. Markdown card).

## Desenvolupament

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements_dev.txt

.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

Documentació d'arquitectura i disseny a [`docs/`](docs/): [fonts de dades](docs/01-data-sources.md), [integracions de referència](docs/02-existing-integrations.md), [especificació funcional](docs/03-feature-spec.md), [arquitectura](docs/04-architecture.md), [pla d'implementació](docs/05-implementation-plan.md).

Voleu contribuir? Mireu [`CONTRIBUTING.md`](CONTRIBUTING.md) (convenció de commits, cicle de release, tests).

## Integracions de referència

S'han analitzat en profunditat dues integracions HACS existents per aprendre'n els patrons: [`johnbr/ha-wildfire-monitor`](https://github.com/johnbr/ha-wildfire-monitor) (Califòrnia, CAL FIRE API REST) i [`Duarte-Mercedes-Santos/ha-pyrovigil`](https://github.com/Duarte-Mercedes-Santos/ha-pyrovigil) (Portugal, ANEPC ArcGIS + IPMA), aquesta última especialment rellevant perquè fa servir el mateix patró ArcGIS FeatureServer que Catalunya. Anàlisi completa a [`docs/02-existing-integrations.md`](docs/02-existing-integrations.md).

## Descàrrec

Aquest projecte **no està afiliat ni aprovat** pel cos de Bombers de la Generalitat de Catalunya, els Agents Rurals, el Departament d'Agricultura ni cap altra institució de la Generalitat. Les dades són públiques però el FeatureServer no es publica com a API oficialment suportada — pot canviar sense avís.

## Llicència

MIT.
