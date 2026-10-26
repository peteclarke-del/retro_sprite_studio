# 8-Bit Sprite Studio

A native Linux pixel-art editor for classic computers and consoles. The standalone application runs on PyQt5 and uses Pillow for image import/export. It needs no network access, external service, project setup, or paid account.

## Run

Use a Python installation that includes **PyQt5** and **Pillow**:

```bash
python3 main.py
```

On Ubuntu/Debian, the distro Python packages are commonly available as `python3-pyqt5` and `python3-pil`. Run the focused graphics/data self-test and GUI initialization smoke test with:

```bash
python3 main.py --self-test
python3 main.py --smoke-test
```

The GUI smoke test requires a usable desktop display; the data self-test does not.

## Current editor

- Click-and-drag pencil and eraser, straight lines, flood fill, colour picker, rectangular selection, zoom, and up to 50 undo/redo steps.
- Canvas sizes from 1×1 to 256×256, multiple independently editable animation frames, adjustable playback speed, and animated GIF export.
- Shape-aware editable in-between frames: interpolates per-colour pixel positions between equally sized keyframes. This is a deliberately simple pixel correspondence technique, not a semantic motion solver; review the generated frames.
- Reusable tile shelf: select a canvas rectangle, capture it, then stamp a tile at the canvas centre.
- Import common PNG, GIF, BMP, JPEG, and WebP images with nearest-palette conversion. Transparency is preserved.
- Export the current frame as PNG, the animation as GIF, or the current frame as row-major 8-bit palette indices (`FF` represents transparent pixels).
- Portable, compressed native `.rsp` project stores profile, frames, animation speed, and tiles.

## Hardware profiles

Includes generic 16-colour artwork, BBC Micro/Electron MODE 1/2/5 presets, Commodore 64/128 hires and multicolour presets, ZX Spectrum attributes, Amstrad CPC MODE 0/1/2, MSX SCREEN 2, NES, Game Boy, Master System, SNES, and Mega Drive/Genesis presets. Changing the target converts all frames and tiles to the closest colours while retaining their canvas dimensions. Supported cell and per-sprite colour limits are enforced when converting and generating tween frames.

Profiles describe practical drawing/conversion constraints; they are not a complete emulation of each system's hardware. For example, C64 multicolour double-width pixel display is not emulated, CPC MODE 0 uses a palette subset, MSX SCREEN 2's real 8×1 attribute bands are approximated as 8×8 editor cells, and the NES, Master System, SNES, and Mega Drive currently use approximation palettes rather than full target-colour encoders. Transparency is represented in PNG/GIF and as `FF` in raw binary export.

## Source generators and next steps

This first usable release deliberately focuses on drawing, basic frame animation, conversion, image formats, and native project files. Target-dialect BASIC, assembly, and C source generation, tile-map authoring, richer machine-accurate hardware packing, semantic tween controls, and desktop packaging are not implemented yet. Raw binary export is a simple palette-index dump; it is **not** a machine-ready hardware sprite encoding.
