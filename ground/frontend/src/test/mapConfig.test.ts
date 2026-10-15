import { expect, it } from "vitest";

import { blankStyle } from "../components/mapStyle";

it("uses a blank local MapLibre style with no remote sources", () => {
  expect(blankStyle.sources).toEqual({});
  expect(blankStyle.sprite).toBeUndefined();
  expect(blankStyle.glyphs).toBeUndefined();
  expect(blankStyle.layers[0].type).toBe("background");
});
