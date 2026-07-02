# Changelog

## [0.3.0](https://github.com/pmontp19/ha-incendiscat/compare/ha-incendiscat-v0.2.0...ha-incendiscat-v0.3.0) (2026-07-02)


### ⚠ BREAKING CHANGES

* domain changes from `bomberscat` to `incendiscat`; entity_ids and event names change accordingly.
* full-fetch reconciliation — prune incidents that vanish from the source view

### Features

* aggregated sensors and phase icons (T6+T12) ([de958fe](https://github.com/pmontp19/ha-incendiscat/commit/de958fe9a996803a45cccd046705dce01994f4cd))
* config flow step 1 (location + radii) (T4) ([1c7ec2a](https://github.com/pmontp19/ha-incendiscat/commit/1c7ec2a64fc422821c0aee676a0e2b4bf50277bc))
* config flow step 2, options flow and reconfigure (T11) ([3935b7b](https://github.com/pmontp19/ha-incendiscat/commit/3935b7b0e9c886d8763506dceab0ad47a3f9c9e0))
* data update coordinator with lifecycle and bus events (T5+T9) ([3d419fb](https://github.com/pmontp19/ha-incendiscat/commit/3d419fb5032021ff5fad962da9e4af860394a5f1))
* diagnostic entities, service degradation repair issue and diagnostics (T13) ([40d1efb](https://github.com/pmontp19/ha-incendiscat/commit/40d1efb3eae11bcfdf3f964a48faba2be0c9ee55))
* entity and flow translations (ca, es, en) (T14) ([37c42d4](https://github.com/pmontp19/ha-incendiscat/commit/37c42d4160fce377d4265f0e8026bf70c7015cc4))
* fire notification blueprint (T15) ([db8e33d](https://github.com/pmontp19/ha-incendiscat/commit/db8e33d1993e28eb1504855465095c166ba39aa8))
* fire_nearby binary sensor (T8) ([aa862e7](https://github.com/pmontp19/ha-incendiscat/commit/aa862e7319ea666f8c802596cf912ea55a4b5b8a))
* geo_location entities per tracked wildfire (T7) ([324ba19](https://github.com/pmontp19/ha-incendiscat/commit/324ba19bb80ef4641d9744e5b7147e584644cd9f))
* incident models, geo utils and ArcGIS client with dedup (T2+T3) ([f0e67e1](https://github.com/pmontp19/ha-incendiscat/commit/f0e67e10e2e1fb498f734dd16c9427e8f81f3fae))
* Pla Alfa risk client, fire_risk sensor and high_risk binary sensor (T10) ([fa4b4b3](https://github.com/pmontp19/ha-incendiscat/commit/fa4b4b377952fbc1deb38a3bd0fea029a364b921))
* register platforms with stub setup entries; sync docs with live-service drift ([33c2549](https://github.com/pmontp19/ha-incendiscat/commit/33c2549cfce9b00d6bed0bb684342cdbb12dc89e))
* reload entry on options update ([9cab1af](https://github.com/pmontp19/ha-incendiscat/commit/9cab1afd5c6919e523835119b7fed392bddbedcd))
* rename integration to Incendis Catalunya (domain: incendiscat) ([4ffce3c](https://github.com/pmontp19/ha-incendiscat/commit/4ffce3ceadda9f13506b74e2597f931b4cb88e11))
* scaffold integration skeleton, CI and dev environment (T1) ([b9468d0](https://github.com/pmontp19/ha-incendiscat/commit/b9468d0998142d14dbc9614e8bf8d26052236268))


### Bug Fixes

* **deps:** ignore pytest/pytest-cov bumps in dependabot ([1f9386d](https://github.com/pmontp19/ha-incendiscat/commit/1f9386d73113b3ec2b65857977b905f9035bde4d))
* full-fetch reconciliation — prune incidents that vanish from the source view ([0dd5391](https://github.com/pmontp19/ha-incendiscat/commit/0dd53912657762e214c9790e79275f6c3ac4ae32))
* harden ArcGIS clients and models per code review (QA wave) ([8182302](https://github.com/pmontp19/ha-incendiscat/commit/81823025aab2b972fb50afd5adbb4579c2396ec5))
* hassfest compliance — manifest key order and lowercase selector option keys ([2e21c48](https://github.com/pmontp19/ha-incendiscat/commit/2e21c489aedae8b6350b0d209a18c307b8cc8ed8))
* **release-please:** keep pre-1.0 semver until a real major bump is intended ([a191f58](https://github.com/pmontp19/ha-incendiscat/commit/a191f58ed6170149b3ddacc6e368c719aafc41b2))
* single reload on reconfigure, shared entity helper, geo_location task hygiene (QA wave) ([ec4d299](https://github.com/pmontp19/ha-incendiscat/commit/ec4d299ee91bec070a3bd3d1845fae2589a830a1))


### Documentation

* add CONTRIBUTING.md and AGENTS.md (CLAUDE.md symlink) ([b725b3a](https://github.com/pmontp19/ha-incendiscat/commit/b725b3a7b498b86546454a6d96632b64eb4820c0))
* user-facing README with install, entities, events, dashboard (T16) ([a76a031](https://github.com/pmontp19/ha-incendiscat/commit/a76a031ab41cc3bfabec3e8f9084b33c4af523ba))

## [0.2.0](https://github.com/pmontp19/ha-bomberscat/compare/ha-bomberscat-v0.1.0...ha-bomberscat-v0.2.0) (2026-07-02)


### ⚠ BREAKING CHANGES

* full-fetch reconciliation — prune incidents that vanish from the source view

### Features

* aggregated sensors and phase icons (T6+T12) ([de958fe](https://github.com/pmontp19/ha-bomberscat/commit/de958fe9a996803a45cccd046705dce01994f4cd))
* config flow step 1 (location + radii) (T4) ([1c7ec2a](https://github.com/pmontp19/ha-bomberscat/commit/1c7ec2a64fc422821c0aee676a0e2b4bf50277bc))
* config flow step 2, options flow and reconfigure (T11) ([3935b7b](https://github.com/pmontp19/ha-bomberscat/commit/3935b7b0e9c886d8763506dceab0ad47a3f9c9e0))
* data update coordinator with lifecycle and bus events (T5+T9) ([3d419fb](https://github.com/pmontp19/ha-bomberscat/commit/3d419fb5032021ff5fad962da9e4af860394a5f1))
* diagnostic entities, service degradation repair issue and diagnostics (T13) ([40d1efb](https://github.com/pmontp19/ha-bomberscat/commit/40d1efb3eae11bcfdf3f964a48faba2be0c9ee55))
* entity and flow translations (ca, es, en) (T14) ([37c42d4](https://github.com/pmontp19/ha-bomberscat/commit/37c42d4160fce377d4265f0e8026bf70c7015cc4))
* fire notification blueprint (T15) ([db8e33d](https://github.com/pmontp19/ha-bomberscat/commit/db8e33d1993e28eb1504855465095c166ba39aa8))
* fire_nearby binary sensor (T8) ([aa862e7](https://github.com/pmontp19/ha-bomberscat/commit/aa862e7319ea666f8c802596cf912ea55a4b5b8a))
* geo_location entities per tracked wildfire (T7) ([324ba19](https://github.com/pmontp19/ha-bomberscat/commit/324ba19bb80ef4641d9744e5b7147e584644cd9f))
* incident models, geo utils and ArcGIS client with dedup (T2+T3) ([f0e67e1](https://github.com/pmontp19/ha-bomberscat/commit/f0e67e10e2e1fb498f734dd16c9427e8f81f3fae))
* Pla Alfa risk client, fire_risk sensor and high_risk binary sensor (T10) ([fa4b4b3](https://github.com/pmontp19/ha-bomberscat/commit/fa4b4b377952fbc1deb38a3bd0fea029a364b921))
* register platforms with stub setup entries; sync docs with live-service drift ([33c2549](https://github.com/pmontp19/ha-bomberscat/commit/33c2549cfce9b00d6bed0bb684342cdbb12dc89e))
* reload entry on options update ([9cab1af](https://github.com/pmontp19/ha-bomberscat/commit/9cab1afd5c6919e523835119b7fed392bddbedcd))
* scaffold integration skeleton, CI and dev environment (T1) ([b9468d0](https://github.com/pmontp19/ha-bomberscat/commit/b9468d0998142d14dbc9614e8bf8d26052236268))


### Bug Fixes

* **deps:** ignore pytest/pytest-cov bumps in dependabot ([1f9386d](https://github.com/pmontp19/ha-bomberscat/commit/1f9386d73113b3ec2b65857977b905f9035bde4d))
* full-fetch reconciliation — prune incidents that vanish from the source view ([0dd5391](https://github.com/pmontp19/ha-bomberscat/commit/0dd53912657762e214c9790e79275f6c3ac4ae32))
* harden ArcGIS clients and models per code review (QA wave) ([8182302](https://github.com/pmontp19/ha-bomberscat/commit/81823025aab2b972fb50afd5adbb4579c2396ec5))
* hassfest compliance — manifest key order and lowercase selector option keys ([2e21c48](https://github.com/pmontp19/ha-bomberscat/commit/2e21c489aedae8b6350b0d209a18c307b8cc8ed8))
* **release-please:** keep pre-1.0 semver until a real major bump is intended ([a191f58](https://github.com/pmontp19/ha-bomberscat/commit/a191f58ed6170149b3ddacc6e368c719aafc41b2))
* single reload on reconfigure, shared entity helper, geo_location task hygiene (QA wave) ([ec4d299](https://github.com/pmontp19/ha-bomberscat/commit/ec4d299ee91bec070a3bd3d1845fae2589a830a1))


### Documentation

* add CONTRIBUTING.md and AGENTS.md (CLAUDE.md symlink) ([b725b3a](https://github.com/pmontp19/ha-bomberscat/commit/b725b3a7b498b86546454a6d96632b64eb4820c0))
* user-facing README with install, entities, events, dashboard (T16) ([a76a031](https://github.com/pmontp19/ha-bomberscat/commit/a76a031ab41cc3bfabec3e8f9084b33c4af523ba))
