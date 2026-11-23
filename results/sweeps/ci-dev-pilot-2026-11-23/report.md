### Sweep `ci-dev-pilot-2026-11-23`: 3 configurations, 1 runs each (accepted-service grade)

| config | scored / errors | pass rate (95% CI) | pass^1 | pass@1 | mean cost | cost per solved | p50 / p90 wall | output tokens | tool calls | searched | frontier |
|---|---|---|---|---|---|---|---|---|---|---|---|
| heuristic | 51 / 0 | 55% (41% to 69%) | 55% | 55% | $0.0000 | $0.0000 | 0.0s / 0.1s | 0 | 3.0 | 0% | yes |
| haiku-5.5-high | 51 / 0 | 73% (61% to 84%) | 73% | 73% | $0.0107 | $0.0147 | 19.7s / 28.3s | 3415 | 11.1 | 0% | yes |
| opus-5.5-high | 51 / 0 | 92% (84% to 98%) | 92% | 92% | $0.1978 | $0.2146 | 12.5s / 16.7s | 1331 | 5.6 | 0% | yes |

| config | change (95% CI) | not_change (95% CI) | balanced | pooled |
|---|---|---|---|---|
| heuristic | 97% (90% to 100%), 29 runs | 0% (0% to 15%), 22 runs | 48% | 55% |
| haiku-5.5-high | 69% (52% to 86%), 29 runs | 77% (59% to 91%), 22 runs | 73% | 73% |
| opus-5.5-high | 97% (90% to 100%), 29 runs | 86% (73% to 100%), 22 runs | 91% | 92% |

Balanced is the mean of the two class pass rates. Class-graded scenarios check the class and the commit, not the exact category or service.

| scenario | heuristic | haiku-5.5-high | opus-5.5-high |
|---|---|---|---|
| ci-altair-35669720079 | 0/1 | 1/1 | 1/1 |
| ci-altair-35775942017 | 0/1 | 1/1 | 1/1 |
| ci-build-37202076420 | 1/1 | 1/1 | 1/1 |
| ci-build-38002299563 | 1/1 | 1/1 | 1/1 |
| ci-build-40533998006 | 1/1 | 0/1 | 0/1 |
| ci-build-44219314063 | 1/1 | 1/1 | 1/1 |
| ci-click-33496785933 | 1/1 | 1/1 | 1/1 |
| ci-click-33887414469 | 1/1 | 1/1 | 1/1 |
| ci-click-34144555748 | 1/1 | 0/1 | 1/1 |
| ci-geopandas-34034858084 | 0/1 | 1/1 | 1/1 |
| ci-geopandas-35534992784 | 0/1 | 1/1 | 1/1 |
| ci-geopandas-41035651082 | 0/1 | 1/1 | 1/1 |
| ci-ipython-39596261587 | 1/1 | 0/1 | 1/1 |
| ci-ipython-39596535293 | 1/1 | 1/1 | 1/1 |
| ci-ipython-39866796179 | 1/1 | 1/1 | 1/1 |
| ci-ipython-40254609405 | 1/1 | 0/1 | 1/1 |
| ci-ipython-41062158624 | 1/1 | 1/1 | 1/1 |
| ci-ipython-42229657590 | 1/1 | 1/1 | 1/1 |
| ci-loguru-36411461201 | 0/1 | 1/1 | 1/1 |
| ci-matplotlib-43908764069 | 1/1 | 1/1 | 1/1 |
| ci-matplotlib-43926514982 | 1/1 | 1/1 | 1/1 |
| ci-matplotlib-43938180211 | 1/1 | 0/1 | 1/1 |
| ci-matplotlib-44933573494 | 1/1 | 0/1 | 1/1 |
| ci-multidict-37421469197 | 1/1 | 1/1 | 1/1 |
| ci-multidict-37614237594 | 1/1 | 1/1 | 1/1 |
| ci-multidict-37647784094 | 1/1 | 1/1 | 1/1 |
| ci-multidict-37666235108 | 1/1 | 0/1 | 1/1 |
| ci-multidict-37738480921 | 1/1 | 1/1 | 1/1 |
| ci-multidict-37771426628 | 1/1 | 1/1 | 1/1 |
| ci-multidict-38127829739 | 0/1 | 1/1 | 1/1 |
| ci-panel-43759376300 | 0/1 | 1/1 | 1/1 |
| ci-panel-43954653640 | 0/1 | 0/1 | 1/1 |
| ci-panel-44339476406 | 1/1 | 0/1 | 1/1 |
| ci-panel-44356789031 | 0/1 | 0/1 | 0/1 |
| ci-panel-44383896934 | 1/1 | 1/1 | 1/1 |
| ci-panel-44397232490 | 1/1 | 0/1 | 1/1 |
| ci-pipx-44001139987 | 1/1 | 1/1 | 1/1 |
| ci-pipx-44661741143 | 1/1 | 1/1 | 1/1 |
| ci-poetry-core-33070303884 | 0/1 | 1/1 | 1/1 |
| ci-poetry-core-33894335120 | 0/1 | 1/1 | 1/1 |
| ci-pytest-42785732184 | 0/1 | 1/1 | 1/1 |
| ci-pytest-43162181446 | 0/1 | 0/1 | 1/1 |
| ci-pytest-xdist-40166502013 | 0/1 | 1/1 | 1/1 |
| ci-pytest-xdist-42131392843 | 0/1 | 1/1 | 1/1 |
| ci-pytest-xdist-43066656851 | 0/1 | 1/1 | 1/1 |
| ci-sentry-python-44335660000 | 0/1 | 1/1 | 1/1 |
| ci-sentry-python-44397296615 | 0/1 | 1/1 | 1/1 |
| ci-streamlink-33797245698 | 0/1 | 1/1 | 1/1 |
| ci-streamlink-37654916190 | 0/1 | 0/1 | 0/1 |
| ci-streamlink-39834644038 | 0/1 | 0/1 | 0/1 |
| ci-sympy-44524099951 | 0/1 | 1/1 | 1/1 |
