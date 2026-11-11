# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The built UI in a real browser, against the whole deployment.

A manager on uvicorn and Postgres serves the assets ``ts/neorc-ui`` built, with
authentication on: a scheduler and workers run ``examples/wordplay`` over HTTP
with a token, and Chromium, driven by Playwright, signs in through a stand-in
OpenID provider, sees a flow's steps as a graph, starts a run from the form,
watches it succeed, looks at it as a graph, cancels another, and signs out.
Skipped without a built UI or a browser: CI builds and installs both for one
Python, and contributing/dev-environment.md says how to locally.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from conftest import (
    EXAMPLES,
    StandInProvider,
    Tokens,
    deployed_example,
    free_port,
    role_tokens,
    serve_app,
)

from neorc.manager import build_app
from neorc_core.flows import read_flows

if TYPE_CHECKING:
    from playwright.async_api import Page

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


@pytest.fixture
async def deployed(
    pg_schema: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Deployed]:
    """The manager serving the built UI, wordplay uploaded, its default queue served.

    Authentication is on, as a deployment has it: people sign in with a
    stand-in OpenID provider on a port of its own, and the scheduler and
    workers send a token. The ``scoring`` queue, which every run of
    ``word_picker_rounds`` reaches through its sub-flow, gets a worker only
    where the test wants a run to finish: without one, a run stays active for
    as long as it takes to cancel.
    """
    from neorc.auth import parse_auth_config
    from neorc.postgres import PostgresCredentialStore, create_schema
    from neorc_core import Access

    monkeypatch.setattr("neorc_ui.STATIC", _built_ui())
    manager_port, provider_port = free_port(), free_port()
    stand_in = StandInProvider(f"http://127.0.0.1:{provider_port}")
    config = parse_auth_config(
        {
            "public_url": f"http://127.0.0.1:{manager_port}",
            "providers": {
                "stand-in": {
                    "title": "Stand-in",
                    "issuer": stand_in.issuer,
                    "client_id": stand_in.client_id,
                    "client_secret_env": "STAND_IN_SECRET",
                }
            },
            "allow": [{"provider": "stand-in", "email": "ada@example.com"}],
        },
        {"STAND_IN_SECRET": stand_in.client_secret},
    )
    await create_schema(pg_schema)
    async with PostgresCredentialStore(pg_schema) as credentials:
        tokens = await role_tokens(Access(credentials), ["default", "scoring"])
    app = build_app(
        pg_schema,
        auth=True,
        auth_config=config,
        create_schema=True,
        long_poll_timeout=2,
    )
    async with (
        serve_app(stand_in.app, port=provider_port),
        serve_app(app, port=manager_port) as address,
        deployed_example(address, "wordplay", ["default"], tokens=tokens) as client,
    ):
        await client.upload_flows(read_flows(EXAMPLES / "wordplay" / "flows"))
        yield Deployed(address, tokens)


@pytest.fixture
async def browsing() -> AsyncIterator[tuple[Page, list[str]]]:
    """A Chromium page, and the console errors and page errors it raises."""
    playwright = pytest.importorskip("playwright.async_api")
    async with playwright.async_playwright() as driver:
        try:
            browser = await driver.chromium.launch()
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

    await page.goto(f"{ui_address}/ui/#/flows/word_picker_rounds")
    # The flow's steps as a graph, before any run: each loop and fan-out once.
    steps = page.get_by_label("Flow graph")
    await expect(steps.locator(".react-flow__node")).to_have_count(9)
    await expect(steps.get_by_role("link", name="word_picker")).to_be_visible()
    await page.get_by_label("sentence").fill(SENTENCE)
    await page.get_by_label("preferred_letter").fill("t")
    await page.get_by_label("requested_at").fill("2026-09-13T10:00")
    await page.get_by_role("button", name="Start run").click()
    await page.wait_for_url(re.compile(r"#/runs/[0-9a-f-]{36}$"))


@pytest.mark.usefixtures("own_tasks_module")
async def test_a_run_is_started_watched_and_another_cancelled_in_the_browser(
    deployed: Deployed, browsing: tuple[Page, list[str]]
) -> None:
    from playwright.async_api import expect

    page, complaints = browsing
    ui_address = deployed.address
    expect.set_options(timeout=10_000)
    heading = page.get_by_role("heading", level=1)

    # Signed out, the sign-in page stands in for the flows asked for; signing
    # in with the provider comes back to them, with no banner.
    await page.goto(f"{ui_address}/ui/#/flows")
    await page.get_by_role("link", name="Sign in with Stand-in").click()
    await expect(page.get_by_label("Profile")).to_contain_text("Ada Lovelace")
    await expect(page).to_have_url(re.compile(r"/ui/#/flows$"))
    await expect(page.get_by_role("note")).to_have_count(0)
    main_nav = page.get_by_role("navigation", name="Main")
    await main_nav.get_by_role("link", name="Flows").click()
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
    report = page.locator("li.step-task", has_text="report")
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
    await main_nav.get_by_role("link", name="Runs").click()
    rows = page.get_by_role("row")
    await expect(rows).to_have_count(3)
    await expect(rows.nth(1)).to_contain_text("cancelled")
    await expect(rows.nth(2)).to_contain_text("succeeded")

    # Signing out ends the session: the pages give way to the sign-in again,
    # and the API refuses the cookie even sent again by hand, as a copy kept
    # elsewhere would be.
    (session,) = [
        c for c in await page.context.cookies() if c["name"] == "neorc_session"
    ]
    await page.get_by_role("button", name="Sign out").click()
    await expect(page.get_by_role("link", name="Sign in with Stand-in")).to_be_visible()
    refused = await page.request.get(
        f"{ui_address}/runs", headers={"cookie": f"neorc_session={session['value']}"}
    )
    assert refused.status == 401

    assert complaints == [], "the browser complained: " + "; ".join(complaints)
