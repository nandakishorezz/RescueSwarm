"""Observer-only evaluation. This namespace is forbidden to autonomy code."""

from .perception import (
    GazeboSegmentationSource,
    PerceptionEvaluator,
    PerceptionFrameEvaluation,
    WorldTruthImageBBox,
    WorldTruthSegmentationFrame,
    WorldTruthVisibilityFrame,
    compact_visibility,
    semantic_mask_boxes,
)

__all__ = [
    "GazeboSegmentationSource", "PerceptionEvaluator", "PerceptionFrameEvaluation",
    "WorldTruthImageBBox", "WorldTruthSegmentationFrame", "WorldTruthVisibilityFrame",
    "compact_visibility", "semantic_mask_boxes",
]
