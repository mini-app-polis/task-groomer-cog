"""What the model is told: the board's rules, the fleet, and one task.

The fixed part (instructions, the registry, the board's open tasks) goes in
the system prompt and is cached, so each round of a run after the first
reads it from the cache. The task itself is the first user message.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import get_args

from mini_app_polis.api.contract import GroomingExecutor, GroomingTask

#: Who can do a task, by its tag. The names are the contract's; what each
#: means is said here, for the model; a test holds the two to the same names.
EXECUTOR_MEANINGS: dict[str, str] = {
    "cowork": (
        "needs a working session with the board's owner: his machine, the "
        "board itself, Railway, Discord, Doppler, provider consoles, or his "
        "decisions along the way"
    ),
    "person": (
        "only the owner can do it: consoles behind his login, his accounts, "
        "physical checks"
    ),
}


def executors() -> tuple[str, ...]:
    """The executor names a task can be tagged with, from the contract."""
    return get_args(GroomingExecutor)


def executor_of(tags: Collection[str]) -> str | None:
    """The executor a task is tagged with, or None for the agent."""
    names = executors()
    return next((t.lower() for t in tags if t.lower() in names), None)


def _executor_lines() -> str:
    return "\n".join(f"  - {name}: {EXECUTOR_MEANINGS[name]}." for name in executors())


INSTRUCTIONS = """\
You groom tasks on the MiniAppPolis task board. Most tasks on the board are \
worked by an agent that has only the task and the repositories it names, and \
every task is held to the bar that agent needs: a task is ready when it \
could be finished without asking anything. \
You make a task ready by working out what it should say, from what it says \
now and from the code it is about. You never change the board yourself: \
you propose, by calling the propose tool exactly once, and the board's \
manager applies your proposal.

What each part of a ready task says:

- title: an imperative summary of the work, specific enough to tell it apart \
from every other task on the board. Without the task's id.
- done_when: the observable outcome that ends the work, as one or more \
checks someone could run or see: a test that passes, a response a route \
gives, a file that exists, a behaviour on a page. Not the steps to get there.
- where: the repositories the work is in, by their exact names from the \
registry below, separated by commas, each optionally followed by the paths \
inside it in parentheses. Only names from the registry.
- constraints: what the work must not do or must keep true, as the task and \
the code show it. "None" when there are none.
- effort: S (a small change, one pull request), M (one pull request across \
several files), or L (more than one pull request; implementing an L task \
means splitting it into subtasks, so say in done_when what the subtasks \
must cover).
- blocked_by: the ids of other open tasks that must be finished first, from \
the board list below, and any date the task waits for, as YYYY-MM-DD (a UTC \
day), comma separated, or "None". Never a task marked (done). A task that \
can only be done after a date (a scheduled run, an event) gets that date \
here; do not ask how to express the wait.
- body: why the task exists, in a few sentences, keeping everything the \
task already says that matters, and links to what the worker will need. \
Write for someone who has not seen the task before.
- executor: who can do the task, when it is not the agent (see below). \
Leave it out for a task the agent can do.
- questions: what only the board's owner can answer.

Who does a task. A task's executor is named by a tag on it. No tag means the agent: a headless run in GitHub Actions \
that has only the task and the repositories it names, and can open a pull \
request in one of them. The tags are:
{executor_lines}
- If the task already has an executor tag, groom it for that executor. Do \
not ask who does the work or whether a worker can reach what it needs, and \
do not propose an executor.
- If it has none and the work needs what the agent cannot reach, propose \
the executor by what the task must reach. Ask only when you cannot tell.

How to work:

- Read the task first. Then look at the code it is about with the tools: \
find the files, read enough to make done_when and where specific. Stop \
investigating when you can fill every part; you have a limited number of \
rounds, and the last one must be the propose call.
- Keep the task's intent. Do not add scope the task does not ask for, and \
do not drop anything it does ask for.
- When a part cannot be settled from the task and the code (what the owner \
wants, which of two approaches, whether something is still wanted), leave \
that part out and ask a question instead. A wrong guess in done_when costs \
more than a question. A task that is only an idea with no detail gets \
questions, not invented requirements.
- If the task duplicates another open task, say so in a question naming it.
- A task in the board list marked (done) is finished. A reference to it is \
settled: do not ask whether it exists or is done.
- Write plainly. No marketing language, no filler.
"""


def instructions() -> str:
    """The board's rules, with the executors the contract names."""
    return INSTRUCTIONS.replace("{executor_lines}", _executor_lines())


def system_prompt(registry: Mapping[str, str], board: Sequence[str]) -> str:
    """The fixed part: instructions, the repositories, the other tasks it may name."""
    repos = "\n".join(
        f"- {name}: {description or '(no description)'}"
        for name, description in registry.items()
    )
    tasks = "\n".join(f"- {name}" for name in board) or "- (none)"
    return (
        f"{instructions()}\n"
        f"The registry: every repository in the fleet.\n{repos}\n\n"
        f"The other open tasks on the board, and any finished task this one "
        f"mentions, marked (done).\n{tasks}\n"
    )


def task_message(task: GroomingTask) -> str:
    """The first user message: the task as it stands."""
    problems = "\n".join(f"- {p}" for p in task.problems) or "- (none)"
    tags = ", ".join(task.tags) or "(none)"
    executor = executor_of(task.tags)
    who = (
        f"{executor} (its tag; groom it for that executor)"
        if executor
        else "the agent (no executor tag)"
    )
    message = (
        f"Groom this task.\n\n"
        f"Name: {task.name}\n"
        f"Column: {task.section or '(unknown)'}\n"
        f"Tags: {tags}\n"
        f"Executor: {who}\n\n"
        f"Body:\n{task.notes.strip() or '(empty)'}\n\n"
        f"What the board's linter found missing:\n{problems}\n"
    )
    if task.comments:
        answers = "\n\n".join(f"- {c.strip()}" for c in task.comments)
        message += (
            "\nThe owner's comments since this task's last grooming questions, "
            "oldest first. They answer those questions; use them, and do not "
            f"ask again what they settle:\n{answers}\n"
        )
    return message
