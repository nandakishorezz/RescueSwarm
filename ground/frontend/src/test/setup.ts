import "@testing-library/jest-dom/vitest";

// MapLibre creates its worker URL while the module is imported. JSDOM does not
// implement the object URL API, so provide the browser contract for UI tests.
if (!URL.createObjectURL) URL.createObjectURL = () => "blob:rescueswarm-test-worker";
if (!URL.revokeObjectURL) URL.revokeObjectURL = () => undefined;
