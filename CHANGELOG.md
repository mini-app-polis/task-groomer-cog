## [1.4.1](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.4.0...v1.4.1) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.22.0 ([0ae94dd](https://github.com/mini-app-polis/task-groomer-cog/commit/0ae94dda2b2dd5f6d180f72e5afb21aa10ad6052))

# [1.4.0](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.3.2...v1.4.0) (2026-10-23)


### Bug Fixes

* **deps:** require miniapppolis-common-utils 6.21.0 for 25-child proposals ([b278440](https://github.com/mini-app-polis/task-groomer-cog/commit/b278440996aac2f357d8f11cdd9cee56dc92b096))


### Features

* **agent:** default the reply limit to 16384 tokens so a 25-child split fits ([931ed7d](https://github.com/mini-app-polis/task-groomer-cog/commit/931ed7d424692f3ea146e6a1a11f44bb3a47556d))

## [1.3.2](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.3.1...v1.3.2) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.20.0 ([ed8d40d](https://github.com/mini-app-polis/task-groomer-cog/commit/ed8d40d70690233c5073d77564e418575bba79ed))

## [1.3.1](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.3.0...v1.3.1) (2026-10-23)


### Bug Fixes

* **proposal:** send executor only when the groomer names one ([fa82acf](https://github.com/mini-app-polis/task-groomer-cog/commit/fa82acf2a9b0443bce262250cd344a0ce2dd23a5))

# [1.3.0](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.2.4...v1.3.0) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.18.0 ([ba3dc6f](https://github.com/mini-app-polis/task-groomer-cog/commit/ba3dc6f19e8210a8f0574619e83d5bdf7b8bef1d))
* **deps:** bump miniapppolis-common-utils to 6.19.0 ([b843432](https://github.com/mini-app-polis/task-groomer-cog/commit/b843432915c8760f15a4b313a673731ccaa39550))
* **estimate:** price the proposal on the last round a capped run gets ([e797684](https://github.com/mini-app-polis/task-groomer-cog/commit/e797684e67c2ae51b32683f9437f6639b6c87231))


### Features

* **prompt:** let grooming name a date blocker and propose who does a task ([a2bf940](https://github.com/mini-app-polis/task-groomer-cog/commit/a2bf940d915be2a8e38784ec9cf92aecf8f4bb2b))
* **prompt:** split a task into child tasks behind GROOMER_SPLIT ([8e559ce](https://github.com/mini-app-polis/task-groomer-cog/commit/8e559ce127b10be17899dcc0d1b2a41d906d09b8))

## [1.2.4](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.2.3...v1.2.4) (2026-10-23)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.17.0 ([ee4127d](https://github.com/mini-app-polis/task-groomer-cog/commit/ee4127d71424dbc243472f8a31a915e5c9f54e41))

## [1.2.3](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.2.2...v1.2.3) (2026-10-22)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.16.2 ([32b77f6](https://github.com/mini-app-polis/task-groomer-cog/commit/32b77f6697fe80a4394e15ee4942fdbcb8e6e976))

## [1.2.2](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.2.1...v1.2.2) (2026-10-22)


### Bug Fixes

* **agent:** ask for the proposal instead of forcing tool_choice ([f4e8788](https://github.com/mini-app-polis/task-groomer-cog/commit/f4e878880b4f585b6ce15f58e9f923428dd7899a))

## [1.2.1](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.2.0...v1.2.1) (2026-10-22)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.16.1 ([20b853a](https://github.com/mini-app-polis/task-groomer-cog/commit/20b853ade28ebd6c53e90d5e5cf6884bdb025868))

# [1.2.0](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.1.2...v1.2.0) (2026-10-22)


### Features

* **prompt:** treat a task marked done as settled ([cac1914](https://github.com/mini-app-polis/task-groomer-cog/commit/cac191429925bb91321913e7c455abfd52c91139))

## [1.1.2](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.1.1...v1.1.2) (2026-10-22)


### Bug Fixes

* **cli:** log the estimate and the run, and document the public definitions ([13d0b6b](https://github.com/mini-app-polis/task-groomer-cog/commit/13d0b6b66bdae370b508aa8c8d92f40fe67acb23))
* **deps:** bump miniapppolis-common-utils to 6.16.0 ([e825980](https://github.com/mini-app-polis/task-groomer-cog/commit/e825980fb1438e105f32e0fa22058850007a9140))
* **repos:** try GitHub and the registry again on a transient failure ([d006a8b](https://github.com/mini-app-polis/task-groomer-cog/commit/d006a8b8f1e121a0b58887e08eae918df2be8cf6))

## [1.1.1](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.1.0...v1.1.1) (2026-10-21)


### Bug Fixes

* **deps:** bump miniapppolis-common-utils to 6.15.1 ([0e53f8d](https://github.com/mini-app-polis/task-groomer-cog/commit/0e53f8d3f53d28d1e237695e7b74b83f405689c3))

# [1.1.0](https://github.com/mini-app-polis/task-groomer-cog/compare/v1.0.0...v1.1.0) (2026-10-21)


### Features

* **groom:** read the owner's answers to its questions ([75281eb](https://github.com/mini-app-polis/task-groomer-cog/commit/75281eb660e5596a476afc10b10aa2630631bde7))

# 1.0.0 (2026-10-21)


### Features

* groom tasks by reading them and the code they name, and propose what they should say ([7ffd0e7](https://github.com/mini-app-polis/task-groomer-cog/commit/7ffd0e72ef494d5b93df95a3ce4700f5965c887a))
