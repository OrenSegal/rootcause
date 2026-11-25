### Sweep `ci-dev-brief-2026-11-23`: 2 configurations, 1 runs each (accepted-service grade)

| config | scored / errors | pass rate (95% CI) | pass^1 | pass@1 | mean cost | cost per solved | p50 / p90 wall | output tokens | tool calls | searched | frontier |
|---|---|---|---|---|---|---|---|---|---|---|---|
| haiku-5.5-high-cite | 51 / 0 | 94% (86% to 100%) | 94% | 94% | $0.0095 | $0.0101 | 15.1s / 21.5s | 2877 | 9.7 | 0% | yes |
| opus-5.5-high-cite | 51 / 0 | 98% (94% to 100%) | 98% | 98% | $0.2129 | $0.2172 | 11.2s / 15.6s | 1265 | 5.4 | 0% | yes |

| config | change (95% CI) | not_change (95% CI) | balanced | pooled |
|---|---|---|---|---|
| haiku-5.5-high-cite | 90% (76% to 100%), 29 runs | 100% (85% to 100%), 22 runs | 95% | 94% |
| opus-5.5-high-cite | 97% (90% to 100%), 29 runs | 100% (85% to 100%), 22 runs | 98% | 98% |

Balanced is the mean of the two class pass rates. Class-graded scenarios check the class and the commit, not the exact category or service.

| scenario | haiku-5.5-high-cite | opus-5.5-high-cite |
|---|---|---|
| ci-altair-35669720079 | 1/1 | 1/1 |
| ci-altair-35775942017 | 1/1 | 1/1 |
| ci-build-37202076420 | 1/1 | 1/1 |
| ci-build-38002299563 | 1/1 | 1/1 |
| ci-build-40533998006 | 1/1 | 0/1 |
| ci-build-44219314063 | 1/1 | 1/1 |
| ci-click-33496785933 | 1/1 | 1/1 |
| ci-click-33887414469 | 1/1 | 1/1 |
| ci-click-34144555748 | 1/1 | 1/1 |
| ci-geopandas-34034858084 | 1/1 | 1/1 |
| ci-geopandas-35534992784 | 1/1 | 1/1 |
| ci-geopandas-41035651082 | 1/1 | 1/1 |
| ci-ipython-39596261587 | 0/1 | 1/1 |
| ci-ipython-39596535293 | 1/1 | 1/1 |
| ci-ipython-39866796179 | 0/1 | 1/1 |
| ci-ipython-40254609405 | 1/1 | 1/1 |
| ci-ipython-41062158624 | 1/1 | 1/1 |
| ci-ipython-42229657590 | 1/1 | 1/1 |
| ci-loguru-36411461201 | 1/1 | 1/1 |
| ci-matplotlib-43908764069 | 1/1 | 1/1 |
| ci-matplotlib-43926514982 | 1/1 | 1/1 |
| ci-matplotlib-43938180211 | 1/1 | 1/1 |
| ci-matplotlib-44933573494 | 1/1 | 1/1 |
| ci-multidict-37421469197 | 1/1 | 1/1 |
| ci-multidict-37614237594 | 1/1 | 1/1 |
| ci-multidict-37647784094 | 1/1 | 1/1 |
| ci-multidict-37666235108 | 1/1 | 1/1 |
| ci-multidict-37738480921 | 1/1 | 1/1 |
| ci-multidict-37771426628 | 1/1 | 1/1 |
| ci-multidict-38127829739 | 1/1 | 1/1 |
| ci-panel-43759376300 | 1/1 | 1/1 |
| ci-panel-43954653640 | 1/1 | 1/1 |
| ci-panel-44339476406 | 1/1 | 1/1 |
| ci-panel-44356789031 | 1/1 | 1/1 |
| ci-panel-44383896934 | 0/1 | 1/1 |
| ci-panel-44397232490 | 1/1 | 1/1 |
| ci-pipx-44001139987 | 1/1 | 1/1 |
| ci-pipx-44661741143 | 1/1 | 1/1 |
| ci-poetry-core-33070303884 | 1/1 | 1/1 |
| ci-poetry-core-33894335120 | 1/1 | 1/1 |
| ci-pytest-42785732184 | 1/1 | 1/1 |
| ci-pytest-43162181446 | 1/1 | 1/1 |
| ci-pytest-xdist-40166502013 | 1/1 | 1/1 |
| ci-pytest-xdist-42131392843 | 1/1 | 1/1 |
| ci-pytest-xdist-43066656851 | 1/1 | 1/1 |
| ci-sentry-python-44335660000 | 1/1 | 1/1 |
| ci-sentry-python-44397296615 | 1/1 | 1/1 |
| ci-streamlink-33797245698 | 1/1 | 1/1 |
| ci-streamlink-37654916190 | 1/1 | 1/1 |
| ci-streamlink-39834644038 | 1/1 | 1/1 |
| ci-sympy-44524099951 | 1/1 | 1/1 |
