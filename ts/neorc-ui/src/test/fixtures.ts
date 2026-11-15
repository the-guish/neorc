// Responses as the manager sends them, typed by the schema so a drift shows
// here at compile time.
import type { Session } from "@oneberth/ui";

import type { ApiEvent, Delivery, Flow, Queue, QueueTask, Run, Task } from "../api";
import reviewForm from "../forms/review_draft.form.json";
import triageForm from "../forms/triage.form.json";

/** oneberth's `GET /auth/session` with nobody signed in, through providers. */
export const SIGNED_OUT: Session = {
  mode: "providers",
  providers: [
    { id: "google", title: "Google" },
    { id: "okta", title: "Okta" },
  ],
  principal: null,
  roles: [],
};

/** oneberth's `GET /auth/session` with Ada signed in. */
export const SIGNED_IN: Session = {
  ...SIGNED_OUT,
  principal: {
    kind: "user",
    id: "0ddddddd-0000-4000-8000-00000000000a",
    name: "Ada Lovelace",
    email: "ada@example.com",
    provider: "google",
  },
  roles: ["neorc/user"],
};

export const WORD_PICKER: Flow = {
  name: "word_picker",
  version: "1.2.0",
  content: {
    name: "word_picker",
    version: "1.2.0",
    inputs: { sentence: "string", preferred_letter: "string" },
    output: "tasks.keep_matching_words",
    steps: {
      split_words: {
        handler: "tasks:split_words",
        params: { sentence: "inputs.sentence" },
      },
      keep_matching_words: {
        handler: "tasks:keep_matching_words",
        params: {
          words: "tasks.split_words",
          letter: "inputs.preferred_letter",
        },
      },
    },
  },
};

export const WORD_PICKER_1_0: Flow = {
  ...WORD_PICKER,
  version: "1.0.0",
  content: { ...(WORD_PICKER.content as object), version: "1.0.0" },
};

export const HELLO: Flow = {
  name: "hello",
  version: "0.1.0",
  content: {
    name: "hello",
    version: "0.1.0",
    steps: { greet: { handler: "tasks:greet" } },
  },
};

export const SUCCEEDED: Run = {
  id: "5f3a1c2e-1111-4bbb-8ccc-000000000001",
  flow: "word_picker",
  version: "1.2.0",
  inputs: { sentence: "potato tomate", preferred_letter: "t" },
  status: "succeeded",
  root_id: "5f3a1c2e-1111-4bbb-8ccc-000000000001",
  parent_id: null,
  parent_address: null,
  output: ["potato", "tomate"],
  reason: null,
  created_at: "2026-09-13T10:00:00+00:00",
  finished_at: "2026-09-13T10:00:02.500000+00:00",
  started_by: { kind: "user", id: "0d1e2f30-1111-4bbb-8ccc-0000000000ad", name: "Ada Lovelace" },
};

export const ACTIVE: Run = {
  id: "a11ce000-1111-4bbb-8ccc-000000000002",
  flow: "hello",
  version: "0.1.0",
  inputs: {},
  status: "active",
  root_id: "a11ce000-1111-4bbb-8ccc-000000000002",
  parent_id: null,
  parent_address: null,
  output: null,
  reason: null,
  created_at: "2026-09-13T10:05:00+00:00",
  finished_at: null,
  started_by: { kind: "service", id: "5e7f1ce0-1111-4bbb-8ccc-000000000007", name: "nightly" },
};

export const CANCELLED: Run = {
  ...ACTIVE,
  id: "ca7ce11e-1111-4bbb-8ccc-000000000003",
  root_id: "ca7ce11e-1111-4bbb-8ccc-000000000003",
  status: "cancelled",
  reason: "cancelled by hand",
  finished_at: "2026-09-13T10:06:00+00:00",
};

export const SPLIT: Task = {
  id: "7a7a7a7a-0000-4000-8000-000000000001",
  run_id: SUCCEEDED.id,
  address: { step: "split_words", scope: [] },
  queue: "default",
  handler: "tasks:split_words",
  params: { sentence: "inputs.sentence" },
  fixed_params: {},
  status: "succeeded",
  attempts: 1,
  lease_expires_at: null,
  result: ["potato", "tomate"],
  error: null,
  created_at: "2026-09-13T10:00:00.100000+00:00",
  started_at: "2026-09-13T10:00:00.200000+00:00",
  finished_at: "2026-09-13T10:00:01+00:00",
};

export function event(sequence: number, run: Run): ApiEvent {
  return { sequence, run_id: run.id, kind: "run_finished" };
}

// A flow that waits for a person: examples/approval, as the manager stores it.

export const QUEUES: Queue[] = [
  { name: "default", kind: "worker" },
  { name: "editors", kind: "external" },
];

export const APPROVAL: Flow = {
  name: "approval",
  version: "1.0.0",
  content: {
    name: "approval",
    version: "1.0.0",
    inputs: { topic: "string" },
    output: "tasks.publish",
    steps: {
      write_draft: {
        handler: "tasks:write_draft",
        params: { topic: "inputs.topic" },
      },
      review_draft: {
        queue: "editors",
        handler: "review-draft",
        params: { draft: "tasks.write_draft" },
        form: reviewForm,
      },
      suggest_title: {
        queue: "editors",
        handler: "suggest-title",
        params: { draft: "tasks.write_draft" },
      },
      publish: {
        handler: "tasks:publish",
        params: {
          draft: "tasks.write_draft",
          review: "tasks.review_draft",
          title: "tasks.suggest_title",
        },
      },
    },
  },
};

export const WAITING: Run = {
  ...ACTIVE,
  id: "ed170000-1111-4bbb-8ccc-000000000004",
  root_id: "ed170000-1111-4bbb-8ccc-000000000004",
  flow: "approval",
  version: "1.0.0",
  inputs: { topic: "tea" },
};

export const REVIEW: Task = {
  id: "4e71e000-0000-4000-8000-000000000001",
  run_id: WAITING.id,
  address: { step: "review_draft", scope: [] },
  queue: "editors",
  handler: "review-draft",
  params: { draft: "tasks.write_draft" },
  fixed_params: {},
  status: "pending",
  attempts: 0,
  lease_expires_at: null,
  result: null,
  error: null,
  created_at: "2026-09-13T10:05:01+00:00",
  started_at: null,
  finished_at: null,
};

export const TITLE: Task = {
  ...REVIEW,
  id: "717e0000-0000-4000-8000-000000000002",
  address: { step: "suggest_title", scope: [] },
  handler: "suggest-title",
};

export function listed(task: Task): QueueTask {
  return { task, flow: "approval", version: "1.0.0" };
}

export function delivered(task: Task): Delivery {
  return { task, inputs: { draft: "A first draft about tea." } };
}

// A round of examples/review_loop: a person triages the issues of a review.

export const REVIEW_ROUND: Flow = {
  name: "review_round",
  version: "1.0.0",
  content: {
    name: "review_round",
    version: "1.0.0",
    inputs: { change: "string" },
    output: "tasks.triage",
    steps: {
      review: { handler: "tasks:review", params: { change: "inputs.change" } },
      triage: {
        queue: "editors",
        handler: "triage-issues",
        params: { review: "tasks.review" },
        form: triageForm,
      },
    },
  },
};

export const TRIAGE: Task = {
  ...REVIEW,
  id: "7e1a9e00-0000-4000-8000-000000000003",
  address: { step: "triage", scope: [] },
  handler: "triage-issues",
  params: { review: "tasks.review" },
};

/** What the reviewer found, as the triage task's input. */
export const FOUND = {
  pr_url: "https://github.com/o/r/pull/7",
  summary: "The review found 2 issues.",
  issues: [
    { n: 1, severity: "blocker", title: "Crash on empty input", detail: "a.py:9" },
    { n: 2, severity: "minor", title: "Typo in a docstring", detail: "a.py:2" },
  ],
};
