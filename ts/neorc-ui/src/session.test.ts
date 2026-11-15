import { describe, expect, it } from "vitest";

import type { OneberthSession } from "./api";
import { mayBrowse, sessionOf } from "./session";

const PROVIDERS = [{ id: "google", title: "Google" }];

describe("sessionOf", () => {
  it.each<[string, OneberthSession["principal"], ReturnType<typeof sessionOf>["principal"]]>([
    ["nobody", null, null],
    [
      "a person",
      { kind: "user", id: "u1", name: "Ada", email: "ada@example.com", provider: "google" },
      { kind: "user", name: "Ada", email: "ada@example.com", provider: "google" },
    ],
    [
      "a service",
      { kind: "service", id: "s1", name: "ci" },
      { kind: "service", name: "ci", email: null, provider: null },
    ],
  ])("shows %s as the page shows who is in", (_who, principal, shown) => {
    const session = sessionOf({ mode: "providers", providers: PROVIDERS, principal, roles: [] });

    expect(session).toEqual({ public_url: location.origin, providers: PROVIDERS, principal: shown });
    expect(mayBrowse(session)).toBe(shown !== null);
  });
});
