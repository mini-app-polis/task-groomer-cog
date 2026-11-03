# ADR-0001: Propose, never write

**Date:** 2026-10-25
**Status:** Accepted
**Repo:** task-groomer-cog

---

## Context

Every task on the board is meant to be worked by an agent with only the task
and the repositories it names. Most tasks start short of that: a voice note's
one-line idea, a detected failure with a templated title, a task written in
a hurry. Making them ready is judgment work, done by a model reading the task
and the code, and it costs money every time.

task-manager-cog is the one writer to the board (task-manager-cog ADR-0001),
and it makes no model calls, so its hourly sweep stays free. The judgment
has to happen somewhere else.

## Decision

This cog does the judgment and nothing else.

- **It proposes and never writes.** A run ends with one structured proposal,
  handed back through the API to task-manager-cog's queue, which applies it.
  The deployed worker holds no Asana credential, so it cannot write to the
  board even by mistake.
- **It reads the code, read-only.** The model's only tools list, read and
  search files in the fleet's repositories, fetched as GitHub zipballs and
  held in memory. Only repositories in the ecosystem registry can be opened.
- **It asks rather than guesses.** A part of a task that cannot be settled
  from the task and the code is left out of the proposal and asked about
  instead.
- **Every run is capped and recorded.** Rounds, tokens and a monthly spend
  cap are enforced in the cog's own request loop, and each run's cost is
  recorded with the API, whatever the outcome.
- **A failed run is never retried automatically.** A redelivered job would
  pay twice, so the queue delivers each job once and the task is groomed
  again when it next changes. The one exception costs nothing: a run stopped
  by the monthly cap before any model request is sent again once there is
  budget.

## Consequences

- task-manager-cog stays free to run, and the only paid step in grooming is
  here, where it can be capped and measured on its own.
- A proposal can arrive for a task that has changed since. It carries the
  fingerprint of the content it was made for, and task-manager-cog does not
  apply one that no longer matches.
- Grooming a task by hand (`python -m task_groomer_cog`) reads the board with
  an Asana token, but only reads; nothing is written to the board or
  recorded with the API.
- Quality is measured on the board: how often a task this process moved to
  Ready is moved back (task-manager-cog's grooming records).
