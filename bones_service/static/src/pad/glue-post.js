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
    const keep = {hazard: (S.bones || {}).hazard, snow: (S.bones || {}).snow, loads: (S.bones || {}).loads};
    S.bones = Object.assign({}, d.bones, ...Object.keys(keep).filter(k => keep[k]).map(k => ({[k]: keep[k]})));
    ensureLevels(S);
  } else {
    S = normalize(Object.assign(fresh(), d)); S.n = S.levels.length;
  }
  renderInputs(); renderT1(); renderT3(); update(); planReset(); save();
  return same;
}

/* Gravity loads by load area (LL, SDL; SL at the roof), kept in S.bones.loads[level][id] */
function bonesData(){ S.bones = S.bones || {}; S.bones.loads = S.bones.loads || {}; return S.bones; }
function loadsAt(lv){ const B = bonesData(); return (B.loads[lv] = B.loads[lv] || {}); }
function loadDefaults(lv){ return lv === nLv() ? {LL: 20, SDL: 20} : {LL: 50, SDL: 15}; }
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
      <td>${b ? fmt(b.A, 0) : '0'}</td><td class="muted">${esc(q.psf)}</td>
      <td><input inputmode="decimal" data-ll="${esc(q.id)}" value="${esc(v.LL)}" aria-label="Live load for area ${esc(q.id)}, psf"></td>
      <td><input inputmode="decimal" data-sdl="${esc(q.id)}" value="${esc(v.SDL)}" aria-label="Superimposed dead load for area ${esc(q.id)}, psf"></td>
      <td>${roof ? (pf !== null ? `<b>${fmt(pf, 1)}</b>` : '<span class="b-warn">enter p<sub>g</sub></span>') : '<span class="muted">–</span>'}</td></tr>`; }).join('');
  el.innerHTML = `<div class="tbl"><table><thead><tr><th>Area ID</th><th>Area, ft²</th><th>Seismic weight, psf</th><th>LL, psf</th><th>SDL, psf</th><th>SL, psf</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small" style="margin:8px 0 0">${roof ? 'Roof: LL is roof live load L<sub>r</sub>. SL is the flat-roof snow load p<sub>f</sub> from the hazard panel.' : 'SL applies at the roof only; pick the roof level to see it.'} Seismic weight is set in the pad’s Loading areas panel.</p>`;
}
document.addEventListener('change', e => {
  const t = e.target; if(!t.matches || !t.matches('#bonesLoads input')) return;
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
  if(!nx && !ny){ verdict = 'No braced frames yet. Choose Braced frames, then click one column and the other column of a braced bay.'; cls = 'b-warn'; }
  else if(!nx || !ny){ verdict = `Frames are needed in both directions; there are none in ${nx ? 'Y' : 'X'}.`; cls = 'b-bad'; }
  else if(nx + ny < 3){ verdict = 'Only one line in each direction: the lines meet at a point, so the plan cannot resist torsion. Add a third line.'; cls = 'b-bad'; }
  else { verdict = `${nx + ny} lines of resistance, ${nx} in X and ${ny} in Y: enough to resist torsion.`; cls = 'b-good'; }
  el.innerHTML = `<p class="${cls}" style="margin:0 0 10px">${verdict}</p>` +
    (nx ? `<h3>X direction <span>${nx} line${nx === 1 ? '' : 's'}</span></h3><ul>${list('X', 'y')}</ul>` : '') +
    (ny ? `<h3>Y direction <span>${ny} line${ny === 1 ? '' : 's'}</span></h3><ul>${list('Y', 'x')}</ul>` : '');
  const ok = nx && ny && nx + ny >= 3;
  window.dispatchEvent(new CustomEvent('bones:lines', {detail: {nx, ny, ok, frames: (S.plan.lfrs || []).length}}));
}

/* 3D view: every level of the typical plan, columns at the corners and frame columns, one brace per story */
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
  let floors = '', cols = '', braces = '', beams = '', labels = '';
  hs.slice(1, -1).forEach(z => floors += ring(z));
  const colPts = V.map(p => p.slice());
  (pl.lfrs || []).forEach(l => {
    const a = [l.x1, l.y1], b = [l.x2, l.y2], x2 = l.type === 'x2';
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
    <path d="${ring(top)}" fill="none" stroke="#fff" stroke-width="1.6"/>${labels}`;
  const sum = document.getElementById('m3dSum');
  if(sum) sum.textContent = `${S.levels.length} level${S.levels.length === 1 ? '' : 's'}, ${(pl.lfrs || []).length} braced frame${(pl.lfrs || []).length === 1 ? '' : 's'}`;
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
window.BonesPad = {open: bonesOpen, state: () => S, bones: () => bonesData(), setHazard, setSnow, copyLoadsToFloors,
  toScreen: (x, y) => { const r = document.getElementById('plan').getBoundingClientRect(), p = toS([x, y]); return [r.left + p[0], r.top + p[1]]; }, fit: () => { sizePlan(); fit(); render(); }, refresh: () => { bonesLines(); render3D(); }};
bonesReady = true;
bonesLines(); render3D(); renderLoads();
