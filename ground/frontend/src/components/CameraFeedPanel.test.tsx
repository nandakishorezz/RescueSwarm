import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useGcsStore } from "../stores/gcsStore";
import { CameraFeedPanel } from "./CameraFeedPanel";

const frame = (sourceTs: number, detections: unknown[]) => ({
  image_b64: "jpeg", source_ts: sourceTs, frame_id: "camera:7",
  image_width: 640, image_height: 400, detection_image_width: 1280, detection_image_height: 800,
  frame_count: 7, decoded_frame_count: 7, inference_count: 7, model_status: "READY",
  detections,
});

describe("CameraFeedPanel", () => {
  beforeEach(() => useGcsStore.setState({ cameraFrame: null }));
  afterEach(cleanup);

  it("renders same-frame person boxes and labels", () => {
    useGcsStore.setState({ cameraFrame: frame(Date.now() / 1000, [{ class_id: 0, class: "item", display_label: "person", conf: .875, bbox: [320, 200, 160, 80] }]) });
    render(<CameraFeedPanel />);
    const box = screen.getByTestId("camera-detection");
    expect(box.style.left).toBe("25%");
    expect(screen.getByText("person 87.5%")).toBeInTheDocument();
  });

  it("clears overlays and detection text for a stale frame", () => {
    useGcsStore.setState({ cameraFrame: frame(Date.now() / 1000 - 10, [{ class_id: 0, display_label: "person", conf: .9, bbox: [1, 2, 3, 4] }]) });
    render(<CameraFeedPanel />);
    expect(screen.queryByTestId("camera-detection")).toBeNull();
    expect(screen.queryByText(/person 90.0%/)).toBeNull();
    expect(screen.getByText(/STALE/)).toBeInTheDocument();
  });
});
