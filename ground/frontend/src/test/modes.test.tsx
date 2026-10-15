import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it } from "vitest";

import { ModeNav } from "../components/ModeNav";
import { useGcsStore } from "../stores/gcsStore";

beforeEach(() => useGcsStore.setState({ mode: "SETUP", competitionMode: false }));

it("switches among application modes", () => {
  render(<ModeNav />);
  fireEvent.click(screen.getByText("MISSION"));
  expect(useGcsStore.getState().mode).toBe("MISSION");
});

it("hides engineering mode during competition", () => {
  useGcsStore.setState({ competitionMode: true });
  render(<ModeNav />);
  expect(screen.queryByText("ENGINEERING")).not.toBeInTheDocument();
});

