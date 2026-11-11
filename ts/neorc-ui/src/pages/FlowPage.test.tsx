import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HELLO, WORD_PICKER, WORD_PICKER_1_0 } from "../test/fixtures";
import { RECORDED } from "../test/recorded";
import { mockApi, refusal, renderWithClient } from "../test/render";
import { FlowPage } from "./FlowPage";

describe("FlowPage", () => {
  it("shows the latest definition and every version", async () => {
    mockApi({
      "/flows/word_picker": WORD_PICKER,
      "/flows/word_picker/versions": {
        versions: [WORD_PICKER, WORD_PICKER_1_0],
      },
    });

    renderWithClient(<FlowPage flow="word_picker" />);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      "word_picker 1.2.0",
    );
    const options = screen.getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "1.2.0 (latest)",
      "1.0.0",
    ]);
    expect(screen.getByLabelText("Definition")).toHaveTextContent(
      '"handler": "tasks:split_words"',
    );
    expect(screen.getByRole("link", { name: "Runs of this flow" })).toHaveAttribute(
      "href",
      "#/runs?flow=word_picker",
    );
    expect(screen.getByRole("form", { name: "Start word_picker" })).toBeInTheDocument();
  });

  it("draws the steps after the version, then the form, then the definition", async () => {
    mockApi({
      "/flows/word_picker": WORD_PICKER,
      "/flows/word_picker/versions": { versions: [WORD_PICKER, WORD_PICKER_1_0] },
    });

    renderWithClient(<FlowPage flow="word_picker" />);

    const canvas = await screen.findByLabelText("Flow graph");
    await waitFor(() => expect(canvas.querySelectorAll(".react-flow__node")).toHaveLength(2));
    expect(canvas.querySelectorAll(".react-flow__edge")).toHaveLength(1);
    expect(within(canvas).getByText("split_words")).toBeInTheDocument();
    expect(within(canvas).getByText("tasks:keep_matching_words")).toBeInTheDocument();
    // A definition, not a run: the queue each task is published on, no status.
    expect(within(canvas).getAllByText("default")).toHaveLength(2);
    expect(canvas.querySelector("[data-status]")).toBeNull();
    expect(within(canvas).queryByText("none")).not.toBeInTheDocument();

    const inOrder = [
      screen.getByLabelText(/Version/),
      screen.getByRole("heading", { name: "Steps" }),
      canvas,
      screen.getByRole("form", { name: "Start word_picker" }),
      screen.getByRole("heading", { name: "Definition" }),
      screen.getByLabelText("Definition"),
    ];
    inOrder.slice(1).forEach((element, i) => {
      expect(
        inOrder[i]!.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });
  });

  it("opens what a step is given when its node is clicked", async () => {
    mockApi({
      "/flows/word_picker": WORD_PICKER,
      "/flows/word_picker/versions": { versions: [WORD_PICKER] },
    });

    renderWithClient(<FlowPage flow="word_picker" />);
    const canvas = await screen.findByLabelText("Flow graph");
    fireEvent.click(await within(canvas).findByText("keep_matching_words"));

    const panel = await screen.findByRole("complementary", {
      name: "keep_matching_words details",
    });
    expect(within(panel).getByText("tasks:keep_matching_words")).toBeInTheDocument();
    expect(within(panel).getByText(/"words": "tasks.split_words"/)).toBeInTheDocument();
    expect(within(panel).getByText(/"letter": "inputs.preferred_letter"/)).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("draws loops, fan-outs and sub-flows once each, as the definition has them", async () => {
    const flow = RECORDED.flows["word_picker_rounds"]!;
    mockApi({
      "/flows/word_picker_rounds": flow,
      "/flows/word_picker_rounds/versions": { versions: [flow] },
    });

    renderWithClient(<FlowPage flow="word_picker_rounds" />);

    const canvas = await screen.findByLabelText("Flow graph");
    await waitFor(() => expect(canvas.querySelectorAll(".react-flow__node")).toHaveLength(9));
    expect(canvas.querySelectorAll(".react-flow__edge")).toHaveLength(5);
    expect(within(canvas).getByText("loop, at most 4, until ran_enough")).toBeInTheDocument();
    expect(within(canvas).getByText("fan-out over tasks.final_words")).toBeInTheDocument();
    expect(within(canvas).getByText("loop, at most 5, until long_enough")).toBeInTheDocument();
    // Nothing of a run: no iteration to pick, nothing "not entered yet".
    expect(within(canvas).queryByText("not entered yet")).not.toBeInTheDocument();
    expect(within(canvas).queryByLabelText(/iteration shown/)).not.toBeInTheDocument();
    // A sub-flow leads to the flow it calls.
    expect(within(canvas).getByRole("link", { name: "word_picker" })).toHaveAttribute(
      "href",
      "#/flows/word_picker",
    );
    fireEvent.click(within(canvas).getByText("picker"));
    const panel = await screen.findByRole("complementary", { name: "picker details" });
    expect(within(panel).getByText(/"sentence": "inputs.sentence"/)).toBeInTheDocument();
  });

  it("draws the version shown, not the latest", async () => {
    const older = {
      ...HELLO,
      name: "word_picker",
      version: "1.0.0",
      content: { ...(HELLO.content as object), name: "word_picker", version: "1.0.0" },
    };
    mockApi({
      "/flows/word_picker/versions/1.0.0": older,
      "/flows/word_picker/versions": { versions: [WORD_PICKER, older] },
    });

    renderWithClient(<FlowPage flow="word_picker" version="1.0.0" />);

    const canvas = await screen.findByLabelText("Flow graph");
    await waitFor(() => expect(canvas.querySelectorAll(".react-flow__node")).toHaveLength(1));
    expect(within(canvas).getByText("greet")).toBeInTheDocument();
    expect(within(canvas).queryByText("split_words")).not.toBeInTheDocument();
  });

  it("starts runs on the latest version only", async () => {
    mockApi({
      "/flows/word_picker/versions/1.0.0": WORD_PICKER_1_0,
      "/flows/word_picker/versions": {
        versions: [WORD_PICKER, WORD_PICKER_1_0],
      },
    });

    renderWithClient(<FlowPage flow="word_picker" version="1.0.0" />);

    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
    expect(screen.getByText(/A run always uses the latest version, 1.2.0/)).toBeInTheDocument();
  });

  it("navigates to a chosen version", async () => {
    mockApi({
      "/flows/word_picker": WORD_PICKER,
      "/flows/word_picker/versions/1.0.0": WORD_PICKER_1_0,
      "/flows/word_picker/versions": {
        versions: [WORD_PICKER, WORD_PICKER_1_0],
      },
    });

    renderWithClient(<FlowPage flow="word_picker" />);
    await screen.findByRole("heading", { level: 1 });
    fireEvent.change(screen.getByLabelText(/Version/), {
      target: { value: "1.0.0" },
    });

    expect(location.hash).toBe("#/flows/word_picker?version=1.0.0");
  });

  it("reports a flow the manager does not have", async () => {
    mockApi({
      "/flows/nothing": refusal(404, "FlowNotFoundError", "no flow 'nothing'"),
      "/flows/nothing/versions": refusal(
        404,
        "FlowNotFoundError",
        "no flow 'nothing'",
      ),
    });

    renderWithClient(<FlowPage flow="nothing" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("no flow 'nothing'");
  });
});
