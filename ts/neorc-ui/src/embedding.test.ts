import { describe, expect, it } from "vitest";

import { isEmbedded } from "./embedding";

describe("isEmbedded", () => {
  it.each([
    ["?embedded", true],
    ["?embedded=1", true],
    ["?x=1&embedded", true],
    ["", false],
    ["?", false],
    ["?embed", false],
    ["?x=embedded", false],
  ])("%j is embedded: %s", (search, expected) => {
    expect(isEmbedded(search)).toBe(expected);
  });
});
