# Bones sketch service

Sketches are read by Claude through the Anthropic API (`sketch_reader/reader.py`); each new sketch is one API call. Everything after the reading (rebuilding the plan to scale, checks, the Module 2 file, the drawing pad) is plain Python and JavaScript with no AI.

The Bones web UI (steps 1 and 2) in front of Sketch Reader. Sketch Reader's code is unchanged; the service calls its `read_with_claude`, `write_all` and `plan_json`, then adds a Module 2 project file.

## Run it

1. Set up Sketch Reader as in its README: Python 3.10+, then `pip install -r requirements.txt`. The service needs nothing else.
2. **API key:** open `.env` (next to `run_bones.bat`) in Notepad, replace `sk-ant-your-key-here` with your key from https://console.anthropic.com, and save. A key already set as the `ANTHROPIC_API_KEY` environment variable also works and takes priority. Keep `.env` private; `.gitignore` already leaves it out of git.
3. Double-click `run_bones.bat`, or run `python -m bones_service.server`. It opens http://127.0.0.1:8765.
4. Without an API key you can still press **Try the example sketch**, which uses the saved reading in `examples`.

Options: `--port 9000`, `--out C:\path\to\outputs`, `--host 0.0.0.0` to let colleagues on your network use it. The API key never leaves the server.

## What happens

1. **Step 1:** the photo is uploaded and Sketch Reader reads it (15 to 60 seconds). Stories and story height are optional.
2. **Step 2:** the clean plan, Sketch Reader's overlay of the reading on your photo, a 3D frame, what was found, and the hand-off checks.
   - Changing stories or story height re-runs from the saved reading, with no new read.
   - **Download Module 2 file** saves `<name>-module2.json`. Open it in the drawing pad with **Open…** to add braced frames and load areas.
3. **Step 3, drawing pad, loads and hazards** (one screen):
   - **Site hazards, ASCE 7:** type the site address (or latitude and longitude), edition, Risk Category and site class, then **Pull from the ASCE Hazard Tool**. Every value stays editable and shows where it came from.
     - With `ASCE_HAZARD_TOKEN` in `.env`: S<sub>DS</sub>, S<sub>D1</sub> and SDC, wind speed V, and ground snow p<sub>g</sub> come from the ASCE Hazard Tool API (`https://api-hazard.asce.org/v1`, `/seismic`, `/wind`, `/snow`). ASCE issues keys per company by quote; the default limit is 1,000 requests a month.
     - Without it: seismic comes from the USGS design-maps service (the Hazard Tool's own seismic source, no key); type wind and snow from https://ascehazardtool.org.
     - Addresses are turned into latitude and longitude by the US Census geocoder (no key).
   - **Roof snow:** SL = p<sub>f</sub> = 0.7 C<sub>e</sub> C<sub>t</sub> p<sub>g</sub> (ASCE 7-22 Eq. 7.3-1), from p<sub>g</sub> and the C<sub>e</sub> and C<sub>t</sub> menus. Minimum roof snow, drift, sliding and rain-on-snow are left to design.
   - **Gravity loads by load area:** one row per load area drawn on the pad, for the pad's current level: LL and SDL (editable; defaults 50 / 15 psf on floors, L<sub>r</sub> 20 / 20 psf at the roof) and SL at the roof. **Copy to every floor level** copies the rows.
   - S<sub>DS</sub>, S<sub>D1</sub> and I<sub>e</sub> are also written to the project's `sds`, `sd1` and `ie`, the fields the ELF app's module 1 reads. Hazards, snow and loads are saved under `bones.hazard`, `bones.snow` and `bones.loads`.
   - **Drawing pad:** **Open in the drawing pad** on step 2 loads the plan into Module 2's drawing pad (Lucas's code from the plan-import handoff, unchanged).
   - Draw lines of resistance (walls or frames; the material is chosen in module 4) end to end, load areas by level, and edit the outline, with the pad's own snapping, undo, dimensions, C.M. / C.R. markers and frame tables.
   - **Lines of resistance** (above the pad) groups the frames by line in X and Y and says whether there are enough lines to resist torsion: at least one line in each direction and at least three in total.
   - Lines of resistance are drawn as shaded walls, in plan and in 3D (one panel per story), with no braces, so the drawing does not assume steel. The pad still keeps a stiffness area A per line to share load between lines until module 4 sets the material.
   - The 3D view draws every level and every line of resistance. Drag to rotate, Shift-drag to pan, click then scroll to zoom.
   - Everything is saved in the browser as you draw. Going back to step 2 and changing stories or snow keeps the frames and load areas. **Download file** saves the project JSON that the ELF app opens with **Open…**.
4. **Step 4, structural system** (a stand-in until the real module 4 is in): **Compare structural systems** on step 3 opens a side-by-side comparison of steel special concentrically braced frames, special reinforced concrete shear walls, and light-frame wood shear walls.
   - Each card shows R, Ω<sub>0</sub> and C<sub>d</sub> and the height limit for the site's SDC (ASCE 7-22 Table 12.2-1), the seismic weight W (floor area × stories × self-weight), and C<sub>s</sub> and base shear V at T<sub>a</sub> = 0.02 h<sub>n</sub><sup>0.75</sup>, using S<sub>DS</sub>, S<sub>D1</sub> and I<sub>e</sub> from step 3. Without hazards it assumes S<sub>DS</sub> 1.0 and S<sub>D1</sub> 0.6.
   - Self-weight, cost, schedule and embodied carbon are placeholder values (`SYSTEMS` in `app.html`).
   - A system over its height limit, or wood over 5 stories, is marked not permitted.
   - The chosen system is saved under `bones.material` (with `placeholder: true`) and goes out with **Download file**.
5. All Sketch Reader files (summary, plan JSON, CSV, DXF, SVG, overlay, reading) are still written to `outputs/<name>/` and linked on step 2.

## The Module 2 file (`bones_service/convert.py`)

Follows section 4 of the Module 2 plan-import handoff:

- Lengths converted to feet; corners counter-clockwise from the lower-left at (0, 0); repeated and mid-edge corners removed. Each of these changes is listed on step 2.
- `levels`: one entry per story, cumulative heights, `psf` 150 as a placeholder for module 3, `la` false.
- `plan.lfrs` is empty: Sketch Reader does not read braced frames.
- Grids, columns, cores, floor area and Sketch Reader's warnings ride along under a `bones` key, which Module 2 ignores.

Checks before hand-off (section 4.5): at least 4 corners, every edge horizontal or vertical, clean corners, no self-intersection, origin at (0, 0), and whether the written dimensions agree.
- An angled edge or a self-crossing outline blocks the download; the drawing pad cannot use them.
- Dimensions that disagree, missing stories, and missing braced frames are shown as warnings and do not block it.

## The drawing pad inside Bones (`bones_service/build_ui.py`)

`static/index.html` is assembled from `static/src/`: the Bones UI (`app.html`) plus the Module 2 code copied unchanged from the handoff (`pad/pad.verbatim.css`, `.html`, `.js`). When Lucas updates Module 2, paste the new code into those three files and run `python -m bones_service.build_ui`.

- The pad's CSS is scoped to `#pad` so it cannot restyle the rest of Bones, and its dark-mode rules are dropped. `pad/theme.css` gives it the Bones colors.
- `pad/glue-pre.js` stands in for the parts of the ELF app the handoff leaves out (modules 1, 3 and 9 to 12, and the 3D code).
- `pad/glue-post.js` opens a step 2 plan (the handoff's section 5A path), computes the lines of resistance, and draws the 3D view. It also swaps the pad's "Braced frames" wording for "Lines of resistance" as the pad renders (the `WORDS` list), and `pad/theme.css` draws them as shaded walls, so the verbatim files stay unchanged.
- With no module 3 in Bones yet, a level uses its load areas whenever it has some.

## Endpoints

| Method | Path | Body / query | Does |
|---|---|---|---|
| GET | `/` | | The UI |
| GET | `/api/health` | | Whether an API key and the example are available |
| POST | `/api/read` | `{image, filename, levels?, storey_height_ft?, model?}` | Reads a new sketch |
| POST | `/api/rebuild` | `{job, levels?, storey_height_ft?, snow?}` | Re-runs from the saved reading |
| POST | `/api/example` | `{levels?, storey_height_ft?}` | Runs the bundled example |
| POST | `/api/geocode` | `{address}` | Latitude and longitude for a US address |
| POST | `/api/hazard` | `{lat, lon, edition, risk_category, site_class}` | Seismic, wind and ground snow (see above) |
| GET | `/api/file` | `?job=&name=` | Downloads one output file |

`snow` is `{pg, Ce, Ct}` (psf and the two factors) and is accepted by read, rebuild and example. `image` is base64 or a data URL (JPG, PNG or WebP, 20 MB max). Errors come back as `{"error": "plain message"}`.

## Tested

With the example reading: 6 corners, 80,000 sf, 1,400 ft perimeter, all 6 dimensions agree; corners (0,0) (500,0) (500,100) (300,100) (300,200) (0,200). Also tested: a metric sketch with a mid-edge corner and a chain that does not add up (converted, cleaned, flagged), and an angled edge (download blocked). Drawing pad, in headless Chromium: frames drawn by clicking land column to column with the right labels; lines of resistance and the torsion check update with each frame; load areas covering the notched plan give W = 11,000 k per level (60,000 sf at 150 psf + 20,000 sf at 100 psf); copy to all levels works; frames survive going back to step 2 and changing stories; the pad restores after a page reload; the downloaded project keeps frames, load areas and snow. Hazard panel: parsing tested against the response shapes in ASCE's API documentation (snow by Risk Category, the case-study sentinel, seismic sds / sd1 / sdc, wind speed); the full screen tested in Chromium with those replies standing in. Not tested here, because this workspace has no internet: a live read with Claude, live ASCE Hazard Tool, USGS and Census calls. Check the first real pull against ascehazardtool.org.
