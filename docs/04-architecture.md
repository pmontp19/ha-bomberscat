# Arquitectura tècnica — `ha-incendiscat`

Estructura interna del codi i decisions de disseny. Mantingut coherent amb les integracions modernes de HA (config_flow, DataUpdateCoordinator, entitats toggleable).

---

## 1. Layout del repositori

```
ha-incendiscat/
├── custom_components/
│   └── incendiscat/
│       ├── __init__.py
│       ├── manifest.json            # domain, version, requirements, iot_class
│       ├── config_flow.py           # dues passes (mapa + filtres)
│       ├── const.py                 # domains, defaults, claus config
│       ├── coordinator.py           # IncendiscatDataUpdateCoordinator
│       ├── arcgis.py                # client FeatureServer (aiohttp) + parsing
│       ├── models.py                # dataclasses: Incident, Fase, Tipus
│       ├── geo.py                   # haversine, filtre per radi
│       ├── sensor.py                # sensors agregats
│       ├── binary_sensor.py         # fire_nearby, high_risk
│       ├── geo_location.py          # entities per incendi
│       ├── diagnostics.py           # service_connected, last_update
│       ├── strings.json             # noms UI del config_flow
│       ├── services.yaml            # serveis exposem (p.ex. incendiscat.refresh)
│       ├── icons.py                 # mdi per fase/tipus
│       └── translations/
│           ├── ca.json
│           ├── es.json
│           └── en.json
├── blueprints/
│   └── automation/
│       └── incendiscat_fire_notification.yaml
├── tests/
│   ├── fixtures/
│   │   ├── featureserver_sample.json
│   │   └── featureserver_empty.json
│   ├── test_arcgis.py
│   ├── test_geo.py
│   ├── test_coordinator.py
│   └── test_lifecycle.py
├── docs/
│   ├── 01-data-sources.md
│   ├── 02-existing-integrations.md
│   ├── 03-feature-spec.md
│   └── 04-architecture.md           # aquest
├── .github/workflows/
│   ├── ci.yml                       # ruff + pytest + coverage
│   └── validate.yml                 # hassfest + HACS
├── hacs.json
├── pyproject.toml
├── README.md
└── LICENSE
```

---

## 2. `manifest.json`

```json
{
  "domain": "incendiscat",
  "name": "Incendis Catalunya",
  "codeowners": ["@pere"],
  "config_flow": true,
  "documentation": "https://github.com/<user>/ha-incendiscat",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/<user>/ha-incendiscat/issues",
  "requirements": [],
  "version": "0.1.0",
  "zeroconf": []
}
```

- **`iot_class: cloud_polling`** —dades al núvol, nosaltres fem polling.
- **`requirements: []`** — només `aiohttp` (ja a HA core). Zero dependències externes és un objectiu explícit.
- **`config_flow: true`** — setup via UI.

---

## 3. Client ArcGIS (`arcgis.py`)

Wrapper async mínim sobre el FeatureServer. Fa servir `aiohttp` directament (com a HA core).

### Responsabilitats

1. **Query paginada** — recórrer tot el dataset amb `resultOffset` fins que `exceededTransferLimit == false`.
2. **Sync incremental** — si tenim `last_data_act`, filtra per `DATA_ACT > :last` i ordena ASC. (**No** `EditDate`: no és consultable al servei real — HTTP 400; vegeu `01-data-sources.md` §2.)
3. **Dedup per `ACT_NUM_ACTUACIO`** — la vista és un log d'snapshots (una actuació pot tenir 2+ files); ens quedem la fila amb `DATA_ACT` màxim com a estat actual (vegeu `01-data-sources.md` §2).
4. **Conversió a dataclasses** — `Incident` amb camps forts.
4. **Tolerància d'esquema** — camps nous/renomenats no trenquen res (usem `.get()`).
5. **Retries** amb backoff exponencial si 5xx.

### Funció principal

```python
async def fetch_incidents(
    session: aiohttp.ClientSession,
    since: datetime | None = None,
    out_sr: int = 4326,
) -> list[Incident]:
    """Tots els incidents nous/modificats des de `since` (o tots)."""
    where = "1=1" if since is None else f"EditDate > TIMESTAMP '{since.isoformat()}'"
    page_size = 2000
    offset = 0
    out: list[Incident] = []
    while True:
        params = {
            "where": where,
            "outFields": "*",
            "outSR": out_sr,
            "f": "geojson",
            "resultRecordCount": page_size,
            "resultOffset": offset,
            "orderByFields": "EditDate ASC",
        }
        async with session.get(FEATURESERVER_URL, params=params) as r:
            r.raise_for_status()
            data = await r.json()
        out.extend(Incident.from_feature(f) for f in data.get("features", []))
        if not data.get("exceededTransferLimit"):
            break
        offset += page_size
    return out
```

### Caching

- El coordinator manté un dict `{act_num: Incident}` en memòria (no persisteix).
- Si el FeatureServer cau, **es conserva l'estat anterior** i només es marca `service_connected = false`.

---

## 4. Models (`models.py`)

```python
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

class Fase(str, Enum):
    # COM_FASE null -> ACTIU: és com ho renderitza el webmap oficial dels Bombers
    ACTIU = "Actiu"
    ESTABILITZAT = "Estabilitzat"
    CONTROLAT = "Controlat"
    EXTINGIT = "Extingit"

    @property
    def severity(self) -> int:  # 0..3 per a ordenació de gravetat
        return {Fase.ACTIU: 3, Fase.ESTABILITZAT: 2,
                Fase.CONTROLAT: 1, Fase.EXTINGIT: 0}[self]

class Tipus(str, Enum):
    FORESTAL = "VF"
    AGRICOLA = "VA"
    URBANA = "VU"

@dataclass(frozen=True, slots=True)
class Incident:
    act_num: str
    lat: float
    lon: float
    fase: Fase
    tipus: Tipus
    tipus_desc: str
    municipi: str | None
    inici: datetime | None
    fi: datetime | None
    vehicles: int
    situacio: str | None
    edit_date: datetime
    creation_date: datetime | None

    @classmethod
    def from_feature(cls, feature: dict) -> "Incident":
        p = feature.get("properties", {})
        lon, lat = feature.get("geometry", {}).get("coordinates", [None, None])
        return cls(
            act_num=p.get("ACT_NUM_ACTUACIO", ""),
            lat=float(lat) if lat is not None else 0.0,
            lon=float(lon) if lon is not None else 0.0,
            fase=_parse_fase(p.get("COM_FASE")),  # null -> Fase.ACTIU (com el visor oficial)
            tipus=Tipus(p.get("TAL_COD_ALARMA2") or "VF"),
            tipus_desc=p.get("TAL_DESC_ALARMA2", ""),
            municipi=p.get("MUNICIPI_SIG") or p.get("MUNICIPI_DPX"),
            inici=_ts(p.get("ACT_DAT_INICI")),
            fi=_ts(p.get("ACT_DAT_FI")),
            vehicles=int(p.get("ACT_NUM_VEH") or 0),
            situacio=p.get("ACT_SITUACIO"),
            edit_date=_ts(p.get("EditDate")),
            creation_date=_ts(p.get("CreationDate")),
        )
```

---

## 5. Coordinator (`coordinator.py`)

`DataUpdateCoordinator` amb `update_interval = timedelta(minutes=scan_interval)`.

Convencions actuals: el coordinator es guarda a **`entry.runtime_data`** amb alias tipat (`type IncendiscatConfigEntry = ConfigEntry[IncendiscatDataUpdateCoordinator]`), **no** a `hass.data[DOMAIN]` (regla [runtime-data](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data/)). A `async_setup_entry`: `await coordinator.async_config_entry_first_refresh()` perquè el setup falli net si el primer fetch falla. `always_update=False` (l'estat implementa `__eq__`) per estalviar escriptures d'estat.

### Estat que manté

```python
@dataclass
class IncendiscatState:
    incidents: dict[str, Incident]          # act_num -> Incident (actius tracked)
    last_edit_date: datetime | None         # per sync incremental
    last_success: datetime | None
    last_error: str | None
    # Snapshots per detectar canvis
    prev_fases: dict[str, Fase]             # act_num -> fase del cicle anterior
    prev_act_nums: set[str]                 # per detectar altes i baixes
```

### Lògica d'actualització

```python
async def _async_update_data(self) -> IncendiscatState:
    try:
        nous_i_modificats = await fetch_incidents(self.session, since=state.last_edit_date)
    except ClientError as e:
        # No esborrem res, marquem error
        state.last_error = str(e)
        return state

    # 1. Aplicar altes/modificacions
    for inc in nous_i_modificats:
        if _passa_filts(inc, self.config) and _inside_radius(inc, home, track_radius):
            state.incidents[inc.act_num] = inc
        else:
            state.incidents.pop(inc.act_num, None)

    # 2. Detectar canvis -> events
    _emit_events(self.hass, state, nous_i_modificats)

    # 3. Netejar extingits antics
    _cleanup_resolved(state, grace_minutes=60)

    state.last_edit_date = max((i.edit_date for i in state.incidents.values()),
                                default=state.last_edit_date)
    state.last_success = utcnow()
    state.last_error = None
    return state
```

### Filtres (en `_passa_filts`)

```python
def _passa_filts(inc: Incident, cfg: Config) -> bool:
    if inc.tipus.value not in cfg.subtipus:
        return False
    if inc.fase not in cfg.active_phases and inc.fase != Fase.EXTINGIT:
        # Extingit el deixem passar per detectar resolució, després es neteja
        return False
    if inc.vehicles < cfg.min_vehicles:
        return False
    return True
```

### Càlcul de distància

Haversine pur (no depen de llibreries externes). Cacheja distàncies per act_num perquè la ubicació d'un incendi no canvia.

---

## 6. Detecció d'events (`_emit_events`)

Cridat al final de cada cicle. Dispara exactament els events de `feature-spec.md` §4:

```python
def _emit_events(hass, state, modified):
    for inc in modified:
        act = inc.act_num
        # 1. Nou detectat
        if act not in state.prev_act_nums and _is_in_state(state, act):
            hass.bus.async_fire("incendiscat_fire_detected", _payload_detected(inc, home))
        # 2. Canvi de fase
        old = state.prev_fases.get(act)
        if old and old != inc.fase:
            hass.bus.async_fire("incendiscat_phase_change", {
                "act_num": act, "municipi": inc.municipi,
                "old_fase": old.value, "new_fase": inc.fase.value,
                "distance_km": _dist(inc, home),
            })
    # 3. Resolts (eren tracked, ja no hi són o han passat a Extingit)
    for act in state.prev_act_nums - {i.act_num for i in modified if _is_in_state(state, act)}:
        # era tracked i ha desaparegut
        prev_inc = state._snapshot.get(act)
        if prev_inc:
            hass.bus.async_fire("incendiscat_fire_resolved", _payload_resolved(prev_inc))
    # Snapshot pel següent cicle
    state.prev_act_nums = {i.act_num for i in state.incidents.values()}
    state.prev_fases = {i.act_num: i.fase for i in state.incidents.values()}
    state._snapshot = dict(state.incidents)
```

---

## 7. Entities

### Patró comú a tots els platforms

```python
PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.GEO_LOCATION)
```

Cada `entity.py` té:

```python
class IncendiscatEntity(CoordinatorEntity[IncendiscatState]):
    _attr_has_entity_name = True
    _attr_translation_key = "..."   # per a multi-idioma

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self._config = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_<entity_name>"
        self._attr_device_info = DeviceInfo(
            identifiers={("incendiscat", config_entry.entry_id)},
            name="Incendis Catalunya",
            manufacturer="Generalitat de Catalunya",
            model="Incendis forestals",
            entry_type=DeviceEntryType.SERVICE,
        )
```

### `geo_location.py`

Una entity dinàmica per incendi. S'usa `async_track_entity_registry_updated_state` per registrar/desregistrar al catàleg.

Per cada `Incident` tracked:

```python
class IncendiscatFireLocation(GeolocationLocation):
    _attr_icon = "mdi:fire"
    _attr_source = "incendiscat"

    def __init__(self, incident, distance_km):
        self._incident = incident
        self._attr_name = f"Foc {incident.municipi or incident.act_num}"
        self._attr_unique_id = f"incendiscat_{incident.act_num}"
        self._attr_latitude = incident.lat
        self._attr_longitude = incident.lon
        self._attr_distance = distance_km  # km (state per geo_location)
        self._attr_extra_state_attributes = {
            "source": "incendiscat",
            "act_num": incident.act_num,
            "fase": incident.fase.value,
            "tipus": incident.tipus.value,
            "tipus_desc": incident.tipus_desc,
            "municipi": incident.municipi,
            "data_inici": incident.inici.isoformat() if incident.inici else None,
            "data_fi": incident.fi.isoformat() if incident.fi else None,
            "vehicles": incident.vehicles,
            "situacio": incident.situacio,
            "updated_at": incident.edit_date.isoformat(),
            "url": _build_url(incident),
        }
```

### Sensors agregats

Subclasses de `IncendiscatEntity` + `SensorEntity`. Implementen `@property state` i `_attr_extra_state_attributes` llegint de `self.coordinator.data`.

Icones segons `icons.py`:

| Fase | mdi |
| --- | --- |
| Actiu | `mdi:fire` |
| Estabilitzat | `mdi:fire-alert` |
| Controlat | `mdi:fire-off` |
| Extingit | `mdi:fire-extinguisher` |

---

## 8. Config flow (`config_flow.py`)

```python
class IncendiscatConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title=f"Incendis Catalunya",
                data=user_input,
            )
        home = await self._get_home_zone()
        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema_with_map(home),  # step 1: mapa
            description_placeholders={"home_lat": home.lat, ...},
        )

    async def async_step_filters(self, user_input=None):
        # step 2: filtres
        ...

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return IncendiscatOptionsFlow()
```

El schema de la passa 1 fa servir `selector.LocationSelector(LocationSelectorConfig(radius=True))` (retorna `{latitude, longitude, radius}`, radi en **metres**).

Convencions actuals (validades contra la doc oficial, 2026):

- **Radi com a `vol.Optional` amb default** + validació defensiva: el widget del frontend pot enviar el form sense radi encara que sigui `Required` ([core#108960](https://github.com/home-assistant/core/issues/108960)).
- **`async_step_reconfigure`** al ConfigFlow (HA ≥ 2024.4) per canviar ubicació/radis post-setup; l'OptionsFlow només per a opcions (filtres, polling, llindars) ([reconfiguration-flow](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/reconfiguration-flow/)).
- **OptionsFlow sense `self.config_entry` manual** (deprecat, eliminació HA 2025.12) ni `OptionsFlowWithConfigEntry` — `self.config_entry` ja hi és automàticament ([blog 2024-11-12](https://developers.home-assistant.io/blog/2024/11/12/options-flow/)).
- **Sense YAML**: config-flow-only per ADR-0010. Cap `async_setup` de plataforma YAML.

---

## 9. Resiliència

### Estratègia davant fallades del FeatureServer

| Fallada | Comportament |
| --- | --- |
| Timeout / xarxa | Retry 3 vegades amb backoff (1s, 2s, 4s). Després `service_connected=false`, es conserva estat cachejat. |
| HTTP 4xx | No hi ha retry. Log + `service_connected=false`. Probablement canvi d'esquema/URL. |
| HTTP 5xx | Retry amb backoff com a timeout. |
| JSON invàlid | Log, conserva cache. |
| Camp esperat faltant | `.get()` + valor per defecte a `Incident.from_feature`. Log warning. |
| URL canviada (404 persistent) | Event `incendiscat_service_degraded`. Notificació a l'usuari perquè faci un issue al repo. |

### Rate limit

- No s'ha observat throttle al FeatureServer (servei intern dels Bombers).
- Precaució: polling mínim **1 minut**, no menys.
- Sync incremental (`since=EditDate`) minimitza transferència.

---

## 10. Tests

### Fixtures

- `featureserver_sample.json`: resposta real capturada amb 5 incidents (incloent-hi Sant Quirze Safaja).
- `featureserver_empty.json`: `{"features": [], "exceededTransferLimit": false}`.
- `featureserver_paginated.json` + mock que retorna `exceededTransferLimit=true` per validar paginació.

### Cobertura mínima

- `test_arcgis.py`: parsing, paginació, sync incremental, tolerància a camps null.
- `test_geo.py`: haversine, filtre per radi (casos cantonada: mateix punt, antípodes, hemisferi).
- `test_coordinator.py`: cicle complet (altes, baixes, canvis de fase), events disparats correctament.
- `test_lifecycle.py`: cleanup de resolts després del `grace_minutes`.

---

## 11. CI / CD

`.github/workflows/ci.yml`:
- `ruff check .` + `ruff format --check .`
- `pytest --cov=custom_components/incendiscat --cov-report=xml`
- `hassfest` (validació manifest)
- Validació HACS

Release amb tags `vX.Y.Z` i `release-please` (alineat amb integracions de referència).

---

## 12. Roadmap post-v1

- **Perímetre cremat** via integració amb un servei de polygons (pendent de trobar-ne font).
- **Històric SQLite**: persistir incidents per respondre preguntes com "quants focs aquest estiu".
- **Card Lovelace pròpia** (`incendiscat-map-card`) amb llegenda per fase i filtres visuals.
- **Suport Andorra / França catalana** si es troben FeatureServers equivalents (els Bombers de l'Est francès i el cos andorrà també fan servir Esri).
- **Mode "campanya forestal"**: polling més freqüent (1 min) entre juny i octubre, menys freqüent la resta de l'any.
- **Watchdog de salut del servei**: tasca diària que comprova que el FeatureServer respon i dispara notificació si porta caigut >24h.

---

## 13. Decisions arquitecturals (AD-style)

| Decisió | Per què |
| --- | --- |
| Cap dependència externa (`requirements: []`) | Minimitzar risc de trencar-se amb canvis de HA; només `aiohttp` ja hi és. |
| `geo_location` per incendi + sensors agregats | Cobrir els dos patrons de consum: Map card i templates. Pyrovigil només té sensors, wildfire-monitor només té geo_location. Nosaltres dos. |
| Distinció `track_radius` vs `alert_radius` | Permet seguir tot Catalunya però només alertar a la vora. Wildfire-monitor fa això. |
| `zone.home` llegit en calent | Tolerant a canvis de casa sense reconfigurar. |
| Events a `hass.bus` (no només binary sensors) | El patró "event-driven" de HA és més natural per a "foc nou" que un `binary_sensor` amb edge-trigger. Pyrovigil ho fa bé amb `fire_detected`; nosaltres afegim `fire_resolved` i `phase_change`. |
| Tolerància d'esquema (`.get`) | El FeatureServer no és API oficial; pot canviar. |
| Grace period per eliminar extingits | Evitar flickering d'entity registre (crear/destruir massivament). |
| `DeviceInfo.entry_type=SERVICE` | Tractar-la com a servei extern, no com a dispositiu físic. Convenció HA per integracions cloud. |
| Multi-idioma (`translations/`) | Català nadiu, castellà i anglès (tercers no catalanoparlants). |
| Llenguatge codi: anglès; UI: català | Convenció HA + respecte al context d'ús. |
