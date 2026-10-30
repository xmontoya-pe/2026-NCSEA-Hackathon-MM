/* ---- Bones glue, part 2: open a plan from step 2, lines of resistance, and a 3D view ---- */
function bonesAfterUpdate(){
  // with no module 3 in Bones, a level uses its load areas whenever it has some
  (S.levels || []).forEach((L, j) => { L.la = ((S.plan.lv[j] || {}).la || []).length > 0; });
  save();
  if(!bonesReady) return;
  bonesLines();
  render3D();
  renderLoads();
}
function bonesOpen(project){
  const d = JSON.parse(JSON.stringify(project));
  const same = S.bones && d.bones && S.bones.source && d.bones.source && S.bones.source.image === d.bones.source.image &&
               JSON.stringify(S.plan.verts) === JSON.stringify(d.plan.verts) && S.plan.closed;
  if(same){
    // same plan as before: keep the frames and load areas drawn here, take the new stories and snow
    const old = S.levels;
    S.levels = d.levels.map((L, j) => Object.assign({}, L, {name: (old[j] || {}).name || ''}));
    S.n = S.levels.length; S.project = d.project || S.project;
    const keep = {hazard: (S.bones || {}).hazard, snow: (S.bones || {}).snow, loads: (S.bones || {}).loads, material: (S.bones || {}).material};
    S.bones = Object.assign({}, d.bones, ...Object.keys(keep).filter(k => keep[k]).map(k => ({[k]: keep[k]})));
    ensureLevels(S);
  } else {
    const sys = S.lateral_system;   // a new plan keeps the lateral system picked on step 3, unless the file has one
    S = normalize(Object.assign(fresh(), d)); S.n = S.levels.length;
    if(!d.lateral_system && sys) S.lateral_system = sys;
  }
  renderInputs(); renderT1(); renderT3(); update(); planReset(); save();
  return same;
}

/* Gravity loads by load area (occupancy, LL, SDL; SL at the roof), kept in S.bones.loads[level][id].
   The occupancy keys come from /api/occupancies (window.BonesOccupancies); the gravity optimizer
   uses the occupancy, not the LL and SDL typed here. */
function bonesData(){ S.bones = S.bones || {}; S.bones.loads = S.bones.loads || {}; return S.bones; }
function loadsAt(lv){ const B = bonesData(); return (B.loads[lv] = B.loads[lv] || {}); }
function loadDefaults(lv){ return lv === nLv() ? {LL: 20, SDL: 20} : {LL: 50, SDL: 15}; }
const occList = () => (window.BonesOccupancies || []).filter(o => !o.is_roof);
const occName = k => k.replace(/_/g, ' ');
function occCell(id, v){
  const opts = occList();
  if(!opts.length) return window.BonesOccupancies ? '<span class="b-warn">unavailable: start Bones with python -m bones_service.api</span>' : '<span class="muted">loading…</span>';
  return `<select data-occ="${esc(id)}" aria-label="Occupancy for area ${esc(id)}" style="height:36px;border:1px solid var(--line2);border-radius:8px;padding:0 8px">
    <option value=""${v.occupancy ? '' : ' selected'}>Choose…</option>${opts.map(o =>
    `<option value="${esc(o.key)}"${o.key === v.occupancy ? ' selected' : ''}>${esc(occName(o.key))} (${fmt(o.ll_psf, 0)}${o.partitions_psf ? ' + ' + fmt(o.partitions_psf, 0) : ''} psf)</option>`).join('')}</select>`;
}
function renderLoads(){
  const el = document.getElementById('bonesLoads'); if(!el) return;
  const lv = padLv(), roof = lv === nLv(), ids = LVP().laIds || [], LS = laStats(), L = loadsAt(lv);
  const sn = bonesData().snow, pf = sn && ok(num(sn.pf_psf)) ? num(sn.pf_psf) : null;
  const tag = document.getElementById('loadsLevel'); if(tag) tag.textContent = label(lv);
  if(!ids.length){ el.innerHTML = '<p class="small" style="margin:0">No load areas on this level yet. Choose <b>Load areas</b> on the pad and draw them; each one gets a row here.</p>'; return; }
  let changedAny = false;
  ids.forEach(q => { if(!L[q.id]){ L[q.id] = Object.assign({}, loadDefaults(lv)); changedAny = true; } });
  if(changedAny) save();
  const rows = ids.map(q => { const b = LS.byId[q.id], v = L[q.id];
    return `<tr><th scope="row"><span class="sw" style="background:${laCol(q.id)}"></span>${esc(q.id)}</th>
      <td style="text-align:left">${roof ? '<span class="muted">roof (L<sub>r</sub>)</span>' : occCell(q.id, v)}</td>
      <td>${b ? fmt(b.A, 0) : '0'}</td><td class="muted">${esc(q.psf)}</td>
      <td><input inputmode="decimal" data-ll="${esc(q.id)}" value="${esc(v.LL)}" aria-label="Live load for area ${esc(q.id)}, psf"></td>
      <td><input inputmode="decimal" data-sdl="${esc(q.id)}" value="${esc(v.SDL)}" aria-label="Superimposed dead load for area ${esc(q.id)}, psf"></td>
      <td>${roof ? (pf !== null ? `<b>${fmt(pf, 1)}</b>` : '<span class="b-warn">enter p<sub>g</sub></span>') : '<span class="muted">–</span>'}</td></tr>`; }).join('');
  el.innerHTML = `<div class="tbl"><table><thead><tr><th>Area ID</th><th style="text-align:left">Occupancy (LL + partitions)</th><th>Area, ft²</th><th>Seismic weight, psf</th><th>LL, psf</th><th>SDL, psf</th><th>SL, psf</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small" style="margin:8px 0 0">${roof ? 'Roof: LL is roof live load L<sub>r</sub>. SL is the flat-roof snow load p<sub>f</sub> from the hazard panel.' : 'SL applies at the roof only; pick the roof level to see it.'} Seismic weight is set in the pad’s Loading areas panel. The gravity optimizer uses each area’s occupancy (its LL, partitions and LL reduction) and the SDL in its config.</p>`;
}
document.addEventListener('change', e => {
  const t = e.target; if(!t.matches) return;
  if(t.matches('#bonesLoads select[data-occ]')){
    const row = loadsAt(padLv())[t.dataset.occ], o = occList().find(q => q.key === t.value);
    row.occupancy = t.value || null;
    if(o) row.LL = o.ll_psf;   // show the occupancy's LL; partitions are added by the optimizer
    save(); renderLoads(); return;
  }
  if(!t.matches('#bonesLoads input')) return;
  const id = t.dataset.ll !== undefined ? t.dataset.ll : t.dataset.sdl, f = t.dataset.ll !== undefined ? 'LL' : 'SDL', v = num(t.value);
  if(!(v >= 0)){ renderLoads(); return; }
  loadsAt(padLv())[id][f] = v; save();
});
function copyLoadsToFloors(){
  const lv = padLv(), src = loadsAt(lv);
  for(let i = 1; i < nLv(); i++) if(i !== lv) bonesData().loads[i] = JSON.parse(JSON.stringify(src));
  save(); renderLoads();
}
function setHazard(h){
  const B = bonesData(), v = (h && h.values) || {}; B.hazard = h;
  if(ok(num(v.sds))) S.sds = num(v.sds);      // module 1 fields of the ELF app
  if(ok(num(v.sd1))) S.sd1 = num(v.sd1);
  if(ok(num(v.ie))) S.ie = num(v.ie);
  save();
}
function setSnow(sn){ bonesData().snow = sn; save(); renderLoads(); }
function setMaterial(m){ bonesData().material = m; save(); }   // step 4's structural system, a stand-in for module 4
const lineKey = v => Math.round(v * 1000) / 1000;
function bonesLines(){
  const el = document.getElementById('bonesLines'); if(!el) return;
  const groups = {X: {}, Y: {}};
  (S.plan.lfrs || []).forEach(l => {
    const d = lDir(l), at = d === 'X' ? lineKey(l.y1) : lineKey(l.x1);
    (groups[d][at] = groups[d][at] || []).push(l.label);
  });
  const nx = Object.keys(groups.X).length, ny = Object.keys(groups.Y).length;
  const list = (d, axis) => Object.keys(groups[d]).sort((a, b) => a - b).map(at =>
    `<li><span>${axis} = ${fmt(+at, at % 1 ? 2 : 0)} ft</span><span>${groups[d][at].map(esc).join(', ')}</span></li>`).join('');
  let verdict, cls;
  if(!nx && !ny){ verdict = isWalls() ? 'No shear walls yet. Choose Shear walls, then click one end of a wall and then the other.'
                                      : 'No braced frames yet. Choose Braced frames, then click one column and the other column of a braced bay.'; cls = 'b-warn'; }
  else if(!nx || !ny){ verdict = `${isWalls() ? 'Walls' : 'Frames'} are needed in both directions; there are none in ${nx ? 'Y' : 'X'}.`; cls = 'b-bad'; }
  else if(nx + ny < 3){ verdict = 'Only one line in each direction: the lines meet at a point, so the plan cannot resist torsion. Add a third line.'; cls = 'b-bad'; }
  else { verdict = `${nx + ny} lines of resistance, ${nx} in X and ${ny} in Y: enough to resist torsion.`; cls = 'b-good'; }
  el.innerHTML = `<p class="${cls}" style="margin:0 0 10px">${verdict}</p>` +
    (nx ? `<h3>X direction <span>${nx} line${nx === 1 ? '' : 's'}</span></h3><ul>${list('X', 'y')}</ul>` : '') +
    (ny ? `<h3>Y direction <span>${ny} line${ny === 1 ? '' : 's'}</span></h3><ul>${list('Y', 'x')}</ul>` : '');
  const ok = nx && ny && nx + ny >= 3;
  window.dispatchEvent(new CustomEvent('bones:lines', {detail: {nx, ny, ok, frames: (S.plan.lfrs || []).length}}));
}

/* 3D view: every level of the typical plan, columns at the corners and frame columns, one brace per story
   (shear walls: one panel per wall, full height) */
function render3D(){
  const svg = document.getElementById('m3d'); if(!svg) return;
  const pl = S.plan, V = pl.verts || [];
  if(!pl.closed || V.length < 3){ svg.innerHTML = '<text x="500" y="300" text-anchor="middle" fill="#A1A1A8" font-size="18">Close the outline to see the 3D model.</text>'; return; }
  const hs = [0].concat(S.levels.map(L => num(L.h))).filter(ok);
  const top = hs[hs.length - 1];
  const W = Math.max(...V.map(p => p[0])), D = Math.max(...V.map(p => p[1]));
  const cy = Math.cos(V3.yaw * Math.PI / 180), sy = Math.sin(V3.yaw * Math.PI / 180);
  const cp = Math.cos(V3.pitch * Math.PI / 180), sp = Math.sin(V3.pitch * Math.PI / 180);
  const P = (x, y, z) => { const u = x - W / 2, v = y - D / 2, a = u * cy - v * sy, b = u * sy + v * cy; return [a, -(z - top / 2) * cp - b * sp]; };
  const pts = [];
  hs.forEach(z => V.forEach(p => pts.push(P(p[0], p[1], z))));
  const mnx = Math.min(...pts.map(p => p[0])), mxx = Math.max(...pts.map(p => p[0])), mny = Math.min(...pts.map(p => p[1])), mxy = Math.max(...pts.map(p => p[1]));
  const k = Math.min(920 / (mxx - mnx || 1), 520 / (mxy - mny || 1)) * V3.zoom;
  const ox = 500 - (mnx + mxx) / 2 * k + V3.px, oy = 300 - (mny + mxy) / 2 * k + V3.py;
  const T = (x, y, z) => { const q = P(x, y, z); return (ox + q[0] * k).toFixed(1) + ' ' + (oy + q[1] * k).toFixed(1); };
  const ring = z => 'M' + V.map(p => T(p[0], p[1], z)).join('L') + 'Z';
  let floors = '', cols = '', braces = '', beams = '', labels = '', panels = '';
  hs.slice(1, -1).forEach(z => floors += ring(z));
  const colPts = V.map(p => p.slice());
  (pl.lfrs || []).forEach(l => {
    const a = [l.x1, l.y1], b = [l.x2, l.y2], x2 = l.type === 'x2';
    if(isWalls()){
      panels += 'M' + T(a[0], a[1], 0) + 'L' + T(b[0], b[1], 0) + 'L' + T(b[0], b[1], top) + 'L' + T(a[0], a[1], top) + 'Z';
      hs.slice(1, -1).forEach(z => { beams += 'M' + T(a[0], a[1], z) + 'L' + T(b[0], b[1], z); });
      const q = T((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, top).split(' ');
      labels += `<text x="${q[0]}" y="${+q[1] - 10}" text-anchor="middle" font-size="15" font-weight="700" fill="#E3A3AD">${esc(l.label)}</text>`;
      return;
    }
    const L = lLen(l), t = x2 && num(l.L1) > 0 && num(l.L1) < L ? num(l.L1) / L : 0.5;
    const m = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    colPts.push(a, b); if(x2) colPts.push(m);
    hs.slice(1).forEach((z, i) => {
      const z0 = hs[i], flip = l.alt && i % 2 === 1;
      beams += 'M' + T(a[0], a[1], z) + 'L' + T(b[0], b[1], z);
      if(x2){
        const atTop = (l.mid !== 'bot') !== (i % 2 === 1);    // Λ then V, alternating each story
        braces += atTop ? 'M' + T(a[0], a[1], z0) + 'L' + T(m[0], m[1], z) + 'L' + T(b[0], b[1], z0)
                        : 'M' + T(a[0], a[1], z) + 'L' + T(m[0], m[1], z0) + 'L' + T(b[0], b[1], z);
      } else {
        const toB = (l.up !== 'a') !== flip;                   // story 1 rises to the 'up' end
        const lo = toB ? a : b, hi = toB ? b : a;
        braces += 'M' + T(lo[0], lo[1], z0) + 'L' + T(hi[0], hi[1], z);
      }
    });
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], q = T(mid[0], mid[1], top).split(' ');
    labels += `<text x="${q[0]}" y="${+q[1] - 10}" text-anchor="middle" font-size="15" font-weight="700" fill="#E3A3AD">${esc(l.label)}</text>`;
  });
  colPts.forEach(p => cols += 'M' + T(p[0], p[1], 0) + 'L' + T(p[0], p[1], top));
  svg.innerHTML = `<path d="${ring(0)}" fill="#fff" fill-opacity=".05" stroke="#5A5A61"/>
    <path d="${floors}" fill="#fff" fill-opacity=".03" stroke="#fff" stroke-opacity=".45"/>
    <path d="${cols}" stroke="#fff" stroke-opacity=".35"/>
    <path d="${beams}" stroke="#D0566A" stroke-opacity=".7" stroke-width="2"/>
    <path d="${braces}" stroke="#D0566A" stroke-width="2.6" fill="none" stroke-linejoin="round"/>
    <path d="${panels}" fill="#D0566A" fill-opacity=".22" stroke="#D0566A" stroke-width="1.6" stroke-linejoin="round"/>
    <path d="${ring(top)}" fill="none" stroke="#fff" stroke-width="1.6"/>${labels}`;
  const sum = document.getElementById('m3dSum'), nl = (pl.lfrs || []).length;
  if(sum) sum.textContent = `${S.levels.length} level${S.levels.length === 1 ? '' : 's'}, ${nl} ${isWalls() ? 'shear wall' : 'braced frame'}${nl === 1 ? '' : 's'}`;
}
(function wire3D(){
  const views = {v3iso: [-35, 28], v3plan: [0, 90], v3front: [0, 0], v3side: [-90, 0]};
  Object.keys(views).forEach(id => { const b = document.getElementById(id); if(b) b.addEventListener('click', () => { [V3.yaw, V3.pitch] = views[id]; V3.zoom = 1; V3.px = V3.py = 0; render3D(); }); });
  const w = document.getElementById('m3dWrap'); if(!w) return;
  let drag = null;
  w.addEventListener('pointerdown', e => { drag = {x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button === 2}; w.setPointerCapture(e.pointerId); });
  w.addEventListener('pointermove', e => {
    if(!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y; drag.x = e.clientX; drag.y = e.clientY;
    if(drag.pan){ const s = 1000 / w.clientWidth; V3.px += dx * s; V3.py += dy * s; }
    else { V3.yaw -= dx * 0.4; V3.pitch = Math.max(0, Math.min(90, V3.pitch + dy * 0.3)); }
    render3D();
  });
  w.addEventListener('pointerup', () => drag = null);
  w.addEventListener('contextmenu', e => e.preventDefault());
  w.addEventListener('wheel', e => { if(document.activeElement !== w) return; e.preventDefault(); V3.zoom = Math.max(0.3, Math.min(6, V3.zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12))); render3D(); }, {passive: false});
})();
/* ---- Shear walls (S.lateral_system = 'shear_wall'): the same lines, as concrete walls with a thickness per story ----
   The pad's own code is unchanged; these wrappers swap the brace inputs for wall thickness. lateral_system and each
   wall's ts (in, story 1 first) are saved with the project, and the ELF engine (elf_engine.py) reads both. */
function isWalls(){ return S.lateral_system === 'shear_wall'; }
function wallTDef(){ const v = num(bonesData().wallT); return v > 0 ? v : 8; }   // the last thickness entered, in
// thickness of wall l in story i (in): that story's, else the nearest story below that has one, else t (as the engine fills it)
function wallT(l, i){
  const ts = Array.isArray(l.ts) ? l.ts : [];
  for(let j = Math.min(i, ts.length) - 1; j >= 0; j--) if(num(ts[j]) > 0) return num(ts[j]);
  return num(l.t) > 0 ? num(l.t) : wallTDef();
}
function fillTs(l){ const ts = []; for(let i = 1; i <= nLv(); i++) ts.push(wallT(l, i)); l.ts = ts; }
// the pad's center of rigidity: each wall on its own, as the engine's point spring with its default inputs
// (I_eff = 0.35 I_g, shear on, h = roof): k = 1/(1/k_f + 1/k_v), k_f = 0.35·t·L³/12, k_v = t·L·h²/(3κ·E/G) = t·L·h²/7.5.
// The analysis also joins walls that meet into cores.
function wallSpring(l, i){
  const t = wallT(l, i) / 12, L = lLen(l), h = num(S.levels[S.levels.length - 1].h);
  if(!(t > 0 && L > 0 && h > 0)) return NaN;
  return 1 / (1 / (0.35 * t * L * L * L / 12) + 1 / (t * L * h * h / 7.5));
}
const wallKFrames = wallK;
wallK = function(l, i){ return isWalls() ? wallSpring(l, i === undefined ? dispStory() : i) : wallKFrames(l, i); };
// a new wall asks for its thickness (the last one entered is offered)
const lfrsClickFrames = lfrsClick;
lfrsClick = function(p){
  const n0 = PL().lfrs.length;
  lfrsClickFrames(p);
  if(!isWalls() || PL().lfrs.length === n0) return;
  const l = PL().lfrs[PL().lfrs.length - 1];
  l.t = wallTDef(); l.ts = Array(nLv()).fill(l.t); save(); renderTPanel();
  askThickness(l);
};
function askThickness(l){
  const dlg = document.getElementById('wallDlg'), inp = document.getElementById('wallDlgIn');
  if(!dlg || !dlg.showModal) return;
  const t0 = wallTDef();
  document.getElementById('wallDlgMsg').textContent = `${l.label}: ${lDir(l)} direction, ${ft(lLen(l))} long. Every level gets this thickness; change one level in the Shear walls panel.`;
  document.getElementById('wallDlgKeep').textContent = `Keep ${+t0.toFixed(2)} in`;
  inp.value = t0;
  dlg.onclose = () => {
    const v = num(inp.value);
    if(dlg.returnValue !== 'ok' || !(v > 0)) return;
    l.t = v; l.ts = Array(nLv()).fill(v); bonesData().wallT = v; changed();
  };
  dlg.returnValue = ''; dlg.showModal(); inp.select();
}
(function wireWallDlg(){
  const k = document.getElementById('wallDlgKeep');
  if(k) k.addEventListener('click', () => document.getElementById('wallDlg').close('keep'));
})();
// the side panel lists wall thicknesses at the pad's level instead of brace areas
const renderTPanelFrames = renderTPanel;
renderTPanel = function(){
  wallsChrome();
  if(!isWalls()) return renderTPanelFrames();
  const L = PL().lfrs, el = $('#tpList'); if(!el) return;
  if(document.activeElement && el.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;   // do not rebuild while typing
  if(document.activeElement !== $('#aDef')) $('#aDef').value = wallTDef();
  if(!L.length){ el.innerHTML = '<div class="tp-empty">Draw shear walls to list them here.</div>'; return; }
  const lv = padLv(), ord = L.map((l,i) => ({l,i})).sort((a,b) => lDir(a.l).localeCompare(lDir(b.l)) || a.l.label.localeCompare(b.l.label, undefined, {numeric:true}));
  el.innerHTML = ord.map(({l,i}) => `<label class="tp-row${sel && sel.type==='lfrs' && sel.idx===i ? ' on' : ''}"><b>${esc(l.label)}</b><span class="muted">${ft(lLen(l))}</span>
    <input class="in" type="number" min="0" step="0.5" data-tt="${i}" value="${esc(wallT(l, lv))}" aria-label="Thickness of ${esc(l.label)} at ${esc(label(lv))}, in inches"><span class="muted">in</span></label>`).join('');
};
$('#tpList').addEventListener('change', e => {
  const t = e.target; if(t.dataset.tt === undefined) return;
  const l = PL().lfrs[+t.dataset.tt], v = num(t.value); if(!l) return;
  if(!(v > 0)){ t.value = wallT(l, padLv()); return; }
  pushHist(); fillTs(l); l.ts[padLv() - 1] = v; t.blur(); changed();
});
// "New walls, t": the thickness offered for the next wall (the pad's own handler keeps the brace area)
$('#aDef').addEventListener('change', e => {
  if(!isWalls()) return;
  e.stopImmediatePropagation();
  const v = num(e.target.value);
  if(v > 0){ bonesData().wallT = v; save(); } else e.target.value = wallTDef();
}, true);
// "Copy to all levels" and "Copy from" copy the thicknesses too
const copyLevelFrames = copyLevel;
copyLevel = function(from, to){
  copyLevelFrames(from, to);
  if(isWalls()) PL().lfrs.forEach(l => { fillTs(l); l.ts[to - 1] = l.ts[from - 1]; });
};
// no brace-direction arrows on walls
const renderFrames = render;
render = function(){ const o = OPT(), b = o.bdir; if(isWalls()) o.bdir = false; try{ renderFrames(); } finally{ o.bdir = b; } };
const hintFrames = hintUpdate;
hintUpdate = function(){
  hintFrames();
  if(!isWalls()) return;
  const el = $('#planHint');
  el.innerHTML = el.innerHTML
    .replace('Click the first column of a braced bay (the line runs column to column).', 'Click one end of a wall, then the other; it asks for the wall thickness.')
    .replace('<b>Braced frames</b> to add frames', '<b>Shear walls</b> to add walls')
    .replace('Frame selected:', 'Wall selected:').replace('the frame moves', 'the wall moves');
};
const planStatsFrames = planStatsUI;
planStatsUI = function(){
  planStatsFrames();
  if(!isWalls()) return;
  const L = PL().lfrs, nx = L.filter(l => lDir(l) === 'X').length;
  $('#ps-n').textContent = `${nx} X · ${L.length - nx} Y walls`;
  $('#ps-Rn').textContent = `${label(padLv())} (drawing pad level), walls below it, each on its own`;
};
// the step 3 lateral system switch (step 4's system cards also carry data-sys, so match these two only)
const SYS_BUTTONS = '[data-sys="braced_frame"], [data-sys="shear_wall"]';
// labels that name braced frames
let chromeFor = null;
function wallsChrome(){
  const w = isWalls(); if(chromeFor === w) return; chromeFor = w;
  const text = (q, i, frames, walls) => {
    const e = document.querySelector('#pad ' + q); if(!e) return;
    const nodes = [...e.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim());
    const n = i < 0 ? nodes[nodes.length - 1] : nodes[i]; if(n) n.textContent = w ? walls : frames;
  };
  text('[data-mode="lfrs"]', 0, '2 · Braced frames', '2 · Shear walls');
  text('.tpanel:not(.lapanel) .tp-head', 0, 'Braced frames ', 'Shear walls ');
  text('.tp-def', 0, 'New frames, A ', 'New walls, t ');
  text('.tp-def', -1, ' in²', ' in');
  const foot = document.querySelector('#pad .tpanel:not(.lapanel) .tp-foot');
  if(foot){ if(!foot.dataset.frames) foot.dataset.frames = foot.innerHTML;
    foot.innerHTML = w ? 't = wall thickness (in) in the story below this level. Each new wall asks for its thickness. The pad’s C.R. treats every wall on its own; the analysis joins walls that meet into cores.' : foot.dataset.frames; }
  const tp = document.querySelector('#pad .tpanel:not(.lapanel)'); if(tp) tp.setAttribute('aria-label', w ? 'Shear walls' : 'Braced frames');
  const ps = $('#ps-n'); if(ps && ps.previousElementSibling) ps.previousElementSibling.textContent = w ? 'Shear walls' : 'Braced frames';
  const bd = document.querySelector('#pad [data-opt="bdir"]'); if(bd) bd.closest('label').hidden = w;
  const what = w ? 'wall thicknesses' : 'brace areas';
  $('#lvCopyAll').title = `Copy this level's ${what} and load areas to every other level`;
  $('#lvCopyFrom').setAttribute('aria-label', `Copy ${what} and load areas from another level`);
  document.querySelectorAll(SYS_BUTTONS).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.sys === (w ? 'shear_wall' : 'braced_frame'))));
}
function setSystem(sys){
  S.lateral_system = sys === 'shear_wall' ? 'shear_wall' : 'braced_frame';
  if(isWalls()) PL().lfrs.forEach(l => { if(!Array.isArray(l.ts) || !l.ts.length) l.ts = Array(nLv()).fill(wallTDef()); });
  save(); changed();
}
document.querySelectorAll(SYS_BUTTONS).forEach(b => b.addEventListener('click', () => setSystem(b.dataset.sys)));

window.BonesPad = {open: bonesOpen, state: () => S, bones: () => bonesData(), setHazard, setSnow, setMaterial, copyLoadsToFloors, renderLoads, setSystem,
  system: () => S.lateral_system || 'braced_frame',
  toScreen: (x, y) => { const r = document.getElementById('plan').getBoundingClientRect(), p = toS([x, y]); return [r.left + p[0], r.top + p[1]]; }, fit: () => { sizePlan(); fit(); render(); }, refresh: () => { bonesLines(); render3D(); }};
bonesReady = true;
renderTPanel(); planStatsUI(); render();   // the pad drew itself before the wrappers above existed
bonesLines(); render3D(); renderLoads();
