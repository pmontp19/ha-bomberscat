# Fonts de dades — Incendis forestals a Catalunya

Resultat de la recerca feta el 2026-07-02 inspeccionant els visors públics d'`interior.gencat.cat` i descobrint els serveis ArcGIS que hi ha per sota.

---

## 1. Visor públic d'Interior

Pàgina d'entrada: <https://interior.gencat.cat/ca/incendis-forestals/inici/index.html>

Aquesta pàgina incrusta **quatre visors**:

| Visor | Propietari | URL del visor | Ús |
| --- | --- | --- | --- |
| Mapa del Pla Alfa | Agents Rurals | <https://experience.arcgis.com/experience/2cf7ebbe492f401db826cb21eae9bfae> | Nivell d'activació, restriccions |
| Mapa d'actuacions dels Bombers | Bombers (DG Emergències) | <https://experience.arcgis.com/experience/f6172fd2d6974bc0a8c51e3a6bc2a735> | Incendis de vegetació en temps real |
| Mapa del perill d'incendi forestal | Dept. Agricultura | <http://agricultura.gencat.cat/ca/ambits/medi-natural/incendis-forestals/mapes/mapa-perill-incendi/> | Risc diari (raster GIF) |
| Mapa d'incidències viàries | Trànsit | — | Fora d'abast |

Els dos primers són **ArcGIS Experience Builder** — aplicacions JS pures que consumirxen serveis REST. Inspeccionant-los s'han pogut extreure els `FeatureServer` públics subjacents, que són els que consumirem.

### Mètode de descobriment

```
1. https://www.arcgis.com/sharing/rest/content/items/f6172fd2d6974bc0a8c51e3a6bc2a735?f=json
   → metadata del item (tipus "Web Experience", owner "AdminInterior", org "interiorGencat.maps.arcgis.com")

2. https://www.arcgis.com/sharing/rest/content/items/f6172fd2d6974bc0a8c51e3a6bc2a735/data?f=json
   → configuració del visor. A `dataSources` hi ha els webmaps i FeatureServers que usa.
```

El visor dels Bombers té 3 datasources (Web/Mobile/Tablet) que apunten tots al mateix FeatureServer de treball.

---

## 2. FeatureServer dels Bombers ⭐ (font principal)

### Endpoint

```
https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/ACTUACIONS_URGENTS_online_PRO_AMB_FASE_VIEW/FeatureServer/0
```

- **Organització ArcGIS Online**: `interiorGencat.maps.arcgis.com` (Generalitat d'Interior).
- **Item ID**: `53f3b1ede32543249ddb169551b68044` (propietat de l'usuari `mapasbase`).
- **`access: public`** — sense token per lectura. No hi ha API key.
- **Tipus**: View Service (vista de la taula operativa `ACTUACIONS_URGENTS` del SISCOM).
- **Última edició observada**: `dataLastEditDate` ~ temps real (s'actualitza cada pocs minuts durant la campanya forestal).

### Capacitats tècniques (extretes de `?f=json`)

- `supportedQueryFormats`: **JSON, geoJSON, PBF**
- `supportedExportFormats`: csv, shapefile, sqlite, geoPackage, filegdb, featureCollection, geojson, kml, excel, parquet
- `maxRecordCount`: **2000** per pàgina; `standardMaxRecordCount`: 16000
- `supportsPagination`: true → cal fer servir `resultOffset` per recórrer tot el dataset
- `supportsOrderBy`: true (camp `EditDate` indexat — ideal per sincronització incremental)
- `supportsStatistics`: true → `COUNT`, `SUM`, `AVG`, etc. sense baixar tot el dataset
- `supportsFullTextSearch`: true
- `geometryType`: `esriGeometryPoint`
- `spatialReference`: **EPSG:25831** (ETRS89 / UTM zone 31N). Admet `outSR` per demanar WGS84.
- `capabilities`: `Query` (només lectura — no es pot editar)

### Vista filtrada (definition query del propi servei)

```
TAL_COD_ALARMA1 = 'IV' AND ACT_NUM_VEH >= 0
```

Això vol dir que el FeatureServer **ja** retorna només els incendis de vegetació (alarma tipus `IV`). No cal filtrar-ho nosaltres.

### Schema (camps rellevants)

Extret del `fields[]` de la resposta JSON. Marcat amb ⭐ els que usarem.

| Camp | Tipus | Descripció | Ús |
| --- | --- | --- | --- |
| `ACT_NUM_ACTUACIO` ⭐ | String(9) | Número d'actuació (ID) | Clau primària de cada incendi |
| `ACT_IDE_ACTUACIO` | String(2) | Tipus (`AC`) | — |
| `ACT_DAT_ACTUACIO` ⭐ | Date | Timestamp de l'actuació | Original |
| `ACT_DAT_INICI` ⭐ | Date | Inici real | Inici de l'actuació |
| `ACT_DAT_FI` ⭐ | Date | Final | `null` mentre estigui obert |
| `ACT_DAT_ACTUAL` | Date | Última actualització | — |
| `TAL_COD_ALARMA1` | String(2) | `IV` (filtre fix) | Sempre `IV` aquí |
| `TAL_DESC_ALARMA1` | String(100) | "incendi vegetació" | — |
| `TAL_COD_ALARMA2` ⭐ | String(4) | `VF`/`VA`/`VU` | **Subtipus**: Forestal/Agrícola/Urbana |
| `TAL_DESC_ALARMA2` ⭐ | String(100) | Descripció subtipus | Etiqueta llegible |
| `ACT_SITUACIO` | String(1) | Codi d'estat operatiu intern (`A`/`I`/`N`/`P`) | Vegeu glossari més avall |
| `MUNICIPI_DPX` ⭐ | String(50) | Municipi (DPX) | Ubicació |
| `MUNICIPI_SIG` ⭐ | String(50) | Municipi (SIG) | Ubicació (més fiable) |
| `ACT_NUM_VEH` ⭐ | SmallInt | Nre. de vehicles assignats | Magnitud |
| `COM_FASE` ⭐⭐ | String(256) | **Fase**: Actiu/Estabilitzat/Controlat/Extingit | Estat d'evolució |
| `EditDate` ⭐ | Date (sistema) | Última edició | Per sync incremental |
| `CreationDate` | Date | Alta | — |
| `OBJECTID`, `ESRI_OID`, `GlobalID` | IDs | Identificadors estables | Dedup |

**Camps `null`**: `COM_FASE` pot ser `null`. **El renderer del webmap oficial dels Bombers tracta `null` com a "IV Actiu"** (confirmat inspeccionant el JSON del webmap `59dc70908b8d4ed6a1ba5ca90de4e65d`): la llegenda pública només té `Actiu` (null), `Estabilitzat`, `Controlat`, `Extingit`. Per tant mapegem `null → Actiu`, igual que el visor oficial.

**⚠️ Estructura de la taula — log d'snapshots, no 1 fila per incendi** (verificat 2026-07-02: 33 files / 28 actuacions distintes): una mateixa `ACT_NUM_ACTUACIO` pot tenir 2+ files amb `ACT_SITUACIO`/`ACT_NUM_VEH`/`COM_FASE` diferents (cadascuna amb el seu `ESRI_OID`/`GlobalID` i `DATA_ACT` propi). **Cal dedup**: agrupar per `ACT_NUM_ACTUACIO` i quedar-se la fila amb `DATA_ACT` màxim com a estat actual — si no, un mateix incendi es compta i es pinta dues vegades amb estats contradictoris.

### Glossari `ACT_SITUACIO` (investigat 2026-07-02)

**No existeix cap domini oficial**: `fields[].domain` és `null` al servei, el popup del webmap oficial amaga el camp (`visible: false`), i cap dataset de Dades Obertes ni documentació pública en publica el diccionari. Els significats següents són **inferits** per correlació de dades (snapshot de 33 files) — no confirmats per Bombers:

| Codi | Significat inferit | Confiança | Evidència |
| :---: | --- | --- | --- |
| `A` | **Activa** (dotacions treballant-hi) | Alta | 100% de files `A` tenen `ACT_NUM_VEH ≥ 1` (mitjana 5.25); tots els altres codis tenen 0 |
| `N` | **Nova** (acabada de crear, fase no avaluada) | Mitjana-alta | Sempre `COM_FASE = null` (6/6); en els casos traçables és l'snapshot més antic |
| `P` | **Pendent** (de tancament administratiu) | Mitjana | Mai `COM_FASE = null` (0/4); apareix després de `N` en el cas traçable |
| `I` | **Inactiva** (sense recursos assignats ara) — millor hipòtesi | Baixa-mitjana | 0 vehicles sempre; cobreix totes les fases; en el cas traçable apareix *després* d'`A` (desmobilització) — **contradiu** la hipòtesi inicial `I = inici` |

Notes:
- `ACT_DAT_FI` era `null` a les 33 files → la vista probablement només mostra actuacions obertes; pot existir un codi terminal (`F`?/`T`?) mai observat aquí.
- **Implicacions per a la integració**: (1) fer servir `COM_FASE` com a estat de cara a l'usuari (és el camp que Bombers publica); (2) fer servir `ACT_NUM_VEH > 0` com a senyal de "s'hi està treballant ara" (separació perfecta al snapshot); (3) exposar `ACT_SITUACIO` com a codi cru sense traduir (`situacio: "A"`).

### Classificació de fase (taxonomia Bombers)

Catalunya fa servir **4 fases pròpies**, que ordenades de més a menys crítiques són:

1. **Actiu** — el foc crema i avança; atenció prioritària.
2. **Estabilitzat** — ja no avança però no està controlat.
3. **Controlat** — els Bombers el dominen, queda treball d'extinció.
4. **Extingit** — finalitzat.

Això **no** és un estàndard internacional (CAP/EDXL/INSPIRE), és la taxonomia operacional del cos de Bombers de la Generalitat, definida al **Pla INFOCAT**. Internament prové del SISCOM (camp `ACT_NIVELL_SISCOM` a la taula original, no exposit al view públic).

### Classificació de subtipus (`TAL_COD_ALARMA2`)

| Codi | Descripció | Categoria |
| --- | --- | --- |
| `VF` | Incendi vegetació forestal | Forestal |
| `VA` | Incendi vegetació agrícola | Agrícola |
| `VU` | Incendi vegetació urbana | Urbana |

Per defecte voldrem només `VF`, però exposarem el filtre.

### Exemple de query GeoJSON

```
GET https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/
        ACTUACIONS_URGENTS_online_PRO_AMB_FASE_VIEW/FeatureServer/0/query
    ?where=1%3D1
    &outFields=ACT_NUM_ACTUACIO,ACT_DAT_INICI,COM_FASE,TAL_COD_ALARMA2,MUNICIPI_SIG,ACT_NUM_VEH
    &outSR=4326
    &f=geojson
    &resultRecordCount=5
```

Resposta verificada (camp compactat):

```json
{
  "type": "FeatureCollection",
  "properties": { "exceededTransferLimit": true },
  "features": [{
    "type": "Feature",
    "id": 367295,
    "geometry": { "type": "Point", "coordinates": [2.16657, 41.72388] },
    "properties": {
      "ACT_NUM_ACTUACIO": "262311630",
      "ACT_DAT_ACTUACIO": 1782300143000,
      "TAL_COD_ALARMA1": "IV",
      "TAL_DESC_ALARMA1": "incendi vegetació",
      "TAL_COD_ALARMA2": "VF",
      "TAL_DESC_ALARMA2": "Incendi vegetació forestal",
      "ACT_SITUACIO": "I",
      "MUNICIPI_DPX": "Sant Quirze Safaja",
      "MUNICIPI_SIG": "Sant Quirze Safaja",
      "ACT_NUM_VEH": 0,
      "COM_FASE": "Estabilitzat"
    }
  }]
}
```

Notes:
- `exceededTransferLimit: true` → sempre cal paginació amb `resultOffset`.
- Timestamps en **ms des d'època UTC**.
- El visor públic aplica un filtre addicional `EditDate = Avui` per mostrar només els del dia; **el FeatureServer mateix no filtra per data**, per tant tenim **històric complet**.

### Estratègia de sincronització

> **⚠️ Drift verificat en implementar (2026-07-02)**: `EditDate` **no és consultable** al servei real — `editFieldsInfo` el declara però desapareix silenciosament d'`outFields=*` i usar-lo a `where`/`orderByFields` retorna HTTP 400. Nota addicional: el wrapper `properties.exceededTransferLimit` és absent del tot quan no s'excedeix el límit (no `false`).

> **⚠️ Finestra de retenció verificada en servei real (2026-07-02)**: la vista aplica una finestra de retenció d'uns **~4 dies sobre `DATA_ACT`**: una fila la `DATA_ACT` de la qual envelleix més enllà d'aquesta finestra **desapareix completament de la vista**, sense passar per cap estat de tancament previ (el `DATA_ACT` mínim observat a la vista és ~3.85 dies, mentre que hi ha incidents amb data d'inici de fa 8+ dies — les seves files antigues ja no hi són). **Conseqüència pràctica**: un sync incremental (`where=DATA_ACT > <última lectura>`) mai pot observar una baixa — un `act_num` que deixa d'aparèixer simplement es perd de la finestra incremental per sempre, sense cap senyal de "resolt". A més, `ACT_DAT_FI` és `null` al 100% de les files observades (inservible per detectar tancament); el tancament s'expressa únicament amb `COM_FASE = 'Extingit'` (estat terminal). Els literals `TIMESTAMP '...'` i els epochs numèrics del servei són **UTC**, confirmat empíricament.
>
> **Decisió (2026-07-02)**: donat que el dataset és petit (desenes de files, sempre una sola pàgina ≤ 2000), cada cicle fa un **fetch complet** (`where=1=1`, `since=None`) i dedup (§2 més amunt), i el coordinador **reconcilia** tractant el resultat com l'estat actual complet: qualsevol `act_num` que estava sent seguit i no apareix en el fetch s'ha esvaït de la vista i es poda (disparant `incendiscat_fire_resolved` si encara no s'havia disparat en passar a `Extingit`). Vegeu `custom_components/incendiscat/coordinator.py` (`_prune_vanished`) per la implementació.

Query per obtenir el dataset complet cada cicle:

```
?where=1=1
&outFields=*
&resultRecordCount=2000
&resultOffset=0
```

---

## 3. Pla Alfa (Agents Rurals) ✅ Confirmat estructurat

### Què és

El **Pla Alfa** és el dispositiu del cos d'Agents Rurals (Departament d'Interior) que gestiona les **restriccions i accessos** al medi natural segons el nivell de perill d'incendi diari. Defineix zones amb limitacions de circulació, permisos, etc.

Visor públic: <https://experience.arcgis.com/experience/2cf7ebbe492f401db826cb21eae9bfae>

Item AGO: `2cf7ebbe492f401db826cb21eae9bfae` (owner `AdminInterior`, mateixa organització que el visor dels Bombers).
Darrera edició observada: avui mateix.

Actualització: **00:00 i 9:30h** (o puntualment si cal). Telèfon d'incidències: 935 617 000.

### FeatureServers públics confirmats

S'han extret del `data?f=json` del visor. **Sis FeatureServers** públics, tots al mateix host `services7.arcgis.com/ZCqVt1fRXwwK6GF4/`:

| Servei | Què conté | Capa |
| --- | --- | --- |
| `Pla_Alfa_Municipal_Avui_FL_2_view` | Perill Pla Alfa per municipi — **avui** | `/0` |
| `Pla_Alfa_Comarcal_Avui_FL_VW` | Perill Pla Alfa per comarca — **avui** | `/1` |
| `tancaments_pla_alfa_avui_VW` | Tancaments de carreteres/vies — **avui** | `/2` |
| `pla_alfa_municipal_dema_FL_VW` | Perill Pla Alfa per municipi — **demà** | `/5` |
| `Pla_Alfa_Comarcal_Dema_FL_VW` | Perill Pla Alfa per comarca — **demà** | `/4` |
| `tancaments_pla_alfa_dema_VW` | Tancaments — **demà** | `/2` |

Capacitats (igual que el FeatureServer dels Bombers): `Query`, GeoJSON/JSON/PBF, paginació 2000, suporta estadístiques, EPSG:25831 nadiu però admet `outSR=4326`.

### Schema `Pla_Alfa_Municipal_Avui_FL_2_view` (el més útil)

Geometria: **polígon municipal** WGS84.

| Camp | Tipus | Descripció |
| --- | --- | --- |
| `FID` | OID | Identificador |
| `CODIMUNI` ⭐ | String(6) | Codi municipi Idescat (6 xifres) |
| `NOMMUNI` ⭐ | String(60) | Nom municipi |
| `CODICOMAR` | String(2) | Codi comarca |
| `NOMCOMAR` ⭐ | String(20) | Nom comarca |
| `PERIL_M` ⭐⭐ | Int | **Nivell de perill Pla Alfa (0–4)** |
| `Shape__Area`, `Shape__Length` | Double | Sistema |

### Schema `Pla_Alfa_Comarcal_Avui_FL_VW`

Geometria: polígon comarcal.

| Camp | Tipus | Descripció |
| --- | --- | --- |
| `FID` | OID | — |
| `CODICOMAR`, `NOMCOMAR` ⭐ | String | Comarca |
| `PERILL` ⭐⭐ | Int | Nivell de perill (0–4) |
| `DATA` ⭐ | Date | Data de vigència del mapa |
| `HORA` ⭐ | String(5) | Hora de vigència (HH:MM) |

> Aquesta és la millor font per verificar "estem mirant dades d'avui a les 9:30h o d'ahir?".

### Escala `PERIL_M` / `PERILL` (extreta del renderer)

| Valor | Color | Nivell |
| :---: | --- | --- |
| 0 | blanc | Sense risc |
| 1 | groc | Baix |
| 2 | taronja | Moderat |
| 3 | vermell | Alt |
| 4 | vermell fosc | Extrem |

**Aquesta escala substitueix directament la RCM 1–5 d'IPMA que fa servir Pyrovigil.** Té 5 nivells (0–4) i ens dona el perill **del municipi concret de l'usuari**, no pas una estimació regional com el GIF d'Agricultura.

### Exemple de query (perill del municipi de l'usuari)

```
GET .../Pla_Alfa_Municipal_Avui_FL_2_view/FeatureServer/0/query
    ?where=CODIMUNI='080193'              ← Barcelona
    &outFields=NOMMUNI,PERIL_M,NOMCOMAR
    &f=geojson
```

Resposta verificada avui: Barcelona, PERIL_M=0 (no estem en campanya activa encara).

### Estratègia d'ús a `ha-incendiscat`

1. Lookup del `CODIMUNI` de `zone.home` fent un `ST_Contains` del polígon sobre lat/lon de l'usuari.
2. Caching diari: el Pla Alfa s'actualitza a 00:00 i 9:30h → no cal polling freqüent.
3. Exposar `sensor.incendiscat_fire_risk` = `PERIL_M` del municipi + `binary_sensor.incendiscat_high_risk` quan `≥ 3`.
4. Opcional: predir demà amb el servei `_Dema_`.

---

## 4. Mapa de perill d'incendi (Departament d'Agricultura) ❌ No estructurat

### Què és

Mapa raster diari amb el nivell de perill d'incendi calculat a partir de meteorologia, humitat de combustibles, etc. Elaborat pel **Servei d'Anàlisi de Perill d'Incendis Forestals** (SAPIF) del Dept. d'Agricultura, Ramaderia, Pesca i Alimentació.

Pàgina: <http://agricultura.gencat.cat/ca/ambits/medi-natural/incendis-forestals/mapes/mapa-perill-incendi/>

### Estat d'explotabilitat

❌ **Confirmat no estructurat** (recerca exhaustiva feta via subagent + verificació directa). L'única sortida pública:

- **GIF raster**: `http://www.gencat.cat/medinatural/incendis/mapes/mapweb.gif` (avui), `mapweb2.gif` (demà), `mapweb_muni.gif` (municipal).
- **Sense world file** descarregable (`mapweb.gfw`/`.pgw`/`.gifw` retornen 404) — la georeferència només és al JS Leaflet de la pàgina.
- **Sense cap FeatureServer/MapServer/WMS** públic pel perill diari (verificat).
- PDFs: `Informe_Diari_SAPIF.pdf`, `Informe_Setmanal_SAPIF.pdf`, `Informe_Episodi_SAPIF.pdf` — no estructurats.

### Items ArcGIS Online relacionats (cap és la font diària)

| Item ID | Títol | Ús real |
| --- | --- | --- |
| `b1116a5559e24a638a4c2efe67b5c327` | perill_incendi (UAB lablet) | Estàtic acadèmic — **no** diari |
| `ca068311b1c3482ba4185b0d7a2498c0` | PERILL_BASIC_INCENDIS_2024_VECTOR | Perill bàsic estàtic 2024 |
| 3 Web Experiences (`Estat vegetació`, `Drought Code`, `mostreig`) del compte `raul.rodrigo_agriculturacat` | Components d'entrada (humitat, drought code, VIIRS) | No són el resultat de perill diari |

### Decisió

**No consumirem aquesta font**. El **Pla Alfa (`PERIL_M` 0–4)** és estructurat, oficial, municipal i s'actualitza diàriament — cobreix la mateixa necessitat amb millor granularitat. La font SAPIF d'Agricultura queda fora d'abast de la integració.

Cal recordar: la font que el Pla Alfa *agrupa* és aquesta d'Agricultura, però la sortida pública operativa pel ciutadà és el Pla Alfa.

---

## 5. INFOCAT (Pla especial de Protecció Civil)

Pla especial d'emergències per incendis forestals de Catalunya. Defineix fases d'activació (0, 1, 2, 3) segons la situació operacional.

Pàgina: <https://interior.gencat.cat/ca/arees_dactuacio/proteccio_civil/plans-proteccio-civil/plans-especials/infocat/index.html>

### Estat d'explotabilitat

⚠️ L'estat d'activació es publica a notes de premsa / Twitter (`@incendiscat`, `@gencat`). **No s'ha trobat endpoint estructurat públic** que doni "nivell INFOCAT actual". Avaluarem si val la pena incloure-ho o quedar-nos amb el perill raster + fases dels Bombers.

---

## 6. focs.cat (tercer)

Web de tercer: <https://focs.cat/mapa>

És una Single Page App que mostra incendis de Catalunya. Molt probablement consumeix el mateix FeatureServer dels Bombers que hem identificat — no aporta font pròpia addicional. Útil com a referència visual.

---

## 7. Estàndards (anàlisi)

La pregunta original era: *"quin estàndard fan servir els bombers i interior?"*

### Tecnològic

- **ArcGIS REST API** / **ArcGIS Online** (Esri). És el backend de tots dos visors principals.
- Compatible a nivell de format amb **OGC** (GeoJSON sortint), però **no és un servei OGC estàndard** (no WFS/WMS).
- No hi ha cap API GraphQL/REST documentada ni cap schema JSON Schema/OpenAPI publicat.

### Operacional

La classificació operacional **no** segueix cap estàndard internacional conegut:

- **No** fan servir **CAP** (Common Alerting Protocol, OASIS).
- **No** fan servir **EDXL** (Emergency Data Exchange Language).
- **No** publiquen com a **INSPIRE** (Annex III — zones de gestió de risc).
- Els codis `IV`/`VF`/`VA`/`VU` i les fases `Actiu/Estabilitzat/Controlat/Extingit` són **taxonomia interna del cos de Bombers de la Generalitat**, alineada amb el **SISCOM** (sistema de comandament del CECAT/112) i definida al **Pla INFOCAT**.

### Implicació per a la integració

Com que no hi ha estàndard intercanviable, l'esforç de mapeig és específic de Catalunya. Per sort el patró ArcGIS és genèric i ens el podem trobar documentat (cas Pyrovigil/Portugal amb ANEPC).

---

## 8. Dades obertes (portal Transparència) — confirmat

S'ha fet una recerca exhaustiva al portal Socrata `analisi.transparenciacatalunya.cat` (verificat via `api/views/<id>.json`):

### ✅ Existeixen: 3 datasets històrics d'incendis

Tots publicats pel **Departament d'Acció Climàtica / Agricultura** (NO pels Bombers — són el registre administratiu post-mèrit validat per Boscos).

| ID | Nom | Files | Període | `rowsUpdatedAt` |
| --- | --- | ---: | --- | --- |
| [`bks7-dkfd`](https://analisi.transparenciacatalunya.cat/d/bks7-dkfd) | Incendis forestals a Catalunya. Anys 2011-2024 | 7.545 | 2011-01-11 → 2024-12-31 | 2025-12-17 |
| [`crs7-idxi`](https://analisi.transparenciacatalunya.cat/d/crs7-idxi) | Incendis forestals per comarques. Any anterior (2025 provisional) | 495 | 2025-01-11 → 2025-12-23 | 2026-06-18 |
| [`9r29-e8ha`](https://analisi.transparenciacatalunya.cat/d/9r29-e8ha) | Incendis forestals per comarques. Any en curs (2026) | 344 | 2026-01-01 → 2026-06-28 | **2026-07-02** (avui) |

### Schema (comú als tres)

```
DATA INCENDI       date        només data (sense hora)
CODI COMARCA       text(2)     codi Idescat
COMARCA            text
CODI MUNICIPI      text(6)     codi Idescat
TERMEMUNIC         text        nom municipi
HAARBRADES         number      ha forestal arbrada cremada
HANOARBRAD         number      ha matoll/herbàcia/pastura
HANOFOREST         number      ha no forestal (urbana/agrícola)
HAFORESTAL         number      = HAARBRADES + HANOARBRAD
```

### Per què NO ens serveixen per a la integració

❌ **Sense coordenades** (lat/lon) — només codi municipi.
❌ **Sense hora** — només data.
❌ **Sense fase operacional** (Actiu/Estabilitzat/etc.).
❌ **Sense tipus IV/VF/VA/VU**.
❌ **Sense recursos desplegats**.
⚠️ **Retard**: s'actualitzen quan es tanca l'any o es valida la incidència — no és temps real. El dataset "any en curs" porta retard de 4 dies (última fila 28 juny, avui és 2 juliol).

### Quan sí els usarem

Són perfectes per a **analytics / històric**:
- "Quants focs hi ha hagut al meu municipi aquest estiu?"
- "Superfície cremada acumulada per comarca any rere any."
- Embed en una card d'estadística del dashboard.

`ha-incendiscat` els consumirà via la API Socrata (SoQL) per a una sensor opcional d'estadística històrica, **no** per a alertes temps real.

### ❌ No publicats com a dades obertes

- Llista d'incendis actius en temps real (és al FeatureServer dels Bombers).
- Pla Alfa (és al FeatureServer dels Agents Rurals).
- Perill d'incendi diari d'Agricultura (només GIF/PDF).

Tots tres són accessibles programàticament via els seus FeatureServers públics, però **no** estan curats com a datasets oficials al portal de Dades Obertes.

---

## 9. Resum de confiabilitat per font

| Font | Estructurada | Pública | Estable | Sense clau | Decisió |
| --- | :---: | :---: | :---: | :---: | --- |
| Bombers FeatureServer (live) | ✅ | ✅ | ⚠️ no oficial | ✅ | **BASE** |
| Pla Alfa Municipal (avui) | ✅ polígons | ✅ | ⚠️ no oficial | ✅ | **RISC** |
| Pla Alfa Comarcal (avui) | ✅ polígons | ✅ | ⚠️ no oficial | ✅ | RISC backup + data/hora |
| Pla Alfa Municipal/Comarcal (demà) | ✅ | ✅ | ⚠️ no oficial | ✅ | Predictiu (opcional) |
| Tancaments Pla Alfa (avui/demà) | ✅ | ✅ | ⚠️ no oficial | ✅ | Mapa addicional |
| Perill d'incendi SAPIF (Agricultura) | ❌ GIF | ✅ | ✅ | ✅ | **Descartat** (el Pla Alfa cobreix la necessitat) |
| Dades Obertes — incendis històrics | ✅ Socrata | ✅ | ✅ oficial | ✅ | **Històric** (no temps real) |
| INFOCAT (nivell activació) | ❌ notes | ✅ | ✅ | ✅ | Opcional, scrap |
| focs.cat | ✅ (tercer) | ✅ | ⚠️ | ✅ | Referència |

### Endpoints públics definitius per la integració

```
# Bombers (incidencias live, punts)
BASE = https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services
BOMBERS_LIVE = {BASE}/ACTUACIONS_URGENTS_online_PRO_AMB_FASE_VIEW/FeatureServer/0

# Pla Alfa (perill per municipi/comarca, polígons, avui i demà)
PLA_ALFA_MUNI_AVUI = {BASE}/Pla_Alfa_Municipal_Avui_FL_2_view/FeatureServer/0
PLA_ALFA_COM_AVUI  = {BASE}/Pla_Alfa_Comarcal_Avui_FL_VW/FeatureServer/1
PLA_ALFA_MUNI_DEMA = {BASE}/pla_alfa_municipal_dema_FL_VW/FeatureServer/5
PLA_ALFA_COM_DEMA  = {BASE}/Pla_Alfa_Comarcal_Dema_FL_VW/FeatureServer/4
PLA_ALFA_TANC_AVUI = {BASE}/tancaments_pla_alfa_avui_VW/FeatureServer/2
PLA_ALFA_TANC_DEMA = {BASE}/tancaments_pla_alfa_dema_VW/FeatureServer/2

# Dades obertes (històric validat, sense coords)
DO_HISTORIC_2011_2024 = https://analisi.transparenciacatalunya.cat/resource/bks7-dkfd.json
DO_HISTORIC_ANTERIOR  = https://analisi.transparenciacatalunya.cat/resource/crs7-idxi.json
DO_HISTORIC_ACTUAL    = https://analisi.transparenciacatalunya.cat/resource/9r29-e8ha.json
```

Tots els FeatureServer són a la mateixa organització AGO (`AdminInterior`/`mapasbase`) → coherent i mantinguda pel mateix equip.
