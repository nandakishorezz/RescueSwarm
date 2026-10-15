import { describe, expect, it } from "vitest";

import { detectionLabel, detectionStyle, validDetections } from "./cameraDetections";

describe("camera detections", () => {
  const person = { class_id: 0, class: "item", display_label: "person", conf: 0.875, bbox: [320, 200, 160, 80] as [number, number, number, number] };

  it("uses the explicit display label without mutating the model label", () => {
    expect(person.class).toBe("item");
    expect(detectionLabel(person)).toBe("person");
  });

  it("scales source-pixel boxes as percentages for a resized image", () => {
    expect(detectionStyle(person, 1280, 800)).toEqual({ left: "25%", top: "25%", width: "12.5%", height: "10%" });
  });

  it("accepts valid detections and safely rejects malformed or empty payloads", () => {
    expect(validDetections([person])).toEqual([person]);
    expect(validDetections([])).toEqual([]);
    expect(validDetections([{ class_id: 0, conf: .8, bbox: [1, 2] }])).toEqual([]);
  });
});
