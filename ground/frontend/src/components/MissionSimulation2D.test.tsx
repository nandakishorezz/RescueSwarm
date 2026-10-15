import { render } from "@testing-library/react";
import { MissionSimulation2D } from "./MissionSimulation2D";
import { describe, test, expect } from "vitest";

describe("MissionSimulation2D", () => {
  test("Renders static scenario geometry perfectly BEFORE START SIMULATION with NO live telemetry", () => {
    // 1. Mock scenario with valid polygon, 2 survivors, and drone start
    const mockScenario = {
      polygon: [
        [77.123, 12.345], // [lng, lat]
        [77.124, 12.345],
        [77.124, 12.346],
        [77.123, 12.346]
      ],
      survivors: [
        { id: "S1", latitude: 12.3452, longitude: 77.1232 },
        { id: "S2", latitude: 12.3458, longitude: 77.1238 }
      ],
      startPosition: { lat: 12.3451, lng: 77.1231 }
    };

    // 2. Render component with NO backend connection (gcsStore is effectively empty by default)
    const { container } = render(<MissionSimulation2D scenario={mockScenario} />);

    // 3. Assert polygon is rendered
    // The polygon maps to the SVG <polygon> tag in the DOM
    const polygon = container.querySelector("polygon");
    expect(polygon).toBeInTheDocument();
    
    // Check it has a points attribute populated with actual mapped coordinates
    const pointsAttr = polygon?.getAttribute("points");
    expect(pointsAttr).toBeTruthy();
    expect(pointsAttr?.length).toBeGreaterThan(10); // Needs to have multiple coordinate pairs

    // 4. Assert 2 survivors are rendered
    // The markers use the CSS class 'survivor-marker'
    const survivorMarkers = container.querySelectorAll(".survivor-marker");
    expect(survivorMarkers).toHaveLength(2);

    // 5. Assert drone start marker is rendered
    const startMarker = container.querySelector(".drone-start-marker");
    expect(startMarker).toBeInTheDocument();

    // 6. Assert live telemetry drone is NOT rendered
    const liveDrone = container.querySelector(".live-drone");
    expect(liveDrone).not.toBeInTheDocument();
  });
});
