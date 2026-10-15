# models/ — Machine Learning Model Weights

Stores all trained YOLO model weights used for person detection.

## Structure

```
models/
├── manifest.yaml          Registry of all available models with metadata
├── yolo11n.pt             Pretrained YOLOv11-nano base (used for transfer learning)
├── artifacts/             Exported / converted model formats (ONNX, TFLite)
└── development/           Experimental checkpoints not yet in production
```

> **Production model** is at:
> `data/training/stage13sim_nadir_person_v3/training_runs/primary/weights/best.pt`
>
> The `yolo11n.pt` here is the **base pretrained** model, not the trained rescue model.
