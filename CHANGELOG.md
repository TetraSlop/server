# Changelog

## [0.2.0](https://github.com/TetraSlop/server/compare/v0.1.0...v0.2.0) (2026-11-22)


### ⚠ BREAKING CHANGES

* **cli:** an operator who relied on these options being off must now pass --no-wagers, --no-card-prizes, --no-allow-cbc or --no-departures.
* releases no longer attach the wheel or SHA256SUMS; install the ghcr.io/tetraslop/server image instead.
* replace Discord sign-in with self-registered accounts over direct TCP
* run the client's rules in Python and drop the native bridge
* existing JSON saves can no longer be loaded or imported, and the server needs a PostgreSQL database.
* tmserver-admin action commands require --request-id, --operator and --reason; the -preview/-apply commands, --digest and the recovery command are gone.
* require native gameplay and remove duplicate Python rules

### Features

* **admin:** add --admin-bind for the panel listener ([d2443eb](https://github.com/TetraSlop/server/commit/d2443ebb9a37713e5cf974636362a882b0c5a65b))
* **auth:** add an opt-in loopback dev identity for local browser sign-in ([cef1fc4](https://github.com/TetraSlop/server/commit/cef1fc489bdfb978f1580c82c258a54dc97cf15d))
* **auth:** let browser clients open game and profile streams ([3b74485](https://github.com/TetraSlop/server/commit/3b74485f1be98a6757f22b9b197c46edf35e3adf))
* **cli:** turn wagers, card prizes, CBC and departures on by default ([642396f](https://github.com/TetraSlop/server/commit/642396feff7615d58df5dfce92466374e6d70347))
* **economy:** prune exactly-once journal rows at startup ([edd0d54](https://github.com/TetraSlop/server/commit/edd0d54dcbe1cc1d581b96d92d9904896f7b6b21))
* publish hosted server with audited administration and CI ([d92ef53](https://github.com/TetraSlop/server/commit/d92ef5319704cf411e8272fafb6e7890d0fae005))
* register accounts by username and assign their POL ID ([815b508](https://github.com/TetraSlop/server/commit/815b5085427e516f7a1b4777c3220d656333db50))
* replace Discord sign-in with self-registered accounts over direct TCP ([3335194](https://github.com/TetraSlop/server/commit/3335194590ae2cde1c072ca72b426c59a9888860))
* run the client's rules in Python and drop the native bridge ([280f7fc](https://github.com/TetraSlop/server/commit/280f7fcf0262d08c008285e74aa0d4d40c9ed223))
* ship the server as a container image ([c4ceda4](https://github.com/TetraSlop/server/commit/c4ceda4f730f699f8434116b0f870cf1d36d12a2))


### Bug Fixes

* **accounts:** keep assigned POL IDs below 2^31 ([ed4d08f](https://github.com/TetraSlop/server/commit/ed4d08f665644473a65d7a19ad0bfccc3803fd39))
* **cards:** compute sale value the way the client's shop and auction do ([99f07b6](https://github.com/TetraSlop/server/commit/99f07b60272a5adc11c32caea4c1e103301dd1e4))
* **ci:** run startup preflight coverage with native rules ([e38959a](https://github.com/TetraSlop/server/commit/e38959af8ba54526c9bdc9f84d6ed1d1b4d75784))
* **game:** drop disabled wagers instead of disconnecting ([b9df386](https://github.com/TetraSlop/server/commit/b9df386ad5c84422298b2f002f39372f50f0683a))
* **network:** keep hosted legacy passwords within polcore's 15 characters ([3e51588](https://github.com/TetraSlop/server/commit/3e515886a92e426392f3ecd8ae8255e47c04709d))


### Documentation

* describe signing in with a username in the README ([cb66d8e](https://github.com/TetraSlop/server/commit/cb66d8e48f8f2c1c795e65d60a5c142370835072))
* describe the PostgreSQL-only server without local play ([897d108](https://github.com/TetraSlop/server/commit/897d1086fa51c67b40923d3982ffbf964d90fe18))
* describe the three-operation store and storage.validation ([6813a45](https://github.com/TetraSlop/server/commit/6813a45c8df80aaa8981bf6f75dd8f7749d7023f))
* design wave 2b (checks, edit parser, envelope codec, staging, exceptions) ([6a21a1d](https://github.com/TetraSlop/server/commit/6a21a1d7e984f28194c0015ecebcb0a6188c8405))
* hand off native server and retail fidelity backlog ([6633574](https://github.com/TetraSlop/server/commit/6633574598963c8aa144349520d832f76f7d974f))
* point the storage row at storage/restore.py ([b7b7b5e](https://github.com/TetraSlop/server/commit/b7b7b5e464ee6932332393d212cdda6062dc81c4))
* remove agent logs and research notes now in TetraPackets ([89fb636](https://github.com/TetraSlop/server/commit/89fb63652e3c39a390b27de2683fa7ac92c8f9b0))
* rewrite the README for new readers ([a452420](https://github.com/TetraSlop/server/commit/a452420bfc1d3b73a3fa0a004672f467e9c263c2))
* update ranking notes for the removed history options ([2cb74d2](https://github.com/TetraSlop/server/commit/2cb74d2b5ca4939897fec4b6658c192eba61168e))


### Code Refactoring

* apply audited admin actions once per request ID ([29ae87f](https://github.com/TetraSlop/server/commit/29ae87f7b4f936be219188358da0028945e1649d))
* require native gameplay and remove duplicate Python rules ([e3192ab](https://github.com/TetraSlop/server/commit/e3192abfae4db7fd470c3c4c248f41d26b3508ea))
* save only to PostgreSQL and remove local play ([385d1cc](https://github.com/TetraSlop/server/commit/385d1cc598ba7b2ea993039deb7645a8db521183))
