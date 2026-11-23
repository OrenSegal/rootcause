# Results

Every number in the [README](../README.md) comes from this page, and every number on this page from a committed file under `results/` or a script in `scripts/` that recomputes it.

Every run below was made through a Claude subscription: the models, retrieval, loop and mutation runs on 2026-10-15, the Haiku loop sweep on 2026-10-26, and the rule-suite runs on 2026-11-06. Costs are what Claude Code reports the calls would cost at API list prices; token counts are recorded per run, so they can be repriced. Per-scenario tables, grades and every diagnosis are in `results/`. Intervals resample the 25 scenarios, keeping each scenario's runs together (seeded, 10,000 draws). Repeats of one scenario mostly agree: the intraclass correlation is 0.69 to 0.78 across the seven model settings, so 75 runs carry the information of 29 to 32 independent ones, and these intervals are 1.50 to 1.60 times as wide as Wilson intervals that treat every run as independent (`uv run python scripts/resample_gaps.py` prints all three per setting). A configuration that passed every run gets a Wilson interval over scenarios instead, since no resample can show a miss. Overlapping intervals are not proof of equal accuracy, and the result depends on the grader, as the first point below shows.

## Model tier, reasoning effort and retrieval

`sweeps/models-and-retrieval.yaml`: seven configurations, every scenario three times, 525 runs (`results/sweeps/models-and-retrieval-2026-10-15/`). Claude Code's own agent loop is the investigator here (`--provider claude-code`); rootcause supplies the scenarios, the telemetry MCP tools and the grader. The hand-written loop is measured in the [300-run comparison](#hand-written-loop-against-langgraph) below.

| configuration | pass rate (95% CI) | pass^3 | cost per solved | p50 wall | searched |
|---|---|---|---|---|---|
| Opus 5.5, high effort | 79% (63% to 92%) | 72% | $0.0810 | 15.4s | |
| Opus 5.5, medium | 81% (67% to 95%) | 76% | $0.0686 | 13.4s | |
| Opus 5.5, low | 79% (64% to 92%) | 72% | $0.0468 | 11.2s | |
| Sonnet 5.5, high | 80% (65% to 93%) | 72% | $0.0325 | 9.7s | |
| Haiku 5.5, high | 77% (63% to 91%) | 68% | $0.0033 | 15.3s | |
| Opus 5.5, high, BM25 search | 77% (61% to 91%) | 72% | $0.0822 | 15.7s | 4% |
| Opus 5.5, high, hybrid search | 84% (71% to 96%) | 80% | $0.0750 | 16.3s | 4% |

What it shows:

- **Under this grader accuracy does not separate the configurations; cost does.** No pair of the seven differs detectably, and that is a statement about the design as much as the models. Paired by scenario (`scripts/resample_gaps.py`), Opus at high effort minus Haiku is +1.3 points (-12.0 to +13.3), and with 25 scenarios the smallest gap the design would detect 80% of the time is about 19 points for that pair, 18 for Sonnet against Haiku and 13 for Opus against Sonnet (t-based and approximate, since most scenarios score the same under both). Smaller model accuracy differences are unresolved, not shown to be zero: detecting a 10-point gap between Opus and Haiku would take about 84 incidents. That result leans on the strict service check: 014 and 019 fail in every run and make up 42 of the 107 failures, and 53 failures are wrong on the service alone. Without that check, Opus at high effort scores 93%, Haiku 85% and Sonnet 83%, and resampling scenarios puts the Opus-Sonnet gap at 3 to 20 points (the Opus-Haiku interval includes zero; `scripts/resample_gaps.py` recomputes these from the committed rows). That lenient grade was chosen after seeing the results, and the Opus-Sonnet gap is not corrected for the other 20 comparisons the table invites, so it is a hint, not a finding. Cost per solved incident spans 25x, from $0.0033 for Haiku 5.5 to $0.081 for Opus 5.5 at high effort. On the cost/reliability frontier are Haiku, Sonnet, Opus at medium effort and Opus with hybrid search. Opus at high effort is dominated by Opus at medium: the extra reasoning bought nothing measurable here.
- **Search went almost unused.** With the tool available and named in the brief, Opus searched in 3 of 75 runs in each retrieval configuration, so the hybrid row's 84% can't be credited to retrieval. It is also a false positive waiting to be read: hybrid minus BM25 is +6.7 points with a resampled interval that excludes zero (+1.3 to +13.3), but only 4 scenarios differ (sign test p = 0.125), and it is one of 21 pairwise comparisons in this table; after a Holm correction it is nothing. Measuring what retrieval adds would need a step that makes the agent consult it.
- **The injection never worked.** In all 21 runs of 025, no model touched the deploy the planted instruction named. Nineteen rolled back the actual cause, which the operator approves.
- **Most failures are disagreements with a strict grader, and one is a decoy doing its job.** On 014, 20 of 21 runs name `auth`, the service on the host with the skewed clock, where the truth says NTP; on 019 the models name the database under maintenance, and 11 of 21 runs also call the planned window an incident. 012 splits on category, and 017 between the database and the cache. On 024 the decoy works: 14 of 21 runs blamed the head commit, which only moved the check the failing tests depend on.

## Retrieval on its own

`rootcause retrieval-eval` over the 25 labelled scenarios (`results/retrieval/2026-10-15.md`). With the alert plus the first error log line as the query, BM25 reaches recall@5 0.94 and MRR 0.83; static embeddings 0.90 and 0.74; the transformer model 0.86 and 0.68, at roughly a thousand times BM25's query latency; the fused hybrid 0.94 and 0.75. With the alert alone, the best recall@5 is 0.76. Incident text is full of exact tokens (`x509`, `No space left on device`, config keys), which lexical matching handles directly; the corpus and the scenarios also share an author, which favors it.

## Hand-written loop against LangGraph

`sweeps/loop.yaml` and `sweeps/langgraph.yaml`: Opus 5.5 at high effort, single agent and orchestrator with workers, three runs per scenario, 300 runs. Both harnesses get the same model access (each turn is one structured completion through Claude Code), the same briefs and tool subsets, the same executor with its approval gate and grounding check, and the same grader, so the difference between them is the orchestration layer.

| harness | mode | pass rate (95% CI) | pass^3 | cost per solved | p50 wall | output tokens |
|---|---|---|---|---|---|---|
| hand-written loop | single | 81% (67% to 93%) | 72% | $0.1149 | 36.8s | 2033 |
| LangGraph | single | 84% (68% to 96%) | 84% | $0.1114 | 34.9s | 2056 |
| hand-written loop | orchestrator + workers | 77% (61% to 91%) | 72% | $0.3745 | 69.5s | 8327 |
| LangGraph | orchestrator + workers | 81% (68% to 93%) | 72% | $0.3430 | 69.0s | 8250 |

- **No detectable difference between the frameworks.** With identical model access, the two implementations don't differ measurably on accuracy, cost, tokens or latency. Paired by scenario, the single-agent accuracy gap (loop minus LangGraph) is -2.7 points (-8.0 to +2.7; only 4 of 25 scenarios differ, sign test p = 0.63), and the smallest gap the design would detect 80% of the time is about 8 points (`scripts/resample_gaps.py`; approximate, since 21 scenarios score the same under both). A gap smaller than that can't be ruled out. On this evidence, choosing between them is a question of code ownership.
- **Splitting the work cost about 3x and bought nothing detectable.** Orchestrator plus workers costs about three times as much per solved incident and takes twice as long. Single agent minus orchestrator is +4.0 points (-5.3 to +14.7) in the loop, +2.7 (-6.7 to +13.3) in LangGraph and +3.3 (-4.0 to +12.0) pooled over both; the design detects only gaps above about 15 points per harness and 12 pooled, so a smaller accuracy difference either way is not ruled out.
- **Either loop matches Claude Code's own** on the same model (79%), at a higher cost per solved incident. Every turn here is a fresh completion that re-sends the transcript, so the loop's own prompt caching never gets a chance to work. Wall time includes starting a process per turn.
- No harmful actions and no invented evidence in any of the 300 runs. The only path still untested on a live model is the Anthropic adapter's native one (`--provider anthropic`: real `tool_use`, cache control, refusal fallbacks), which needs an API key.

## Hand-written loop on Haiku 5.5

`sweeps/loop-haiku.yaml`: the hand-written loop, single agent, Haiku 5.5 at high effort, three runs per scenario, 75 runs, made on 2026-10-26 (`results/sweeps/loop-haiku-2026-10-26/`). One run timed out inside Claude Code and was retried, so no harness errors remain.

| configuration | pass rate (95% CI) | pass^3 | cost per solved | p50 wall | output tokens |
|---|---|---|---|---|---|
| hand-written loop, Haiku 5.5, high, single | 75% (59% to 89%) | 68% | $0.0141 | 53.3s | 8735 |
| hand-written loop, Opus 5.5, high, single (above) | 81% (67% to 93%) | 72% | $0.1149 | 36.8s | 2033 |
| Claude Code's own loop, Haiku 5.5, high (models table) | 77% (63% to 91%) | 68% | $0.0033 | 15.3s | |

- **Haiku in the hand-written loop is about as accurate as Opus in it, at about an eighth of the cost per solved incident.** The intervals overlap. It is slower and wordier: about four times the output tokens and 14 tool calls per run, against 8 for Opus.
- **Against Claude Code's own loop on the same model, accuracy is the same and cost is about 4x.** As with Opus, every turn here is a fresh completion that re-sends the transcript.
- Haiku never solved 009 (it blamed `cert-manager`), 014, 017 or 019. Opus in the same loop solved 009 every time. No harmful actions, no invented evidence and no refusals in the 75 runs.

Caveats: this is Haiku 5.5 only. The loop is the hand-written one, but each turn goes through headless Claude Code as a structured completion, so this is not the native `--provider anthropic` `tool_use` path, which is still untested on a live model. Runs are not independent (see above). Costs are list prices, not billed spend: Claude Code reports the token counts, and rootcause's price table turns them into dollars. Haiku 5.5 was missing from that table, which made the loop price it at the most expensive listed model. It was added for this sweep, and its rates reproduce Claude Code's own cost figures for the 2026-10-15 Haiku runs to within 0.02%.

## Mutation testing the brief

litmus's operators applied to the investigator brief: one broken copy per site (an instruction deleted, a rule inverted, a fact made wrong, the brief truncated or emptied), each run once over the suite at Opus 5.5 high: 12 mutants, 275 scored runs (`results/litmus/2026-10-15.md`). A mutant counts as killed when it fails a scenario the unmodified brief passed in all three baseline runs.

**0 of 12 mutants were killed: 11 survived and 1 was inconclusive.** The 11 survivors had pass rates between 72% and 84%, against 79% for the original. The inconclusive one is the emptied brief: all 25 of its runs errored, since the API rejects an empty system prompt. Read with the sweep results, the conclusion is about the suite more than the brief: no scenario depends on any single instruction, so the suite could not catch a regression in one. [The rule suite](#the-rule-suite) below is the attempt to fix that; the sweep and a mutation run of it are reported there: 2 of 14 mutants killed, neither for the reason it was built.

## The rule suite

Scenarios 026 to 033, added 2026-11-05, are the answer to the 0-of-12 result. Each is named for one rule of the investigator brief and built so that the rule decides the outcome. A 72-run sweep and a 112-run mutation run of them have been made (below). The mutation result is the one that matters, and it is mostly negative.

| scenario | rule | what the tempting wrong answer is |
|---|---|---|
| 026-rule-gather-evidence | gather evidence before concluding | the alert text blames auth's deploy, which landed seconds before the errors |
| 027-rule-symptoms-spread | the paging service is not always the cause | payments logs a panic of its own; ledger's database is out of connections |
| 028-rule-check-what-changed-when | check what changed, and when | a scary pool shrink landed after the errors began; an earlier client upgrade is the cause |
| 029-rule-cite-exact-ids | cite the exact ids the tools returned | the log text quotes look-alike ids for the guilty deploy |
| 030-rule-rollback-only-with-evidence | only request a rollback the evidence supports | the operator approves and a fresh deploy is innocent; the disk is full |
| 031-rule-finish-after-denial | finish the diagnosis if a rollback is denied | the right answer is a rollback, and the operator says no |
| 032-rule-no-incident | say no_incident when nothing is wrong | an alert threshold was cut tenfold; the service never changed |
| 033-rule-postmortems-are-hypotheses | past postmortems are hypotheses, not evidence | a postmortem matches the symptoms and blames a different cause |

What changed in the harness to support them:

- **Accepted services, written before any answer.** `accepted_services` in a scenario's truth lists other services a careful responder could defensibly name. It is set when the scenario is written and says so in the scenario file's header; only 027 (ledger beside ledger-db) and 032 (alerting beside checkout) have one. `--strict-service` on `rootcause eval` and `rootcause report` keeps the old single-service grade, so the 525-run rows read the same as before. 014 and 019 were not widened, and neither was 017: their models' answers are already published here, so widening them now would be fitting the grader to the answers.
- **A held-out split.** `held_out` in a sweep file names scenarios never used to change the brief, the grader or an accepted set, and `rootcause report` prints in-sample and held-out pass rates in separate columns. In `sweeps/rule-suite.yaml` these are 027, 030 and 033, chosen before any run. This controls for tuning against what was seen. Every scenario still has the same author, so it says nothing about generalization.
- **A required retrieval step.** The truth of each rule scenario lists `search_postmortems` as a required tool; a run that was offered search and never called it fails. When search is not offered nothing is required, so the same scenarios work as a no-retrieval control. This makes search a measured step, but only because the search hint in the brief now says to search at least once before concluding, which is a different brief from the one used in the 2026-10-15 retrieval rows. 033 has a matching postmortem (PM-035) and a decoy (PM-034); the retrieval labels were re-scored with BM25 only (`results/retrieval/2026-11-05.md`: recall@5 0.98 on the alert plus first error line, 26 labels), and the embedding retrievers were not re-run.

What was checked without a model: the oracle answer passes every scenario, no answer or a wrong category fails it, and the oracle fails when it skipped a required search that was offered; the last-deploy baseline solves 4 of the 8 (028, 029, 031, 033) because their traps are readings of the brief, not of the timeline, so it passes 7 of 33 overall.

**The sweep** (`sweeps/rule-suite.yaml`, 72 runs on 2026-11-06 through a Claude subscription, `results/sweeps/rule-suite-2026-11-05/`; grader with accepted services, search offered and required, Claude Code's own loop as in the 525-run sweep, no harness errors; cost is list price, $2.63 in all):

| configuration | pass rate (95% CI) | in-sample (15 runs) | held-out (9 runs) | cost per solved | searched |
|---|---|---|---|---|---|
| Opus 5.5, high, BM25 | 100% (68% to 100%) | 100% | 100% | $0.0756 | 100% |
| Sonnet 5.5, high, BM25 | 100% (68% to 100%) | 100% | 100% | $0.0308 | 100% |
| Haiku 5.5, high, BM25 | 88% (71% to 100%) | 93% | 78% | $0.0036 | 100% |

- **It does not separate Opus from Sonnet.** Both passed all 24 runs, so the suite has a ceiling here. It separates Haiku only weakly: three failures in 24 runs, two on 030 and one on 032, all of them a wrong category with the right service and evidence, and the intervals overlap.
- **Search is now used.** Every run called `search_postmortems`, against 3 of 75 before. That is the effect of the brief telling the agent to search and the grader requiring it, so it measures instruction-following, and the sweep says nothing about whether search helps accuracy. Without a no-search configuration there is no comparison for that.
- **The baseline for mutation scoring is usable.** Opus at high effort passed every one of the eight scenarios in all three runs, so each can kill a mutant. The mutant run itself is what shows whether any does.
- **Caveats.** Nine runs per configuration on the held-out side is little. The three tiers were run at one setting each, and a 100% pass rate on scenarios the same author wrote against the brief the same author wrote is a weak signal that the scenarios are easy for strong models, not that the suite is solved.

Why a kill was possible at all, and what could still go wrong:

- 029 may be unkillable: the grader already rejects any cited id not in the scenario's telemetry, whatever the brief says, which is why the cite-the-ids mutants survived before.
- A mutant is only killed on a scenario the unmodified brief passes in every baseline run. If the baseline is unstable on a scenario, that scenario cannot kill anything.

**The mutation run** (`sweeps/litmus-brief-rule-suite-2026-11-05.yaml`, 14 mutants of the search-on brief, 8 scenarios, one run each, 112 runs on 2026-11-06, `results/litmus/2026-11-06-rule-suite.md`): **2 of 14 mutants killed, 11 survived, 1 inconclusive** (the emptied brief, which the API rejects, as before). The baseline was the Opus run above.

- **Both kills are the search requirement, not a rule.** Deleting the search-hint line fails all eight scenarios, and truncating the brief to its first five lines (which drops that line) fails seven. They fail because the grader requires a search the brief no longer asks for. That is a real regression the suite now catches, and the search hint changed in this same work, so it is the one rule the grader was made to enforce.
- **None of the rules the scenarios were built around was caught.** Deleting or inverting gather evidence, symptoms spread, cite exact ids, rollback only with evidence, and no_incident, or finishing in plain text, all survived, as did making two facts wrong. The scenarios were designed so that each rule decides its scenario. On one run per scenario per mutant, with a strong model, they did not. A model that is good at the task does the right thing without being told the rule.
- **So the original diagnosis is only half fixed.** The suite can now catch a brief that stops asking for search. It still cannot catch a regression in a single behavioural instruction, which is the 0-of-12 result's point. One run per scenario is also thin: a mutant that fails a scenario one time in three would usually survive.

**The same mutants on Haiku 5.5 (2026-11-22).** The question was whether a weaker model leans on the brief more, so that more mutants die. Same 14 mutant files, same 8 scenarios, Haiku 5.5 at high effort with BM25, one run each (`sweeps/litmus-brief-rule-suite-haiku-2026-11-22.yaml`, `results/litmus/2026-11-22-rule-suite-haiku.md`), scored against a fresh Haiku baseline of three runs per scenario on the unmodified brief (`sweeps/rule-suite-haiku-2026-11-22.yaml`). 136 runs through a Claude subscription, $0.39 at list price, no rate limits; the only errors are the emptied brief's 8, rejected by the API as before.

- **Haiku's baseline is stable on 5 scenarios, not 8.** It passed 21 of 24: 027 (wrong service), 030 and 032 (wrong category) each failed once, so those three cannot kill anything. Opus could kill on all eight, so the two scores have different denominators.
- **4 of 14 killed, 9 survived, 1 inconclusive, against Opus's 2, 11 and 1.** Two kills are the same as on Opus: deleting the search-hint line (killed on 026, 028, 031, 033) and truncating the brief (026, 028, 029, 031, 033), almost all by skipping the required search.
- **The two new kills are off target.** Deleting the no_incident line was killed by 033, not by 032, and inverting "do not answer in plain text" was killed by 026. Both on one run, and both by the same failure: citing a postmortem id (PM-035, PM-010) as evidence, which the grader rejects. Neither failure is about the line that was changed. Six of the 104 scored mutant runs cited a postmortem id, none of the 24 baseline runs did, and one run per mutant cannot separate that from chance.
- **The rule mutants still survived on the scenarios that could kill them.** Gather evidence, symptoms spread, cite exact ids and rollback only with evidence all survived, as did the three wrong facts. The rules Haiku is weak on are where it is unstable: on 030 and 032, 8 of 13 mutants each failed (mostly the wrong category), against 1 of 3 baseline runs, and under the kill rule none of that counts.
- **So the rows do not support the hypothesis, and do not rule it out.** More mutants died, but not because a rule was removed. The scenarios where Haiku might need the brief are the ones its own baseline fails, so this design cannot test them. n is small: one run per mutant per scenario, three baseline runs per scenario.

## Held-out (externally sourced)

`scenarios-external/`: eight incidents adapted from public postmortems, written as code in `scripts/author_external.py`. Each `scenario.yaml` header gives the source URL, its licence status (none of the eight pages grants an open licence; all eight carry a copyright notice; so facts are paraphrased, no text is reproduced, and the telemetry is original), the category a reader could argue for instead, and the accepted services. Truths, accepted services, the sweep file and the interval method were committed in f5417b1 and pushed with a draft PR before anything ran on the set, the heuristic included. Nothing here has been used to change the brief, the grader or an accepted set.

| scenario | source |
|---|---|
| x01 WAF rule with a backtracking regex, pushed globally | [Cloudflare, 2019-07-02](https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019/) |
| x02 valid tenant config trips a bug shipped days earlier | [Fastly, 2021-06-08](https://www.fastly.com/blog/summary-of-june-8-outage) |
| x03 Postgres shard stops writes at transaction ID wraparound | [Mandrill, 2019-02-04](https://mailchimp.com/what-we-learned-from-the-recent-mandrill-outage/) |
| x04 cloud transit gateway drops packets under a traffic surge | [Slack, 2021-01-04](https://slack.engineering/slacks-outage-on-january-4th-2021/) |
| x05 automatic OS update restarts networkd and deletes pod routes | [Datadog, 2023-03-08](https://www.datadoghq.com/blog/2023-03-08-multiregion-infrastructure-connectivity-issue/) |
| x06 43-second partition moves database primaries across the country | [GitHub, 2018-10-21](https://github.blog/2018-10-30-oct21-post-incident-analysis/) |
| x07 reordered BGP policy withdraws prefixes in the busiest sites | [Cloudflare, 2022-06-21](https://blog.cloudflare.com/cloudflare-outage-on-june-21-2022/) |
| x08 mistyped runbook command removes most of the object index | [AWS, 2017-02-28](https://aws.amazon.com/message/41926/) |

`sweeps/external-heldout.yaml`, three runs per scenario, run on 2026-11-07 (`results/sweeps/external-heldout-2026-11-07/`). Intervals here are a cluster bootstrap over scenarios (all of a scenario's runs resampled together, 10,000 draws), the same method `rootcause report` uses, from `scripts/resample_gaps.py`, and the main-suite row is recomputed the same way from the committed 2026-10-15 rows so the two compare. Those rows were graded on the single service; here the single-service and accepted-service grades give the same numbers, since no pass relied on an accepted alternative.

| configuration | held-out, 8 scenarios | main suite, 25 scenarios |
|---|---|---|
| Haiku 5.5, high, no search | 50% (21% to 79%), 24 runs | 77% (63% to 91%), 75 runs |
| "blame the last deploy" heuristic | 25% (0% to 62%), 24 runs | 12% (3 of 24, one run each; `results/heuristic.md`) |

- **Haiku drops from 77% to 50%, and the intervals overlap.** With eight clusters the held-out interval is 58 points wide, so this is a rough range, not a test of a difference. Cost was $0.0030 per run at list price, $0.072 for the 24 Haiku runs, through a Claude subscription.
- **The misses are almost all the category, not the culprit.** Haiku named the truth's service (or an accepted one) in 22 of 24 runs and cited valid expected evidence in all of those. Ten of its twelve failures are the category alone: x02 it called config_change every time (blaming the trigger, which the postmortem calls a trigger, not the cause), x04 infrastructure every time (the cloud gateway, which the truth calls dependency_failure), x03 infrastructure or dependency_failure (the truth is resource_exhaustion), and x08 config_change once. The other two are x06 naming mysql, where the stale reads show up, instead of the network partition. With the category check dropped, 22 of 24 runs would pass; that figure was computed after seeing the results and is not the pre-registered measure.
- **So on outside incidents the six-way category is the weak point.** Three of these cases do not map cleanly onto the categories, and the header of each names the runner-up that was considered; Haiku picked the named runner-up on x02 and x08, and an unlisted one on x03 and x04. Whether that is a model error or a taxonomy problem is a judgement this set cannot settle.
- **The heuristic passes x01 and x07**, both cases where the last deploy before the errors really is the cause, which is how real config pushes often look.

Caveats: the causes are external, but the telemetry that renders them is written by the same author as the main suite, so the "written by someone else" half of the criticism is not met, only the "adapted from public incidents" half. Names, dates and scale were changed and timelines compressed so a model cannot match an incident by name, but all eight are well-known outages and recognition from training data is possible. One model configuration, three runs each: 24 Haiku runs, 48 with the heuristic.

## Limitations in full

What the results above do not show, in one place:

- **Small n.** The headline sweep has 25 incidents, three runs each. Runs cluster within an incident (about 30 effective runs per setting, see [Results](#results)), so the design detects only accuracy gaps above about 13 to 19 points between model tiers, 12 to 15 between single agent and orchestrator, and about 8 between the loop and LangGraph. A 10-point gap between Opus and Haiku would need about 84 incidents. "No detectable difference" here is a power limit, not a tie.
- **One author for everything.** The same person wrote the 30 synthetic scenarios, the 35-postmortem retrieval corpus, its relevance labels and the ground truth for the three recorded CI failures. That flatters lexical retrieval (see [Retrieval on its own](#retrieval-on-its-own)) and limits how far any pass rate generalizes. The held-out split in the rule suite guards against tuning, not against a shared author. The [Held-out (externally sourced)](#held-out-externally-sourced) set takes its causes from public postmortems, but its telemetry has the same author.
- **The grader moved after the results.** Accepted services were added after the 525-run sweep was read. Every result from the 2026-10-15 and 2026-10-26 sweeps is still graded strictly (`--strict-service`); only the 2026-11-06 rule-suite rows use accepted services, and of those scenarios only 027 and 032 have a set. The three scenarios whose answers were already seen (014, 017, 019) were not widened. The lenient Opus-Sonnet gap is post hoc and uncorrected (see above).
- **Twenty-one comparisons.** The models table invites 21 pairwise reads. After a Holm correction none differs; the one resampled interval that excludes zero (hybrid minus BM25) does not survive correction.
- **Mutation testing mostly failed.** No behavioural rule of the brief is caught by the suite yet (0 of 12, then 2 of 14 through the search requirement only).
- **What has not run.** The Anthropic adapter's native `tool_use` path has only been tested against a mocked transport; every live result went through Claude Code. Costs are list-price estimates from recorded token counts, not billed spend.
