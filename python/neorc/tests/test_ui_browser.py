# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The built UI in a real browser, against the whole deployment.

Standalone neorc, composed by oneberth, on uvicorn and Postgres, serves the
assets ``ts/neorc-ui`` built: oneberth's shell with one application, neorc. A
scheduler and workers run ``examples/wordplay`` over HTTP with service tokens,
and Chromium, driven by Playwright, signs in on the shell's sign-in page
through oneberth's stand-in OpenID provider, finds neorc chosen on the rail,
sees a flow's steps as a graph, starts a run from the form, watches it
succeed, looks at it as a graph, cancels another, and signs out from the
shell's account button.
A second journey is a person's on ``examples/approval``, in oneberth's local
mode, signed in with a ticket: the queues, a queue's tasks, and the two
answers its run waits for. A third is a person's on ``examples/review_loop``,
in the local mode too: a list of issues triaged on a queue's page, two rounds
of it, and the run ending with the result.
Skipped without a built UI or a browser: CI builds and installs both for one
Python, and contributing/dev-environment.md says how to locally.
``NEORC_TEST_CHROMIUM`` names a Chromium to launch instead of Playwright's own.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import (
    EXAMPLES,
    Tokens,
    declare_queues,
    deployed_example,
    free_port,
    role_tokens,
    serve_app,
)
from oneberth.config import local_config
from oneberth.identity import CredentialKind, new_secret
from oneberth.web import Composition
from oneberth.web.testing import (
    StandInProvider,
    client_of,
    config_for,
    launcher,
    signed_in,
)

from neorc.manager import build_app
from neorc_core.flows import read_flows

if TYPE_CHECKING:
    from playwright.async_api import Page

    from neorc_core import ManagerClient

pytestmark = pytest.mark.postgres

BUILT = Path(__file__).parents[3] / "ts" / "neorc-ui" / "dist"
RUN_TIMEOUT_MS = 90_000
SENTENCE = "potato tomate berry watermelon"


def _built_ui() -> Path:
    if (BUILT / "index.html").is_file():
        return BUILT
    import neorc_ui

    if (neorc_ui.STATIC / "index.html").is_file():
        return neorc_ui.STATIC
    pytest.skip("no built UI: run `npm run build` in ts/neorc-ui")


@dataclass(frozen=True)
class Deployed:
    address: str
    tokens: Tokens
    """What the scheduler, workers and uploads send; people sign in instead."""
    app: Composition


@contextlib.asynccontextmanager
async def _deployment(
    pg_schema: str,
    monkeypatch: pytest.MonkeyPatch,
    example: str,
    *,
    queues: list[str],
    served: list[str],
    external: list[str] | None = None,
    local: bool = False,
) -> AsyncIterator[tuple[Deployed, ManagerClient]]:
    """The manager serving the built UI, an example uploaded, some queues served.

    People sign in with oneberth's stand-in OpenID provider on a port of its
    own, or, ``local``, the local user signs in with a ticket. The scheduler
    and workers send a service token. ``queues`` are declared worker queues,
    with a worker token each, and ``external`` external ones; ``served`` get
    a worker.
    """
    from neorc.postgres import create_schema
    from neorc_core import QueueKind

    monkeypatch.setattr("neorc_ui.STATIC", _built_ui())
    manager_port, provider_port = free_port(), free_port()
    stand_in = StandInProvider(f"http://127.0.0.1:{provider_port}")
    config = (
        local_config("neorc", port=manager_port)
        if local
        else config_for(
            "neorc",
            public_url=f"http://127.0.0.1:{manager_port}",
            providers={stand_in.provider_id: stand_in.config},
        )
    )
    await create_schema(pg_schema)
    await declare_queues(pg_schema, *queues)
    await declare_queues(pg_schema, *(external or []), kind=QueueKind.EXTERNAL)
    app = build_app(
        pg_schema, config=config, create_schema=True, long_poll_timeout=2, local=local
    )
    if local:
        app.launch_secret = new_secret(CredentialKind.LAUNCH)
    async with (
        serve_app(stand_in.app, port=provider_port),
        serve_app(app, port=manager_port) as address,
    ):
        user = await _local_person_token(app) if local else None
        tokens = await role_tokens(app, queues, user=user)
        async with deployed_example(address, example, served, tokens=tokens) as client:
            await client.upload_flows(read_flows(EXAMPLES / example / "flows"))
            yield Deployed(address, tokens, app), client


async def _local_person_token(app: Composition) -> str:
    """The local user's personal token, made on the account page, for a script."""
    async with client_of(app) as client:
        await signed_in(app, client)
        made = await client.post("/auth/tokens", json={"name": "script"})
    assert made.status_code == 201, made.text
    secret: str = made.json()["secret"]
    return secret


async def _open_signed_in(page: Page, deployed: Deployed, hash_: str) -> None:
    """Open ``hash_`` signed in as the local user, as the launcher opens the window."""
    async with client_of(deployed.app) as client:
        asked = await client.post(
            "/auth/tickets", json={}, headers=launcher(deployed.app)
        )
    assert asked.status_code == 201, asked.text
    await page.goto(asked.json()["url"])
    await page.goto(f"{deployed.address}/ui/{hash_}")


@pytest.fixture
async def deployed(
    pg_schema: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Deployed]:
    """The manager serving the built UI, wordplay uploaded, its default queue served.

    The ``scoring`` queue, which every run of ``word_picker_rounds`` reaches
    through its sub-flow, gets a worker only where the test wants a run to
    finish: without one, a run stays active for as long as it takes to cancel.
    """
    async with _deployment(
        pg_schema,
        monkeypatch,
        "wordplay",
        queues=["default", "scoring"],
        served=["default"],
    ) as (deployment, _):
        yield deployment


@pytest.fixture
async def approving(
    pg_schema: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Deployed, ManagerClient]]:
    """The manager serving the built UI, the approval example uploaded.

    Its ``default`` queue has a worker. Its ``editors`` queue is external:
    nobody serves it but the person in the browser.
    """
    async with _deployment(
        pg_schema,
        monkeypatch,
        "approval",
        queues=["default"],
        served=["default"],
        external=["editors"],
        local=True,
    ) as deployment:
        yield deployment


@pytest.fixture
async def triaging(
    pg_schema: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Deployed, ManagerClient]]:
    """The manager serving the built UI, the review_loop example uploaded.

    Its ``default`` queue has a worker, which reviews. Its ``editors`` queue
    is external: the person in the browser triages.
    """
    async with _deployment(
        pg_schema,
        monkeypatch,
        "review_loop",
        queues=["default"],
        served=["default"],
        external=["editors"],
        local=True,
    ) as deployment:
        yield deployment


@pytest.fixture
async def browsing() -> AsyncIterator[tuple[Page, list[str]]]:
    """A Chromium page, and the console errors and page errors it raises."""
    playwright = pytest.importorskip("playwright.async_api")
    async with playwright.async_playwright() as driver:
        try:
            browser = await driver.chromium.launch(
                executable_path=os.environ.get("NEORC_TEST_CHROMIUM") or None
            )
        except playwright.Error as exc:  # no browser downloaded
            pytest.skip(f"no Chromium for Playwright: {str(exc).splitlines()[0]}")
        page = await browser.new_page()
        complaints: list[str] = []
        page.on(
            "console",
            lambda message: (
                complaints.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: complaints.append(str(error)))
        try:
            yield page, complaints
        finally:
            with contextlib.suppress(Exception):
                await browser.close()


async def _start_word_picker_rounds(page: Page, ui_address: str) -> None:
    from playwright.async_api import expect

    await page.goto(f"{ui_address}/ui/#/neorc/flows/word_picker_rounds")
    # The flow's steps as a graph, before any run: each loop and fan-out once.
    steps = page.get_by_label("Flow graph")
    await expect(steps.locator(".react-flow__node")).to_have_count(9)
    await expect(steps.get_by_role("link", name="word_picker")).to_be_visible()
    await page.get_by_label("sentence").fill(SENTENCE)
    await page.get_by_label("preferred_letter").fill("t")
    await page.get_by_label("requested_at").fill("2026-09-13T10:00")
    await page.get_by_role("button", name="Start run").click()
    await page.wait_for_url(re.compile(r"#/neorc/runs/[0-9a-f-]{36}$"))


@pytest.mark.usefixtures("own_tasks_module")
async def test_a_run_is_started_watched_and_another_cancelled_in_the_browser(
    deployed: Deployed, browsing: tuple[Page, list[str]]
) -> None:
    from playwright.async_api import expect

    page, complaints = browsing
    ui_address = deployed.address
    expect.set_options(timeout=10_000)
    heading = page.get_by_role("heading", level=1)

    # Signed out, the shell's sign-in page stands in for the flows asked for;
    # signing in with the provider comes back to them, with no banner, and
    # the rail has neorc chosen, alone.
    await page.goto(f"{ui_address}/ui/#/neorc/flows")
    await page.get_by_role("link", name="Sign in with Stand-in").click()
    account = page.get_by_role("button", name="Account")
    await expect(account).to_have_attribute("title", "Ada Lovelace")
    await expect(page).to_have_url(re.compile(r"/ui/#/neorc/flows$"))
    await expect(page.get_by_role("note")).to_have_count(0)
    rail = page.get_by_role("navigation", name="Applications")
    await expect(rail.get_by_role("link")).to_have_count(1)
    await expect(rail.get_by_role("link", name="neorc")).to_have_attribute(
        "aria-current", "page"
    )
    main_nav = page.get_by_role("navigation", name="Main")
    await main_nav.get_by_role("link", name="Flows", exact=True).click()
    table = page.get_by_role("table")
    await expect(table.get_by_role("link", name="word_picker_rounds")).to_be_visible()

    # A run started from the form, with every declared input, watched to its
    # end: with a worker on the scoring queue for as long as that takes.
    async with deployed_example(
        ui_address, "wordplay", ["scoring"], scheduler=False, tokens=deployed.tokens
    ):
        await _start_word_picker_rounds(page, ui_address)
        await expect(heading).to_contain_text("succeeded", timeout=RUN_TIMEOUT_MS)

    # The fan-out ran a branch per word, and the results are shown.
    await expect(page.get_by_role("region", name="decorate branch 2")).to_be_visible()
    report = page.locator("li.neorc-step-task", has_text="report")
    await report.get_by_text("details").click()
    await expect(report).to_contain_text("potato**")
    await expect(report).to_contain_text("tomate**")

    # The same run as a graph: a node per step, the fan-out's branches side
    # by side, zoomable, a task's details from its node.
    await page.get_by_role("link", name="Graph").click()
    canvas = page.get_by_label("Run graph")
    await expect(canvas.get_by_text("branch 1", exact=True)).to_be_visible()
    await expect(canvas.get_by_text("branch 2", exact=True)).to_be_visible()
    await expect(canvas.locator(".react-flow__node")).to_have_count(13)
    await expect(canvas.locator(".react-flow__edge")).to_have_count(8)
    # A loop is one box, at its latest iteration; an earlier one when picked.
    shown = canvas.get_by_label("runs: iteration shown")
    await expect(shown).to_have_value("2")
    ended = "ran_enough returned true: the loop ended here"
    await expect(canvas.get_by_text(ended)).to_be_visible()
    await canvas.get_by_role("button", name="runs: previous iteration").click()
    await expect(shown).to_have_value("1")
    went_on = "ran_enough returned false: iteration 2 followed"
    await expect(canvas.get_by_text(went_on)).to_be_visible()
    await expect(canvas.locator(".react-flow__node")).to_have_count(13)
    await canvas.get_by_text("report", exact=True).click()
    panel = page.get_by_role("complementary", name="report details")
    await expect(panel).to_contain_text("potato**")
    await panel.get_by_role("button", name="Close").click()
    await expect(panel).to_have_count(0)
    # Zooming out keeps every node on the canvas, whatever the window's size.
    viewport = canvas.locator(".react-flow__viewport")
    before = await viewport.get_attribute("style")
    await canvas.get_by_role("button", name="Zoom Out").click()
    await expect(viewport).not_to_have_attribute("style", before or "")
    # And fitting the view brings them back, after the graph has changed.
    zoomed_out = await viewport.get_attribute("style")
    await canvas.get_by_role("button", name="Fit View").click()
    await expect(viewport).not_to_have_attribute("style", zoomed_out or "")
    # Back to the outline, which the next run opens with: the choice is kept.
    await page.get_by_role("link", name="Outline").click()
    await expect(page.get_by_role("region", name="decorate branch 2")).to_be_visible()

    # Another run, which cannot finish with nobody serving the scoring queue,
    # cancelled from its page.
    await _start_word_picker_rounds(page, ui_address)
    await page.get_by_role("button", name="Cancel run").click()
    asking = page.get_by_role("group", name="Confirm cancelling")
    await expect(asking).to_contain_text("every active run in its tree")
    await page.get_by_role("button", name="Cancel it").click()
    await expect(heading).to_contain_text("cancelled", timeout=RUN_TIMEOUT_MS)
    await expect(page.locator("dd", has_text="cancelled by hand")).to_be_visible()

    # The runs list has both, newest first.
    await main_nav.get_by_role("link", name="Runs", exact=True).click()
    rows = page.get_by_role("row")
    await expect(rows).to_have_count(3)
    await expect(rows.nth(1)).to_contain_text("cancelled")
    await expect(rows.nth(2)).to_contain_text("succeeded")

    # Signing out from the account button ends the session: the pages give way
    # to the sign-in again, and the API refuses the cookie even sent again by
    # hand, as a copy kept elsewhere would be.
    cookie = deployed.app.cookies.session
    (session,) = [c for c in await page.context.cookies() if c["name"] == cookie]
    await account.click()
    menu = page.get_by_role("group", name="Account")
    await expect(menu).to_contain_text("ada@example.com")
    await menu.get_by_role("button", name="Sign out").click()
    await expect(page.get_by_role("link", name="Sign in with Stand-in")).to_be_visible()
    refused = await page.request.get(
        f"{ui_address}/neorc/runs", headers={"cookie": f"{cookie}={session['value']}"}
    )
    assert refused.status == 401

    assert complaints == [], "the browser complained: " + "; ".join(complaints)


@pytest.mark.usefixtures("own_tasks_module")
async def test_a_person_answers_the_tasks_of_an_external_queue_in_the_browser(
    approving: tuple[Deployed, ManagerClient], browsing: tuple[Page, list[str]]
) -> None:
    """docs/specs/task-completion.md, from the page a person uses.

    The queues by kind, a queue's tasks by state, flow and time, a task with
    its inputs, the form of its step and the plain one, what the manager
    refuses, and the run going on with both answers.
    """
    from playwright.async_api import expect

    (deployed, client), (page, complaints) = approving, browsing
    ui_address = deployed.address
    expect.set_options(timeout=10_000)
    run = await client.start_run("approval", {"topic": "tea"})

    # Signed in as the person at the machine, with no sign-in page, a person
    # sees every queue, with its kind.
    await _open_signed_in(page, deployed, "#/neorc/queues")
    await expect(page.get_by_role("button", name="Account")).to_be_visible()
    await expect(page.get_by_role("link", name="Sign in with Stand-in")).to_have_count(
        0
    )
    await expect(page).to_have_url(re.compile(r"/ui/#/neorc/queues$"))
    rows = page.get_by_role("row")
    filters = page.get_by_role("form", name="Filters")
    await expect(rows).to_have_count(3)
    await expect(rows.nth(1)).to_contain_text("default")
    await expect(rows.nth(1)).to_contain_text("worker")
    await expect(rows.nth(2)).to_contain_text("editors")
    await expect(rows.nth(2)).to_contain_text("external")
    await filters.get_by_label("Kind").select_option("external")
    await expect(rows).to_have_count(2)
    await expect(rows.nth(1)).to_contain_text("editors")

    # The sidebar lists the queues too, below its "Queues" entry, of the kind
    # chosen there. The entry's arrow folds the list away and brings it back.
    main_nav = page.get_by_role("navigation", name="Main")
    sidebar = main_nav.get_by_role("navigation", name="Queues")
    kinds = sidebar.get_by_role("group", name="Kind of queue")
    await expect(sidebar.get_by_role("link")).to_have_count(2)
    await main_nav.get_by_role("button", name="Collapse Queues").click()
    await expect(sidebar).to_have_count(0)
    await expect(
        main_nav.get_by_role("link", name="Queues", exact=True)
    ).to_be_visible()
    await main_nav.get_by_role("button", name="Expand Queues").click()
    await expect(sidebar.get_by_role("link")).to_have_count(2)
    await kinds.get_by_role("button", name="worker").click()
    await expect(sidebar.get_by_role("link")).to_have_text(["default"])
    await kinds.get_by_role("button", name="external").click()
    await expect(sidebar.get_by_role("link")).to_have_text(["editors"])
    await kinds.get_by_role("button", name="all").click()
    await sidebar.get_by_role("link", name="editors").click()

    # The queue's pending tasks, once a worker has written the draft.
    await expect(page.get_by_role("heading", level=1)).to_contain_text("editors")
    await expect(filters.get_by_label("State")).to_have_value("pending")
    await expect(filters.get_by_label("Flow")).to_have_value("")
    await expect(filters.get_by_label("Changed in the last")).to_have_value("24h")
    await expect(rows).to_have_count(3, timeout=RUN_TIMEOUT_MS)
    await expect(rows.nth(1)).to_contain_text("approval")
    await filters.get_by_label("Flow").select_option("approval")
    await expect(page).to_have_url(re.compile(r"flow=approval"))
    await expect(rows).to_have_count(3)

    # A task with a form: its inputs, and the form of its step.
    await page.get_by_role("link", name=re.compile("review_draft")).click()
    panel = page.get_by_role("complementary", name="Task")
    await expect(panel).to_contain_text("review-draft")
    await expect(panel).to_contain_text("A first draft about tea.")
    form = panel.get_by_role("form", name="Complete review_draft")
    await expect(form.get_by_label("Approve")).to_be_checked()
    await form.get_by_label("Ask for changes").check()
    # The form's own check, said before anything is sent.
    await form.get_by_label("Comment for the author").fill("x" * 501)
    await form.get_by_role("button", name="Complete").click()
    await expect(form.get_by_role("alert")).to_have_text(
        "Comment for the author: Keep the comment under 500 characters."
    )
    # What the form cannot submit, the manager refuses, and the task waits.
    await form.get_by_role("button", name="Edit as JSON").click()
    await form.get_by_label("Result as JSON").fill('{"decision": "later"}')
    await form.get_by_role("button", name="Complete").click()
    await expect(form.get_by_role("alert")).to_contain_text("no field 'comment'")
    await form.get_by_role("button", name="Back to the form").click()
    await form.get_by_label("Comment for the author").fill("Shorter, please.")
    await form.get_by_role("button", name="Complete").click()
    await expect(panel.locator("[data-status]")).to_have_attribute(
        "data-status", "succeeded"
    )
    await expect(panel).to_contain_text("Shorter, please.")
    await expect(form).to_have_count(0)
    await expect(rows).to_have_count(2)  # it left the pending tasks

    # A task with no form: a text is the answer.
    await page.get_by_role("link", name=re.compile("suggest_title")).click()
    plain = panel.get_by_role("form", name="Complete suggest_title")
    await plain.get_by_role("button", name="Complete").click()
    await expect(plain.get_by_role("alert")).to_have_text("Write an answer.")
    await plain.get_by_label("Answer").fill("On tea")
    await plain.get_by_role("button", name="Complete").click()
    await expect(panel.locator("[data-status]")).to_have_attribute(
        "data-status", "succeeded"
    )
    await expect(page.get_by_text("No pending task of approval")).to_be_visible()

    # The run went on with both answers.
    await panel.get_by_role("link", name=str(run.id)[:8]).click()
    heading = page.get_by_role("heading", level=1)
    await expect(heading).to_contain_text("succeeded", timeout=RUN_TIMEOUT_MS)
    finished = await client.get_run(run.id)
    assert finished.output == (
        "Not published. The editor asked for changes: Shorter, please."
    )

    # Every state, and further back: the two answers, the latest first.
    await page.goto(f"{ui_address}/ui/#/neorc/queues/editors")
    await filters.get_by_label("State").select_option("all")
    await expect(rows).to_have_count(3)
    await expect(rows.nth(1)).to_contain_text("suggest_title")
    await expect(rows.nth(1)).to_contain_text("succeeded")
    await filters.get_by_label("Changed in the last").select_option("1y")
    await expect(page).to_have_url(re.compile(r"window=1y"))
    await expect(rows).to_have_count(3)
    await filters.get_by_label("State").select_option("failed")
    await expect(page.get_by_text("No failed task")).to_be_visible()

    # A worker queue's tasks are listed and read, with no form to complete.
    await sidebar.get_by_role("link", name="default").click()
    await expect(heading).to_contain_text("default")
    await filters.get_by_label("State").select_option("succeeded")
    await expect(rows).to_have_count(3)
    await page.get_by_role("link", name=re.compile("write_draft")).click()
    await expect(panel).to_contain_text("A first draft about tea.")
    await expect(panel.get_by_role("form")).to_have_count(0)

    # A run that ends takes its waiting tasks with it: the list drops them,
    # and the task that was open loses its form.
    ended = await client.start_run("approval", {"topic": "coffee"})
    await page.goto(f"{ui_address}/ui/#/neorc/queues/editors")
    await expect(rows).to_have_count(3, timeout=RUN_TIMEOUT_MS)
    await page.get_by_role("link", name=re.compile("review_draft")).click()
    await expect(panel.get_by_role("form")).to_be_visible()
    await client.cancel_run(ended.id)
    await expect(panel).to_contain_text("Its run ended (cancelled: cancelled by hand)")
    await expect(panel.get_by_role("form")).to_have_count(0)
    await expect(page.get_by_text("No pending task")).to_be_visible()

    # The browser's console names the one completion the manager refused.
    assert len(complaints) == 1 and "status of 422" in complaints[0], complaints


@pytest.mark.usefixtures("own_tasks_module")
async def test_a_person_triages_a_list_of_issues_in_the_browser(
    triaging: tuple[Deployed, ManagerClient], browsing: tuple[Page, list[str]]
) -> None:
    """docs/architecture/forms.md, a task with a form that reads its inputs.

    One card per issue of the review, a bar of three buttons in each, and
    Re-work and Ready to complete the task: twice, as the loop goes round.
    """
    from playwright.async_api import expect

    (deployed, client), (page, complaints) = triaging, browsing
    ui_address = deployed.address
    expect.set_options(timeout=10_000)
    run = await client.start_run("review_loop", {"change": "Add a retry"})

    await _open_signed_in(page, deployed, "#/neorc/queues/editors")
    await expect(page.get_by_role("button", name="Account")).to_be_visible()
    rows = page.get_by_role("row")
    panel = page.get_by_role("complementary", name="Task")
    await expect(rows).to_have_count(2, timeout=RUN_TIMEOUT_MS)

    # The first round: the review's issues, one card each, read from the
    # task's input.
    await page.get_by_role("link", name=re.compile("triage")).click()
    form = panel.get_by_role("form", name="Complete triage")
    await expect(form).to_contain_text("The review of 'Add a retry' found 3 issues.")
    cards = form.get_by_role("article")
    await expect(cards).to_have_count(3)
    await expect(cards.nth(0)).to_contain_text("blocker")
    await expect(cards.nth(0)).to_contain_text("Crash on empty input")
    await expect(cards.nth(0)).to_contain_text("a.py:9")
    await expect(cards.nth(2)).to_contain_text("Typo in a docstring")
    # Re-work and Ready complete the task: the form has no other button.
    await expect(form.get_by_role("button", name="Complete")).to_have_count(0)
    # Every issue needs a decision, before anything is sent.
    first = cards.nth(0).get_by_role("group", name="Decision")
    await first.get_by_role("button", name="Fix").click()
    await expect(first.get_by_role("button", name="Fix")).to_have_attribute(
        "aria-pressed", "true"
    )
    await form.get_by_role("button", name="Ready").click()
    await expect(form.get_by_role("alert")).to_have_text("Decision, item 2: choose one")
    # What the form cannot submit, the manager refuses: a decision per item.
    await form.get_by_role("button", name="Edit as JSON").click()
    await form.get_by_label("Result as JSON").fill(
        '{"decision": ["fix"], "verdict": "ready"}'
    )
    await form.get_by_role("button", name="Complete").click()
    await expect(form.get_by_role("alert")).to_contain_text(
        "field 'decision' has 1 values, and /inputs/review/issues has 3 items"
    )
    await form.get_by_role("button", name="Back to the form").click()
    for card, decision in zip(range(3), ["Defer", "Defer", "Discard"], strict=True):
        bar = cards.nth(card).get_by_role("group", name="Decision")
        await bar.get_by_role("button", name=decision).click()
    await form.get_by_role("button", name="Re-work").click()
    await expect(panel.locator("[data-status]")).to_have_attribute(
        "data-status", "succeeded"
    )
    await expect(panel).to_contain_text('"verdict": "rework"')

    # Re-work starts another round, with a task of its own.
    await expect(rows).to_have_count(2, timeout=RUN_TIMEOUT_MS)
    await page.get_by_role("link", name=re.compile("triage")).click()
    await expect(cards).to_have_count(3)
    for card, decision in zip(range(3), ["Fix", "Fix", "Discard"], strict=True):
        bar = cards.nth(card).get_by_role("group", name="Decision")
        await bar.get_by_role("button", name=decision).click()
    await form.get_by_role("button", name="Ready").click()
    await expect(panel.locator("[data-status]")).to_have_attribute(
        "data-status", "succeeded"
    )

    # Ready ends the loop, and the run goes on with the triage. The task was
    # a round's, a sub-flow's run; the run started is the loop's.
    await page.goto(f"{ui_address}/ui/#/neorc/runs/{run.id}")
    heading = page.get_by_role("heading", level=1)
    await expect(heading).to_contain_text("succeeded", timeout=RUN_TIMEOUT_MS)
    finished = await client.get_run(run.id)
    assert finished.output == (
        "Ready after 2 rounds: 2 to fix, 0 deferred, 1 discarded."
    )

    # The browser's console names the one completion the manager refused.
    assert len(complaints) == 1 and "status of 422" in complaints[0], complaints
