"""Sketch Reader: photo of a floor-plan sketch in, text files out.

Window:        python app.py
Command line:  python app.py sketch.png --out C:\\path\\to\\folder [--levels 6 --storey-height 12]
               python app.py sketch.png --reading sketch-reading.json   (re-run without calling Claude)
"""
import argparse
import json
import os
import sys
import threading

from sketch_reader.reader import DEFAULT_MODEL, read_with_claude
from sketch_reader.outputs import write_all

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "settings.json")
MODELS = [DEFAULT_MODEL, "claude-opus-5-5"]


def default_out():
    aisummit = os.path.join(os.path.expanduser("~"), "AISUMMIT")
    return os.path.join(aisummit, "outputs") if os.path.isdir(aisummit) else os.path.join(HERE, "outputs")


def load_settings():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    try:
        with open(SETTINGS, "w") as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass


def run(image, out_dir, reading=None, model=DEFAULT_MODEL, levels=None, storey_height=None, api_key=None, log=print):
    if not os.path.isfile(image):
        raise RuntimeError("Photo not found: " + image)
    if reading:
        log("Using saved reading: " + os.path.basename(reading))
        with open(reading) as f:
            raw = json.load(f)
    else:
        log("Reading the sketch with %s (usually 15-60 seconds)..." % model)
        raw = read_with_claude(image, model=model, api_key=api_key)
    log("Rebuilding the plan from the dimensions and writing files...")
    folder, paths, B = write_all(image, raw, out_dir, levels, storey_height)
    P = B["props"]
    if P:
        log("Floor area %s %s, perimeter %s %s, %d corners, %d dimensions." % (
            format(round(P["area"], 2), ","), {"ft": "ft2", "m": "m2", "mm": "mm2"}[B["units"]],
            format(round(P["perimeter"], 2), ","), B["units"], P["vertex_count"], len(B["rd"]["dims"])))
    for w in B["plan"]["warnings"] + B["rd"]["warnings"]:
        log("Check: " + w)
    log("Saved %d files to %s" % (len(paths), folder))
    return folder, paths


# ---------------------------------------------------------------- command line
def cli(argv):
    ap = argparse.ArgumentParser(description="Turn a floor-plan sketch photo into text files.")
    ap.add_argument("image", help="photo of the sketch (JPG or PNG)")
    ap.add_argument("--out", default=default_out(), help="output folder (default: %(default)s)")
    ap.add_argument("--reading", help="saved *-reading.json to reuse instead of calling Claude")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--levels", type=int, help="number of storeys (overrides the sketch)")
    ap.add_argument("--storey-height", type=float, help="storey height in the sketch's units")
    a = ap.parse_args(argv)
    try:
        run(a.image, a.out, a.reading, a.model, a.levels, a.storey_height)
    except Exception as e:  # show a plain message, not a traceback
        print("Error: %s" % e)
        sys.exit(1)


# ---------------------------------------------------------------- window
def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog

    s = load_settings()
    root = tk.Tk()
    root.title("Sketch Reader")
    root.minsize(640, 460)
    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    v_img = tk.StringVar()
    v_out = tk.StringVar(value=s.get("out", default_out()))
    v_model = tk.StringVar(value=s.get("model", DEFAULT_MODEL))
    v_levels = tk.StringVar()
    v_height = tk.StringVar()
    v_key = tk.StringVar(value="")
    v_reuse = tk.BooleanVar(value=False)

    def pick_img():
        p = filedialog.askopenfilename(title="Choose the sketch photo", initialdir=s.get("img_dir", ""),
                                       filetypes=[("Images", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if p:
            v_img.set(p)
            s["img_dir"] = os.path.dirname(p)

    def pick_out():
        p = filedialog.askdirectory(title="Choose the output folder", initialdir=v_out.get())
        if p:
            v_out.set(p)

    r = 0
    ttk.Label(frm, text="Sketch photo").grid(row=r, column=0, sticky="w", pady=4)
    ttk.Entry(frm, textvariable=v_img).grid(row=r, column=1, sticky="ew", padx=6)
    ttk.Button(frm, text="Browse...", command=pick_img).grid(row=r, column=2)
    r += 1
    ttk.Label(frm, text="Save files to").grid(row=r, column=0, sticky="w", pady=4)
    ttk.Entry(frm, textvariable=v_out).grid(row=r, column=1, sticky="ew", padx=6)
    ttk.Button(frm, text="Browse...", command=pick_out).grid(row=r, column=2)
    r += 1
    opts = ttk.Frame(frm)
    opts.grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 4))
    ttk.Label(opts, text="Storeys").pack(side="left")
    ttk.Entry(opts, textvariable=v_levels, width=6).pack(side="left", padx=(4, 14))
    ttk.Label(opts, text="Storey height").pack(side="left")
    ttk.Entry(opts, textvariable=v_height, width=8).pack(side="left", padx=(4, 14))
    ttk.Label(opts, text="Model").pack(side="left")
    ttk.Combobox(opts, textvariable=v_model, values=MODELS, width=20).pack(side="left", padx=4)
    r += 1
    keyrow = ttk.Frame(frm)
    keyrow.grid(row=r, column=0, columnspan=3, sticky="ew", pady=4)
    ttk.Label(keyrow, text="API key").pack(side="left")
    ttk.Entry(keyrow, textvariable=v_key, show="•", width=40).pack(side="left", padx=4)
    ttk.Label(keyrow, text="(leave empty to use ANTHROPIC_API_KEY)", foreground="#666").pack(side="left")
    r += 1
    ttk.Checkbutton(frm, text="Reuse the saved reading for this photo (no new Claude call, e.g. after changing storeys)",
                    variable=v_reuse).grid(row=r, column=0, columnspan=3, sticky="w")
    r += 1
    btns = ttk.Frame(frm)
    btns.grid(row=r, column=0, columnspan=3, sticky="w", pady=10)
    go = ttk.Button(btns, text="Read sketch and save files")
    go.pack(side="left")
    open_btn = ttk.Button(btns, text="Open output folder", state="disabled")
    open_btn.pack(side="left", padx=8)
    r += 1
    logbox = tk.Text(frm, height=12, wrap="word", state="disabled", font=("Consolas", 9))
    logbox.grid(row=r, column=0, columnspan=3, sticky="nsew")
    frm.rowconfigure(r, weight=1)
    last = {"folder": None}

    def log(msg):
        def w():
            logbox.configure(state="normal")
            logbox.insert("end", msg + "\n")
            logbox.see("end")
            logbox.configure(state="disabled")
        root.after(0, w)

    def num_or_none(v, cast):
        try:
            return cast(v) if v.strip() else None
        except ValueError:
            return None

    def work():
        img, out = v_img.get().strip(), v_out.get().strip()
        try:
            if not os.path.isfile(img):
                raise RuntimeError("Choose a sketch photo first.")
            reading = None
            if v_reuse.get():
                stem = os.path.splitext(os.path.basename(img))[0]
                reading = os.path.join(out, stem, stem + "-reading.json")
                if not os.path.isfile(reading):
                    raise RuntimeError("No saved reading for this photo yet. Untick 'Reuse' to read it.")
            folder, _ = run(img, out, reading, v_model.get().strip() or DEFAULT_MODEL,
                            num_or_none(v_levels.get(), int), num_or_none(v_height.get(), float),
                            v_key.get().strip() or None, log)
            last["folder"] = folder
            root.after(0, lambda: open_btn.configure(state="normal"))
        except Exception as e:
            log("Error: %s" % e)
        finally:
            root.after(0, lambda: go.configure(state="normal"))

    def start():
        go.configure(state="disabled")
        s.update(out=v_out.get(), model=v_model.get())
        save_settings(s)
        threading.Thread(target=work, daemon=True).start()

    def open_folder():
        f = last["folder"]
        if f and os.path.isdir(f):
            if sys.platform.startswith("win"):
                os.startfile(f)
            elif sys.platform == "darwin":
                os.system('open "%s"' % f)
            else:
                os.system('xdg-open "%s"' % f)

    go.configure(command=start)
    open_btn.configure(command=open_folder)
    log("Choose a photo of a floor-plan sketch, then press 'Read sketch and save files'.")
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli(sys.argv[1:])
    else:
        gui()
