export interface CameraDetection {
  class_id: number;
  class?: string;
  display_label?: string;
  conf: number;
  /** [x, y, width, height] in detection_image_width/height pixels. */
  bbox: [number, number, number, number];
  geolocation?: {
    status: "APPROXIMATE" | "UNAVAILABLE";
    target_id?: string;
    latitude?: number;
    longitude?: number;
    uncertainty_m?: number;
    quality?: string;
    reason?: string | null;
  };
}

export function detectionLabel(detection: CameraDetection): string {
  return detection.display_label ?? detection.class ?? `class ${detection.class_id}`;
}

export function detectionStyle(
  detection: CameraDetection,
  sourceWidth: number,
  sourceHeight: number,
): Record<string, string> {
  const [x, y, width, height] = detection.bbox;
  return {
    left: `${(x / sourceWidth) * 100}%`,
    top: `${(y / sourceHeight) * 100}%`,
    width: `${(width / sourceWidth) * 100}%`,
    height: `${(height / sourceHeight) * 100}%`,
  };
}

export function validDetections(value: unknown): CameraDetection[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is CameraDetection => {
    const detection = item as Partial<CameraDetection>;
    return typeof detection.class_id === "number"
      && typeof detection.conf === "number"
      && Array.isArray(detection.bbox)
      && detection.bbox.length === 4
      && detection.bbox.every((coordinate) => typeof coordinate === "number");
  });
}
