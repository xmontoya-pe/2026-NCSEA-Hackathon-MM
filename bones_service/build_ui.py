"""Assemble static/index.html from the Bones UI and the Module 2 drawing pad.

    python -m bones_service.build_ui

Sources in static/src/:
  app.html                  the Bones UI with three markers: <!--PAD_CSS-->, <!--PAD_MARKUP-->, <!--PAD_JS-->
  pad/pad.verbatim.css      Module 2 CSS, copied unchanged from the handoff (section 7.4)
  pad/pad.verbatim.html     Module 2 markup (section 7.3)
  pad/pad.verbatim.js       Module 2 JavaScript (sections 7.5 to 7.8)
  pad/glue-pre.js           stand-ins for the parts of the ELF app that are not in the handoff
  pad/glue-post.js          opening a step 2 plan, lines of resistance, the 3D view
  pad/theme.css             the Bones palette for the pad
  step5.js                  step 5, client view: saves the pad state and opens /dashboard

When Module 2 changes, paste the new code into the pad/*.verbatim.* files and run this again.
The pad's CSS is scoped to #pad so it cannot restyle the rest of Bones, and its dark-mode rules are dropped
(Bones is light). Its JavaScript runs in its own function scope, exactly as in the ELF app.

Step 5: the "5 Client view" label in app.html's step nav becomes a #nav5 button, and step5.js is added
before </body>. If app.html already has a #nav5 element, it is left as is.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "static", "src")
SCOPE = "#pad"
STEP5_LABEL = '<span class="step later"><i>5</i>Client view</span>'
STEP5_BUTTON = '<button class="step" id="nav5" type="button"><i>5</i>Client view</button>'


def _blocks(css):
    """Split CSS into top-level (prelude, body) pairs; body is None for statements like @import."""
    out, i, n = [], 0, len(css)
    while i < n:
        j = i
        while j < n and css[j] not in "{;":
            j += 1
        if j >= n:
            break
        prelude = css[i:j].strip()
        if css[j] == ";":
            out.append((prelude, None))
            i = j + 1
            continue
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        out.append((prelude, css[j + 1:k - 1]))
        i = k
    return out


def _scope_selector(sel):
    sel = sel.strip()
    if not sel:
        return None
    if re.match(r':root\s*(:not\(\[data-theme="light"\]\)|\[data-theme="dark"\])', sel):
        return None                                  # dark theme: dropped
    if sel in (":root", "html", "body"):
        return SCOPE
    if sel == "*":
        return SCOPE + " *"
    sel = re.sub(r"^(html|body)\b\s*", "", sel)
    return "%s %s" % (SCOPE, sel)


def scope_css(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for prelude, body in _blocks(css):
        if body is None:
            out.append(prelude + ";")
        elif prelude.startswith("@media") and "prefers-color-scheme" in prelude and "dark" in prelude:
            continue
        elif prelude.startswith(("@media", "@supports", "@container")):
            inner = scope_css(body)
            if inner.strip():
                out.append("%s{%s}" % (prelude, inner))
        elif prelude.startswith("@"):
            out.append("%s{%s}" % (prelude, body))     # @keyframes, @font-face: unchanged
        else:
            sels = [s for s in (_scope_selector(x) for x in prelude.split(",")) if s]
            if sels:
                out.append("%s{%s}" % (",".join(dict.fromkeys(sels)), body.strip()))
    return "\n".join(out)


def read(*p):
    with open(os.path.join(SRC, *p), encoding="utf-8") as f:
        return f.read()


def add_step5(app):
    """Wire the step 5 nav item to the client dashboard (see the module docstring)."""
    if STEP5_LABEL in app:
        app = app.replace(STEP5_LABEL, STEP5_BUTTON)
    elif 'id="nav5"' not in app:
        raise SystemExit("app.html has no step 5 nav item: expected " + STEP5_LABEL)
    head, body, tail = app.rpartition("</body>")
    if not body:
        raise SystemExit("app.html is missing </body>")
    return head + '<script id="step5-js">\n' + read("step5.js") + "</script>\n" + body + tail


def build(app_html=None):
    app = app_html if app_html is not None else read("app.html")
    css = "<style id=\"pad-css\">\n" + scope_css(read("pad", "pad.verbatim.css")) + "\n" + read("pad", "theme.css") + "\n</style>"
    js = ("<script id=\"pad-js\">\n(function(){\n" + read("pad", "glue-pre.js") + "\n" +
          read("pad", "pad.verbatim.js") + "\n" + read("pad", "glue-post.js") + "\n})();\n</script>")
    markup = '<div id="pad">\n' + read("pad", "pad.verbatim.html") + "\n</div>"
    for mark, val in (("<!--PAD_CSS-->", css), ("<!--PAD_MARKUP-->", markup), ("<!--PAD_JS-->", js)):
        if mark not in app:
            raise SystemExit("app.html is missing " + mark)
        app = app.replace(mark, val)
    return add_step5(app)


if __name__ == "__main__":
    html = build()
    with open(os.path.join(HERE, "static", "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print("Wrote static/index.html (%d kB)" % (len(html) // 1024))
