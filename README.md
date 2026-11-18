# GTA VI launch-day Twitch forecaster

[![Bounty](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![Rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/adjiadjo/makefun-gta6-twitch-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![Reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/adjiadjo/makefun-gta6-twitch-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)

`main.py` uses the Python 3.13 standard library. Run `python3 main.py` with case files in `/input`, or set `MAKEFUN_INPUT_DIR` to a local directory. For each `*.in` file it writes a matching `.out` file to `/output` or `MAKEFUN_OUTPUT_DIR`. Each output contains five ordered viewer-count quantiles for each UTC hour from 2 through 24. The program uses no network and reads its data from the supplied case.

## Model

Each donor launch contributes its hourly log changes from the first UTC hour. Weights favor similar checkpoint counts, similar lead-up trends, and compatible release schedules. Trend features compare the anchor with medians from three pre-checkpoint windows and skip windows with inadequate data. The model excludes the target title's own category history from the donor pool. It aligns donor curves by UTC so that their observed trajectories can reflect regional unlocks and shifts in Twitch usage.

Light adjacent-hour smoothing reduces the influence of spikes in individual donor curves. A weighted Gaussian mixture represents uncertainty, with a wider kernel when the target exceeds the donor scale range. The final forecast keeps the observed hour-1 level as its anchor. To calibrate the distribution, the model predicts each donor's probability ranks using only the other donors. It pools those ranks across nearby hours and shrinks the estimated calibration toward the identity mapping.

## Validation

A local leave-one-title-out diagnostic held out each of the 37 eligible comparable launches, including during calibration. The four practice targets were excluded from model development. Loss uses the competition's mean log pinball formula and compares the same observed hours for both forecasts.

| Comparable validation set | Hours | Model loss | House baseline loss | Model 90% coverage | House 90% coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| All eligible titles | 851 | 0.413487 | 0.929650 | 91.0% | 62.4% |
| Closest schedule group (7 mixed launches) | 161 | 0.333328 | 0.451316 | 88.2% | 70.8% |

All four practice inputs produced byte-identical, contract-valid outputs across two runs each on Python 3.13.12. The comparable diagnostic measures historical fit under title exclusion. The final case may have a larger audience, a different regional mix, or a launch curve unlike the available donors. The sample has few close schedule matches. Quantiles are marginal by hour and carry no guarantee of coverage for GTA VI.
