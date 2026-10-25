# task-groomer-cog

[![CI](https://github.com/mini-app-polis/task-groomer-cog/actions/workflows/ci.yml/badge.svg)](https://github.com/mini-app-polis/task-groomer-cog/actions/workflows/ci.yml)
[![Version](https://img.shields.io/github/v/tag/mini-app-polis/task-groomer-cog?label=version)](https://github.com/mini-app-polis/task-groomer-cog/releases)

Grooms tasks on the board. Every task is meant to be worked by an agent that
has only the task and the repositories it names, so a task is ready when an
agent could finish it without asking. This cog reads a task that is not
ready, looks at the code it is about, and proposes what the task should
say. It never writes to the board: task-manager-cog applies the proposal.
See [ADR-0001](docs/decisions/ADR-0001-propose-never-write.md).

## Flow

```
task-manager-cog sweep ──POST /v1/tasks/groomings/jobs──► api-kaianolevine-com
                                                                │
                                                    task-groomer queue
                                                                ▼
                                                   task-groomer-cog (Lambda)
                                                     1. check the month's spend
                                                     2. the model reads the task,
                                                        then the code, with
                                                        read-only tools
                                                     3. it calls propose, once
                                                     4. record the run's cost
                                                                │
           POST /v1/tasks/groomings/proposals ◄─────────────────┘
                                │
                   task-manager queue ──► task-manager-cog applies it
```

The model is given the task, the board's definition of ready, every
repository in the ecosystem registry with its description, the names
of the other open tasks, and any finished task this one mentions, marked
`(done)`. Its tools list, read and search files in the
fleet's repositories, read from GitHub zipballs on `dev` (or `main` for a
repository without one), in memory. It ends with one `propose` call: the
title, Done when, Where, Constraints, Effort, Blocked by, a body, and
questions for anything it could not settle from the task and the code.

**Blocked by** may name dates as well as tasks: a task that can only be
done after a date (a scheduled run, an event) is given the date
(`YYYY-MM-DD`, a UTC day) rather than a question about how to express the
wait. task-manager-cog holds it out of Ready until that day.

**Who does a task.** A task's executor is a tag on it, named in the
shared contract (`mini_app_polis.api.contract.GroomingExecutor`):

- no tag: the agent, a headless run that has only the task and the
  repositories it names;
- `cowork`: a working session with the owner (his machine, the board,
  Railway, Discord, Doppler, consoles, his decisions);
- `person`: the owner alone (consoles behind his login, accounts,
  physical checks).

A task already tagged is groomed for that executor, and the groomer does
not ask who does the work. For an untagged task that needs what the agent
cannot reach, it proposes `cowork` or `person` by what the task must
reach, and asks only when it cannot tell; task-manager-cog applies the
proposed executor as a tag. A proposal never changes a tag the task
already has.

**Splitting.** With `GROOMER_SPLIT=true`, a task that is neither a leaf
(one repository, one executor) nor a compound task (done when its
children are) is split. The proposal carries its children, each a leaf
with its own Done when, Where, Constraints, Effort, body and executor,
ordered so a child that waits on an earlier one names it by position
(a shared library before its consumer). The task itself keeps only its
title and body: task-manager-cog creates the children and rewrites it as
their compound task. The number of children is capped by the shared
contract (`GROOMING_MAX_CHILDREN`); a split over the cap, or with a child
that does not hold, is dropped whole and the task is proposed without it.
Splitting runs are paid like any other and count against the monthly cap.
The by-hand run (`--task` without `--dry-run`) lists the children it would
propose and its cost, and writes nothing; `--dry-run` cannot know the
children, but with splitting on prices the long run for a split's larger
proposal. Off by default, so no split is proposed before task-manager-cog
can apply one.

## Caps

| Cap | Setting | Default |
|---|---|---|
| Model | `GROOMER_MODEL` | `claude-sonnet-5-5` |
| Tool rounds per run | `GROOMER_MAX_ROUNDS` | 8; the last asks for the proposal, and a run without one fails |
| Input tokens per run | `GROOMER_MAX_INPUT_TOKENS` | 150000; past 80% each round asks for the proposal |
| Output tokens per reply | `GROOMER_MAX_OUTPUT_TOKENS` | 16384 |
| Spend per calendar month (UTC) | `GROOMER_MONTHLY_CAP_USD` | 20; checked before every request |
| Splitting | `GROOMER_SPLIT` | off; `true` lets a run split a task into children |

The fixed part of the prompt and the tools are cached, so every round after
the first reads them at the cache price. A run's cost is recorded with the
API (`POST /v1/tasks/groomings/runs`) whatever the outcome, and the month's
total (`GET /v1/tasks/groomings/spend`) is what the cap is checked
against. A model missing from `pricing.py` is refused: it could not be
capped.

There are no automatic retries. The queue delivers each job once, so a run
is never paid for twice; a run that fails is reported and the task is
groomed again when it next changes.

## Layout

```
src/task_groomer_cog/
  worker.py     Lambda entrypoint: one job, one paid run, one proposal
  agent.py      the run: the request loop, the tools and every cap
  prompt.py     what the model is told
  proposal.py   the propose call, checked, as the contract's proposal
  repos.py      read-only access to the fleet's repositories
  registry.py   the fleet's repositories, from ecosystem.yaml
  pricing.py    what a run costs
  __main__.py   estimate and run by hand
```

## Running locally (DOC-013)

**Prerequisites:** Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), and the
[Doppler CLI](https://docs.doppler.com/docs/install-cli). Secrets come from
Doppler's shared `dev` config; nothing reads a `.env` file. `.env.example`
lists the names read.

```bash
git clone git@github.com:mini-app-polis/task-groomer-cog.git
cd task-groomer-cog
doppler setup                              # reads doppler.yaml: mini-app-polis-ecosystem / dev
uv sync --all-extras
uv run pre-commit install
uv run pre-commit run --all-files
```

**Estimate first.** These read the board and count each task's first
request with Anthropic's free token-counting endpoint. Nothing is spent:

```bash
doppler run -- uv run python -m task_groomer_cog --dry-run --task 1219712805103580
doppler run -- uv run python -m task_groomer_cog --dry-run --board
```

**Groom one task for real.** This is a paid run. It prints the proposal,
the tools the model used and the cost, and writes nothing anywhere:

```bash
doppler run -- uv run python -m task_groomer_cog --task 1219712805103580
```

## Running tests

```bash
uv run pytest                              # with coverage, as CI runs it
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
```

## Deployment

A release on `main` deploys the Lambda through the shared `lambda-deploy`
workflow. The function, queue, dead-letter queue and alarms are declared in
`mini-app-polis/infra`, which also supplies the repository variables
`AWS_DEPLOY_ROLE_ARN`, `AWS_REGION` and `AWS_FUNCTION_NAME`.
