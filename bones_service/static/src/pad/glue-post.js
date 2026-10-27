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
    S = normalize(Object.assign(fresh(), d)); S.n = S.levels.length;
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
  if(!nx && !ny){ verdict = 'No lines of resistance yet. Choose Lines of resistance on the pad, then click one end of a wall or frame and then the other.'; cls = 'b-warn'; }
  else if(!nx || !ny){ verdict = `Lines of resistance are needed in both directions; there are none in ${nx ? 'Y' : 'X'}.`; cls = 'b-bad'; }
  else if(nx + ny < 3){ verdict = 'Only one line in each direction: the lines meet at a point, so the plan cannot resist torsion. Add a third line.'; cls = 'b-bad'; }
  else { verdict = `${nx + ny} lines of resistance, ${nx} in X and ${ny} in Y: enough to resist torsion.`; cls = 'b-good'; }
  el.innerHTML = `<p class="${cls}" style="margin:0 0 10px">${verdict}</p>` +
    (nx ? `<h3>X direction <span>${nx} line${nx === 1 ? '' : 's'}</span></h3><ul>${list('X', 'y')}</ul>` : '') +
    (ny ? `<h3>Y direction <span>${ny} line${ny === 1 ? '' : 's'}</span></h3><ul>${list('Y', 'x')}</ul>` : '');
  const ok = nx && ny && nx + ny >= 3;
  window.dispatchEvent(new CustomEvent('bones:lines', {detail: {nx, ny, ok, frames: (S.plan.lfrs || []).length}}));
}

/* 3D view: every level of the typical plan, columns at the corners, each line of resistance shaded as a wall */
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
  let floors = '', cols = '', walls = '', labels = '';
  hs.slice(1, -1).forEach(z => floors += ring(z));
  (pl.lfrs || []).forEach(l => {
    const a = [l.x1, l.y1], b = [l.x2, l.y2];
    hs.slice(1).forEach((z, i) => {   // one shaded panel per story, so the floor lines still read across the wall
      walls += 'M' + T(a[0], a[1], hs[i]) + 'L' + T(b[0], b[1], hs[i]) + 'L' + T(b[0], b[1], z) + 'L' + T(a[0], a[1], z) + 'Z';
    });
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], q = T(mid[0], mid[1], top).split(' ');
    labels += `<text x="${q[0]}" y="${+q[1] - 10}" text-anchor="middle" font-size="15" font-weight="700" fill="#E3A3AD">${esc(l.label)}</text>`;
  });
  V.forEach(p => cols += 'M' + T(p[0], p[1], 0) + 'L' + T(p[0], p[1], top));
  svg.innerHTML = `<path d="${ring(0)}" fill="#fff" fill-opacity=".05" stroke="#5A5A61"/>
    <path d="${floors}" fill="#fff" fill-opacity=".03" stroke="#fff" stroke-opacity=".45"/>
    <path d="${cols}" stroke="#fff" stroke-opacity=".35"/>
    <path d="${walls}" fill="#D0566A" fill-opacity=".38" stroke="#D0566A" stroke-width="1.6" stroke-linejoin="round"/>
    <path d="${ring(top)}" fill="none" stroke="#fff" stroke-width="1.6"/>${labels}`;
  const sum = document.getElementById('m3dSum'), nL = (pl.lfrs || []).length;
  if(sum) sum.textContent = `${S.levels.length} level${S.levels.length === 1 ? '' : 's'}, ${nL} line${nL === 1 ? '' : 's'} of resistance`;
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
/* Wording: the pad's frames stand for walls or frames of any material (chosen in module 4), so its brace wording
   is swapped for "lines of resistance" as it renders, leaving pad.verbatim.* unchanged. Specific phrases first. */
const WORDS = [
  ['Click the first column of a braced bay (the line runs column to column)', 'Click one end of the wall or frame'],
  ['click the two columns of each braced bay', 'click both ends of each wall or frame'],
  ['Columns, beams and one brace per story are drawn for every frame.', 'Each line of resistance is shaded as a wall at every story.'],
  ['Frame selected:', 'Line selected:'], ['New frames, A', 'New lines, A'], [' to add frames', ' to add them'],
  ['Braced frames', 'Lines of resistance'], ['braced frames', 'lines of resistance'],
  ['Braced frame', 'Line of resistance'], ['braced frame', 'line of resistance']];
const reword = s => WORDS.reduce((t, [a, b]) => t.split(a).join(b), s);
function rewordTree(n){
  if(n.nodeType === 3){
    if(n.parentElement && !n.parentElement.closest('svg')){ const t = reword(n.nodeValue); if(t !== n.nodeValue) n.nodeValue = t; }
    return;
  }
  if(n.nodeType !== 1 || n.closest('svg')) return;   // the plan and 3D drawings carry no wording, and redraw often
  ['aria-label', 'title'].forEach(a => { const v = n.getAttribute(a); if(v){ const t = reword(v); if(t !== v) n.setAttribute(a, t); } });
  n.childNodes.forEach(rewordTree);
}
(function wireWording(){
  const pad = document.getElementById('pad'); if(!pad) return;
  const foot = pad.querySelector('.tpanel:not(.lapanel) .tp-foot');
  if(foot) foot.innerHTML = 'Each line is a wall or frame that resists lateral load; its material is chosen in Module 4. Until then, A (in²) only sets how the load is shared between lines.';
  rewordTree(pad);
  new MutationObserver(ms => ms.forEach(m => m.type === 'characterData' ? rewordTree(m.target) : m.addedNodes.forEach(rewordTree)))
    .observe(pad, {childList: true, subtree: true, characterData: true});
})();
window.BonesPad = {open: bonesOpen, state: () => S, bones: () => bonesData(), setHazard, setSnow, setMaterial, copyLoadsToFloors,
window.BonesPad = {open: bonesOpen, state: () => S, bones: () => bonesData(), setHazard, setSnow, copyLoadsToFloors, renderLoads,
  toScreen: (x, y) => { const r = document.getElementById('plan').getBoundingClientRect(), p = toS([x, y]); return [r.left + p[0], r.top + p[1]]; }, fit: () => { sizePlan(); fit(); render(); }, refresh: () => { bonesLines(); render3D(); }};
bonesReady = true;
bonesLines(); render3D(); renderLoads();
