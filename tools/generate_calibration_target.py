"""Generate a printable checkerboard SVG with explicit physical dimensions."""

from __future__ import annotations

import argparse
from pathlib import Path


def generate_checkerboard(columns: int, rows: int, square_mm: float, output: Path) -> None:
    if columns < 3 or rows < 3 or square_mm <= 0:
        raise ValueError("Inner-corner dimensions must be >=3 and square size must be positive")
    squares_x, squares_y = columns + 1, rows + 1
    width, height = squares_x * square_mm, squares_y * square_mm
    rectangles = []
    for row in range(squares_y):
        for column in range(squares_x):
            if (row + column) % 2 == 0:
                rectangles.append(
                    f'<rect x="{column * square_mm}mm" y="{row * square_mm}mm" '
                    f'width="{square_mm}mm" height="{square_mm}mm" fill="black"/>'
                )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height + 12}mm" '
        f'viewBox="0 0 {width} {height + 12}"><rect width="100%" height="100%" fill="white"/>'
        + "".join(rectangles)
        + f'<text x="0" y="{height + 8}" font-family="sans-serif" font-size="4">'
          f'{columns}x{rows} INNER CORNERS · NOMINAL {square_mm:g} mm squares · MEASURE AFTER PRINTING</text></svg>'
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--columns", type=int, required=True, help="Inner-corner columns")
    parser.add_argument("--rows", type=int, required=True, help="Inner-corner rows")
    parser.add_argument("--square-size-mm", type=float, required=True)
    parser.add_argument("--output", type=Path, default=Path("calibration/checkerboard.svg"))
    args = parser.parse_args()
    generate_checkerboard(args.columns, args.rows, args.square_size_mm, args.output)
    print(args.output)
    print("Measure the printed squares and pass the measured size to calibrate_camera.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

