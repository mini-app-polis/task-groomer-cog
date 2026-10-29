"""task-groomer-cog's Lambda handler, end to end on SQS events.

A job is one paid run: the model reads the task and the fleet's code through
the read-only tools, proposes, and the proposal goes back through the API.
These tests drive ``lambda_handler`` with realistic records and look at what
the stubbed services were sent: what was paid for, what was recorded
against the month's cap, and what was proposed.
"""

from __future__ import annotations

import httpx

from .harness import (
    API_KEY,
    FINGERPRINT,
    TASK_GID,
    LambdaContext,
    groom_body,
    propose,
    sqs_event,
    sqs_record,
    tool_use,
)


def _severities(posted: list[tuple]) -> list[str]:
    """The severity of each run report or finding the handler posted."""
    return [args[1] if len(args) > 1 else kwargs["severity"] for args, kwargs in posted]


def test_a_job_reads_the_code_and_hands_its_proposal_back(
    world, handler, _no_outbound_reports
) -> None:
    model, github, api = world
    model.conversation = [
        tool_use("read_file", {"repo": "infra", "path": "cogs.hcl"}),
        propose(),
    ]

    result = handler(sqs_event(sqs_record(groom_body(), "m-1")))

    assert result == {"batchItemFailures": []}
    # The tool read the real archive, and its text went back to the model.
    assert github.downloads == ["infra"]
    tool_result = model.requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "cog-worker" in tool_result["content"]
    # The run's cost is recorded once, with what it used.
    (run,) = api.runs
    assert run["task_gid"] == TASK_GID
    assert run["input_tokens"] == 2000 and run["output_tokens"] == 400
    assert run["cost_usd"] > 0
    # The proposal carries the content it was made for, and goes back once.
    (proposal,) = api.proposals
    assert proposal["fingerprint"] == FINGERPRINT
    assert proposal["title"] == "Dashboard for TV shows and movies"
    assert proposal["effort"] == "M"
    assert proposal["questions"] == ["Which service holds the watch history?"]
    # Under this cog's own key, never a shared one.
    assert set(api.auth) == {f"Bearer {API_KEY}"}
    assert _severities(_no_outbound_reports) == ["SUCCESS"]


def test_a_duplicate_delivery_proposes_for_the_same_content(world, handler) -> None:
    """SQS delivers at least once. The queue allows one receive, so a failed
    job is never retried, but SQS can still hand the same message over
    twice. The first delivery claims the task and its content before the
    model is called; the second finds the claim held and is acknowledged
    with nothing paid, recorded or proposed."""
    model, _github, api = world
    model.conversation = [propose()]
    body = groom_body()

    for attempt in (1, 1):
        result = handler(sqs_event(sqs_record(body, "m-1", receive_count=attempt)))
        assert result == {"batchItemFailures": []}

    assert model.first_requests() == 1
    assert len(api.runs) == 1
    assert [p["fingerprint"] for p in api.proposals] == [FINGERPRINT]
    assert api.claims == {(TASK_GID, FINGERPRINT): "claimed"}
    assert api.releases == []


def test_a_failed_run_gives_its_claim_back_so_the_next_send_can_groom(
    world, handler
) -> None:
    model, _github, api = world
    model.conversation = [propose()]
    api.fail("/v1/tasks/groomings/proposals", 502)

    failed = handler(sqs_event(sqs_record(groom_body(), "m-1")))
    assert failed == {"batchItemFailures": [{"itemIdentifier": "m-1"}]}
    assert api.claims == {(TASK_GID, FINGERPRINT): "released"}

    # The same content, sent again in a new message, is groomed.
    again = handler(sqs_event(sqs_record(groom_body(), "m-2")))
    assert again == {"batchItemFailures": []}
    assert len(api.proposals) == 1
    assert model.first_requests() == 2


def test_a_claim_that_cannot_be_asked_for_drops_the_job_unpaid(
    world, handler, _no_outbound_reports
) -> None:
    model, _github, api = world
    model.conversation = [propose()]
    api.fail("/v1/tasks/groomings/claims", 502)

    result = handler(sqs_event(sqs_record(groom_body(), "m-1")))

    assert result == {"batchItemFailures": []}
    assert model.requests == []
    assert api.runs == [] and api.proposals == []
    assert _severities(_no_outbound_reports) == ["WARN"]


def test_a_bad_record_is_returned_and_the_rest_of_the_batch_completes(
    world, handler, _no_outbound_reports
) -> None:
    model, _github, api = world
    model.conversation = [propose()]
    event = sqs_event(
        sqs_record("not json at all", "bad-1"),
        sqs_record(groom_body(), "good-1"),
        sqs_record('{"type": "task.create", "version": 1, "payload": {}}', "bad-2"),
    )

    result = handler(event)

    assert result == {
        "batchItemFailures": [{"itemIdentifier": "bad-1"}, {"itemIdentifier": "bad-2"}]
    }
    # The good job was groomed regardless.
    assert len(api.proposals) == 1
    # One ERROR per unprocessable message, and the good run's report.
    assert sorted(_severities(_no_outbound_reports)) == ["ERROR", "ERROR", "SUCCESS"]


def test_an_unprocessable_message_is_reported_only_on_its_first_receive(
    world, handler, _no_outbound_reports
) -> None:
    for attempt in (1, 2):
        result = handler(
            sqs_event(sqs_record("not json", "bad-1", receive_count=attempt))
        )
        assert result == {"batchItemFailures": [{"itemIdentifier": "bad-1"}]}

    assert _severities(_no_outbound_reports) == ["ERROR"]
    _model, _github, api = world
    assert api.runs == [] and api.proposals == []


def test_the_monthly_cap_stops_a_run_before_it_is_paid(
    world, handler, _no_outbound_reports
) -> None:
    model, _github, api = world
    api.spent = 20.0  # GROOMER_MONTHLY_CAP_USD

    result = handler(sqs_event(sqs_record(groom_body(), "m-1")))

    # Handled, not failed: nothing was spent, and a redelivery would only be
    # refused again.
    assert result == {"batchItemFailures": []}
    assert model.requests == []
    assert api.runs == [] and api.proposals == []
    assert api.claims == {(TASK_GID, FINGERPRINT): "released"}
    assert _severities(_no_outbound_reports) == ["WARN"]


def test_a_proposal_the_api_refuses_is_posted_once_and_the_job_dead_letters(
    world, handler
) -> None:
    """A 502 on the proposal is not retried by the client (a POST is not
    idempotent) or by the queue (one receive), so the run is not paid for
    twice: the record goes to the dead-letter queue with its cost recorded."""
    model, _github, api = world
    model.conversation = [propose()]
    api.fail("/v1/tasks/groomings/proposals", 502)

    result = handler(sqs_event(sqs_record(groom_body(), "m-1")))

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-1"}]}
    assert api.attempts["/v1/tasks/groomings/proposals"] == 1
    assert api.proposals == []
    assert len(api.runs) == 1
    assert model.first_requests() == 1


def test_a_transient_model_failure_is_tried_again_inside_the_run(
    world, handler
) -> None:
    model, _github, api = world
    model.conversation = [httpx.Response(529, json={"error": "overloaded"}), propose()]

    result = handler(sqs_event(sqs_record(groom_body(), "m-1")))

    assert result == {"batchItemFailures": []}
    assert len(model.requests) == 2
    assert len(api.runs) == 1 and len(api.proposals) == 1


def test_a_job_with_no_time_left_is_returned_without_a_paid_call(
    world, handler
) -> None:
    model, _github, api = world
    model.conversation = [propose()]

    result = handler(
        sqs_event(sqs_record(groom_body(), "m-1")),
        LambdaContext(remaining_ms=5_000),
    )

    assert result == {"batchItemFailures": [{"itemIdentifier": "m-1"}]}
    assert model.requests == []
    assert api.runs == [] and api.proposals == []
