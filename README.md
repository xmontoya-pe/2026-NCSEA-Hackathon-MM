# Sketch Reader

Photo of a hand-drawn floor plan in, text files out.

You pick a photo of a sketch. Claude reads the outline and the written dimensions. The app rebuilds the plan to scale from those dimensions and saves:

| File | What it is |
|---|---|
| `<name>-summary.txt` | Everything in plain text: dimensions, corners, edges as vectors, floor area, perimeter, centroid, second moments, levels, checks |
| `<name>-plan.json` | The same data in a structured form (for Grasshopper, Python, the Rhino import) |
| `<name>-edges.csv` | One row per edge, for Excel |
| `<name>-plan.dxf` | Lines, corner and length labels, level outlines; opens in Rhino or AutoCAD |
| `<name>-plan.svg` | A labelled, to-scale drawing |
| `<name>-overlay.png` | Your photo with what was read drawn on top, to check the reading |
| `<name>-reading.json` | Claude's raw reading, so you can re-run without calling Claude again |

Files go into a folder named after the photo, inside the output folder you choose.

## Setup (once)

1. Install Python 3.10 or newer from python.org. On Windows, tick "Add python.exe to PATH" in the installer.
2. Open a terminal in this folder and install the two libraries:
   ```
   pip install -r requirements.txt
   ```
3. Get an API key from https://console.anthropic.com (Settings > API keys) and save it:
   ```
   setx ANTHROPIC_API_KEY "sk-ant-..."
   ```
   Close and reopen the terminal afterwards. You can also paste the key into the app each time instead.

## Use it

**Window:** double-click `run_app.bat` (or run `python app.py`).
1. Browse to the sketch photo.
2. Choose where to save the files. It defaults to `AISUMMIT\outputs` in your user folder when that folder exists.
3. Optionally enter the number of storeys and the storey height, if they aren't written on the sketch.
4. Press **Read sketch and save files**. Reading takes about 15 to 60 seconds.

To change the storeys later without paying for another reading, tick **Reuse the saved reading** and run it again.

**Command line:**
```
python app.py sketch.png --out C:\Users\you\AISUMMIT\outputs
python app.py sketch.png --levels 6 --storey-height 12
python app.py sketch.png --reading outputs\sketch\sketch-reading.json   (no Claude call)
```

**Try it without an API key:** the `examples` folder has your notched sketch and a saved reading:
```
python app.py examples\notched-sketch.png --reading examples\notched-sketch-reading.json
```

## How it works

1. `sketch_reader/reader.py` sends the photo to Claude with instructions to return the outline corners, every written dimension (with the two points it measures between), grid lines, columns, cores, storeys and notes, as JSON.
2. `sketch_reader/geometry.py` rebuilds the plan. Sketches are rarely to scale, so it ignores the drawn proportions. It splits the plan into spans between the x and y lines of the drawing and sets each span from the written dimensions. Spans with no dimension are scaled from the drawing. When a chain of dimensions doesn't add up to the overall, it tells you which ones disagree.
3. `sketch_reader/outputs.py` writes the files.

## Getting good readings

- Write a dimension on every span, and give chains an overall. The overall lets the app check the chain.
- Draw the slab edge with the heaviest line.
- Use ' for feet, or write "m" / "mm" once.
- Write the storey count and height on the sketch, e.g. "6 STOREYS, F-F 12'".
- Always glance at `overlay.png` to confirm the reading before using the numbers.

## Open it in Rhino

Run `import_sketch_plan.py` in Rhino (Rhino 8: `ScriptEditor`; Rhino 7: `RunPythonScript`) and pick the `-plan.json` file. It builds layers, the outline, edge lines carrying their data as Attribute User Text, real dimensions, slabs per level, and floor properties as Document User Text.

## Settings

- Model: `claude-sonnet-5-5` by default. Pick `claude-opus-5-5` in the window, or pass `--model`, for messy sketches.
- Tolerances (how close lines must be to count as the same line, how far off horizontal an edge can be) are at the top of `sketch_reader/geometry.py`.

## Seismic ELF API

`api.py` is a FastAPI service over `elf_engine.py`, the ASCE 7-22 ELF engine for steel braced frames or concrete shear walls (one engine, picked by the project's `lateral_system`).

```
pip install -r requirements-api.txt
uvicorn api:app --reload
```

Open http://127.0.0.1:8000/docs. `POST /analyze` takes the project file the Bones drawing pad downloads. For shear walls, add a `shear_walls` block with R, C<sub>d</sub>, f′c and the other wall inputs; wall thicknesses come from the pad. Tests: `python -m pytest tests` (the shear wall results are checked against the ShearWall dashboard, `tests/test_shearwall.py`).
