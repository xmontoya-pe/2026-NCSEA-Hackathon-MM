const $ = s => document.querySelector(s);
const num = v => (v === '' || v === null || v === undefined) ? NaN : Number(v);
const ok = v => Number.isFinite(v);
const fmt = (v, d) => ok(v) ? v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}) : '—';
function armed(btn, prompt, action){
  if(btn.dataset.armed){ clearTimeout(+btn.dataset.t); btn.textContent = btn.dataset.txt; delete btn.dataset.armed; btn.classList.remove('arm'); action(); return; }
  btn.dataset.txt = btn.textContent; btn.dataset.armed = '1'; btn.textContent = prompt; btn.classList.add('arm');
  btn.dataset.t = setTimeout(() => { btn.textContent = btn.dataset.txt; delete btn.dataset.armed; btn.classList.remove('arm'); }, 3500);
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function defaultLevels(n){ return Array.from({length:n},(_,j)=>({name:'',h:(j+1)*10,psf:200})); }
// typical floor area comes from the closed floor plan in section 2
function planArea(){ const st = (S.plan && S.plan.closed) ? polyStats() : null; return st ? st.A : NaN; }
function fresh(){
  return {project:'',by:'',date:new Date().toISOString().slice(0,10),n:5,cs:0.1,ie:1.0,sds:1.0,sd1:0.6,
          sys:'other',ct:0.02,xexp:0.75,Tan:'',k:1,kAuto:true,
          fillH:10,fillP:200,levels:defaultLevels(5),
          plan:{verts:[],closed:false,lfrs:[],grid:1,aDef:10,kStory:1,padLv:1,lv:[],opts:{chain:true,lfdim:true,nums:true,cm:true,cr:true,bdir:true,la:true}},
          hd:{dir:'Y',lvl:null,F:'Fx',method:'rigid',acc:'1'},
          col:{lvl:null,line:'',om:false,omega:2.0,method:'rigid',acc:'env',s:null},
          m3d:{forces:false}, ch:{s:null, dir:'Y', lvl:null, F:'Fx', method:'rigid', acc:'1'}, ve:{wall:0, method:'rigid', acc:'env'}};
}
function load(){
  try{ const s = JSON.parse(localStorage.getItem(KEY) || localStorage.getItem(KEY_BF)); if(s && Array.isArray(s.levels) && s.levels.length){ s.levels.forEach(L=>{ delete L.wpx; delete L.area; }); return normalize(Object.assign(fresh(), s)); } }catch(e){}
  return null;
}
function save(){ try{ localStorage.setItem(KEY, JSON.stringify(S)); }catch(e){} }

/* ---- per-level data (10/16): brace area A per frame per story (l.As), load areas per level (plan.lv[j]),
   the section 3 load-area switch per level (levels[j].la). Level j's braces are the story directly below level j. ---- */
function ensureLevels(st){
  const n = (st.levels || []).length || 1, pl = st.plan;
  // older projects: one set of load areas and one switch for the whole building -> copied to every level
  if(!Array.isArray(pl.lv)) pl.lv = [];
  if(Array.isArray(pl.la) || Array.isArray(pl.laIds)){
    const one = {la: (pl.la || []).filter(r => r && [r.x1,r.y1,r.x2,r.y2].every(v => ok(num(v)))), laIds: pl.laIds || [], laActive: pl.laActive || ''};
    if(!pl.lv.length) for(let j=0;j<n;j++) pl.lv.push(JSON.parse(JSON.stringify(one)));
    delete pl.la; delete pl.laIds; delete pl.laActive;
  }
  if(st.useLA !== undefined){ (st.levels || []).forEach(L => { if(L.la === undefined) L.la = !!st.useLA; }); delete st.useLA; }
  for(let j=0;j<n;j++) if(!pl.lv[j] || typeof pl.lv[j] !== 'object') pl.lv[j] = j ? JSON.parse(JSON.stringify(pl.lv[j-1])) : {la:[], laIds:[], laActive:''};
  pl.lv.length = n;
  pl.lv.forEach(o => { if(!Array.isArray(o.la)) o.la = []; if(!Array.isArray(o.laIds)) o.laIds = []; if(typeof o.laActive !== 'string') o.laActive = ''; });
  (st.levels || []).forEach(L => { L.la = !!L.la; });
  (pl.lfrs || []).forEach(l => {
    if(!Array.isArray(l.As)) l.As = [];
    for(let j=0;j<n;j++) if(!(num(l.As[j]) > 0)) l.As[j] = j && num(l.As[j-1]) > 0 ? l.As[j-1] : (num(l.A) > 0 ? num(l.A) : (num(pl.aDef) > 0 ? num(pl.aDef) : 10));
    l.As.length = n;
  });
  const v = Math.round(num(pl.padLv)); pl.padLv = v >= 1 && v <= n ? v : 1;
}
const nLv = () => S.levels.length;
const padLv = () => { const v = Math.round(num(S.plan.padLv)); return v >= 1 && v <= nLv() ? v : 1; };
const LV = lv => S.plan.lv[(lv || padLv()) - 1] || {la:[], laIds:[], laActive:''};
const LVP = () => LV(padLv());
// brace area of frame l in story i (the braces directly below level i)
const braceA = (l, i) => { const v = num(Array.isArray(l.As) ? l.As[(i || 1) - 1] : NaN); return v > 0 ? v : num(l.A); };
const braceAtxt = l => { const u = [...new Set((l.As || [l.A]).map(v => +num(v).toFixed(2)))]; return u.length === 1 ? String(u[0]) : `${Math.min(...u)} to ${Math.max(...u)}`; };
function normalize(st){
  const f = fresh();
  const optDef = Object.assign({}, f.plan.opts);   // defaults first, then the saved options on top (new options default on)
  st.plan = Object.assign(f.plan, st.plan || {}); st.plan.opts = Object.assign(optDef, st.plan.opts || {});
  if(!(num(st.plan.aDef) > 0)) st.plan.aDef = 10;
  if(!(num(st.plan.kStory) >= 1)) st.plan.kStory = 1;
  delete st.plan.tDef; delete st.plan.kShear; delete st.plan.kh;
  (st.plan.lfrs || []).forEach(l => { if(!(num(l.A) > 0)) l.A = st.plan.aDef; if(l.up !== 'a' && l.up !== 'b') l.up = 'b'; l.alt = !!l.alt; delete l.t; if(l.type !== 'x2') l.type = 'single'; if(l.mid !== 'bot') l.mid = 'top'; });
  ensureLevels(st);
  st.hd = Object.assign(f.hd, st.hd || {});
  if(!['rigid','simple','cont'].includes(st.hd.method)) st.hd.method = 'rigid';
  return st;
}
let S = load() || fresh();
ensureLevels(S);

const autoLabel = i => i === S.n ? 'Roof' : 'Lvl ' + i;
const label = i => (S.levels[i-1].name || '').trim() || autoLabel(i);

function download(name, text, type){
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text],{type}));
  a.download = name; document.body.appendChild(a); a.click();
  setTimeout(()=>{ URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
const baseName = () => ((S.project||'seismic-elf-bf').trim().replace(/[^\w\-]+/g,'_') || 'seismic-elf-bf');
$('#btnSave').addEventListener('click', ()=> download(baseName()+'.json', JSON.stringify(S,null,2), 'application/json'));
$('#btnOpen').addEventListener('click', ()=> $('#fileOpen').click());
$('#fileOpen').addEventListener('change', e=>{
  const f = e.target.files[0]; if(!f) return;
  f.text().then(t=>{
    const d = JSON.parse(t);
    if(!d || !Array.isArray(d.levels) || !d.levels.length) throw new Error('bad');
    S = normalize(Object.assign(fresh(), d)); S.n = S.levels.length;
    renderInputs(); renderT1(); renderT3(); update(); planReset();
  }).catch(()=>{ const b = $('#btnOpen'); b.textContent = 'Not a valid project file'; b.classList.add('arm'); setTimeout(()=>{ b.textContent = 'Open…'; b.classList.remove('arm'); }, 3000); });
  e.target.value = '';
});

/* ================= FLOOR PLAN & LFRS ================= */
const planEl = $('#plan'), wrap = $('#planWrap');
const EPS = 1e-6;
let PW = 1000, PH = 560, fitted = false;
let view = {s:8, ox:60, oy:480};
let mode = 'outline', cur = null, curS = null, snapR = null, typed = '', lfStart = null;
let hover = null, sel = null, drag = null, spaceDown = false, laStart = null;
const hist = [];
const PL = () => S.plan;
const OPT = () => PL().opts;
const gstep = () => { const g = num(PL().grid); return g > 0 ? g : 1; };
const sg = v => { const g = gstep(); return Math.round(v/g)*g; };
const rnd = v => Math.round(v*1e6)/1e6;
const same = (a,b) => Math.abs(a[0]-b[0])<EPS && Math.abs(a[1]-b[1])<EPS;
const toS = p => [view.ox + p[0]*view.s, view.oy - p[1]*view.s];
const toW = (x,y) => [(x-view.ox)/view.s, (view.oy-y)/view.s];
function ft(v){ const r = Math.round(v*100)/100; if(Math.abs(r)<0.005) return "0'"; return r.toFixed(2).replace(/\.?0+$/,'') + "'"; }
const lDir = l => Math.abs(l.y1-l.y2) < EPS ? 'X' : 'Y';
const lLen = l => lDir(l)==='X' ? l.x2-l.x1 : l.y2-l.y1;

/* ---- editable brace-frame dimensions (10/15): select a frame, click one of its blue dimensions, type a value, Enter ---- */
let dimEdits = [], dimEd = null, msgT = 0;
function planMsg(t){ const m = $('#planMsg'); m.textContent = t; m.hidden = false; clearTimeout(msgT); msgT = setTimeout(() => { m.hidden = true; }, 4500); }
function dimAt(sp){ return dimEdits.find(d => sp[0] >= d.x0 && sp[0] <= d.x1 && sp[1] >= d.y0 && sp[1] <= d.y1) || null; }
function closeDimEd(){ if(dimEd){ const el = dimEd.el; dimEd = null; el.remove(); } }
function openDimEd(d){
  closeDimEd();
  const ed = d.ed, cur = ed.kind==='perp' ? Math.abs(ed.perpTo-ed.c) : (ed.side==='lo' ? ed.fLo-ed.other : ed.other-ed.fHi);
  const el = document.createElement('input');
  el.type = 'text'; el.inputMode = 'decimal'; el.className = 'dim-ed'; el.id = 'dimEd';
  el.value = String(Math.round(cur*100)/100); el.setAttribute('aria-label', 'New dimension, ft');
  const r = planEl.getBoundingClientRect(), k = r.width / PW;
  el.style.left = (d.cx*k - 42) + 'px'; el.style.top = (d.cy*k - 13) + 'px';
  wrap.appendChild(el); dimEd = {el, ed};
  el.addEventListener('keydown', e => {
    e.stopPropagation();
    if(e.key==='Enter'){ e.preventDefault(); const v = parseFloat(el.value.replace(/[^0-9.\-]/g, '')); const x = dimEd && dimEd.ed; closeDimEd(); wrap.focus({preventScroll:true}); if(x) applyDimEdit(x, v); }
    else if(e.key==='Escape'){ e.preventDefault(); closeDimEd(); wrap.focus({preventScroll:true}); }
  });
  el.addEventListener('blur', () => { if(dimEd && dimEd.el === el) closeDimEd(); });
  el.addEventListener('pointerdown', e => e.stopPropagation());
  el.focus(); el.select();
}
function applyDimEdit(ed, v){
  const l = PL().lfrs[ed.idx]; if(!l) return;
  if(!ok(v) || v < 0){ planMsg('Enter a distance in feet (0 or more).'); return; }
  const hz = lDir(l)==='X';
  if(ed.kind==='gap'){
    const L = ed.fHi - ed.fLo, newLo = ed.side==='lo' ? ed.other + v : ed.other - v - L, delta = newLo - ed.fLo;
    const vmax = ed.side==='lo' ? ed.bnd.hi - L - ed.other : ed.other - L - ed.bnd.lo;
    if(newLo < ed.bnd.lo - 1e-6 || newLo + L > ed.bnd.hi + 1e-6){ planMsg(`${l.label} would run past the outline or into the next frame on its line. Largest value here: ${ft(vmax)}`); return; }
    if(Math.abs(delta) < 1e-9) return;
    pushHist();
    if(hz){ l.x1 = rnd(l.x1+delta); l.x2 = rnd(l.x2+delta); } else { l.y1 = rnd(l.y1+delta); l.y2 = rnd(l.y2+delta); }
  } else {
    const sg0 = Math.sign(ed.c - ed.perpTo) || 1, newC = ed.perpTo + sg0*v;
    if(v > 0){
      const P2 = (al,pp) => ed.hz ? [al,pp] : [pp,al];
      const inside = [0.01, 0.25, 0.5, 0.75, 0.99].every(t => pip(P2(ed.a0 + (ed.a1-ed.a0)*t, newC)));
      if(!inside){ planMsg(`${l.label} would leave the outline at ${ft(v)} from the edge.`); return; }
    }
    if(Math.abs(newC - ed.c) < 1e-9) return;
    pushHist();
    if(hz){ l.y1 = rnd(newC); l.y2 = rnd(newC); } else { l.x1 = rnd(newC); l.x2 = rnd(newC); }
  }
  $('#planMsg').hidden = true;
  changed();
}
function pushHist(){ hist.push(JSON.stringify(PL())); if(hist.length > 150) hist.shift(); }
function undo(){ if(!hist.length) return; S.plan = JSON.parse(hist.pop()); sel = null; lfStart = null; typed = ''; if(!PL().closed && mode !== 'outline') setMode('outline', true); changed(); }

/* ---- geometry ---- */
function polyEdges(){
  const v = PL().verts, n = v.length, out = [];
  const m = PL().closed ? n : n-1;
  for(let i=0;i<m;i++){ const a=v[i], b=v[(i+1)%n]; out.push({i, a, b, hz: Math.abs(a[1]-b[1])<EPS}); }
  return out;
}
function pip(p){
  const v = PL().verts; let inside = false;
  for(let i=0, j=v.length-1; i<v.length; j=i++){
    const [xi,yi] = v[i], [xj,yj] = v[j];
    if(((yi>p[1]) !== (yj>p[1])) && (p[0] < (xj-xi)*(p[1]-yi)/(yj-yi) + xi)) inside = !inside;
  }
  return inside;
}
function segDist(p,a,b){
  const dx=b[0]-a[0], dy=b[1]-a[1], L2=dx*dx+dy*dy;
  let t = L2 ? ((p[0]-a[0])*dx + (p[1]-a[1])*dy)/L2 : 0; t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy);
}
const onBoundary = p => polyEdges().some(e => segDist(p, e.a, e.b) < 1e-6);
// travel from p along axis k (0 = x, 1 = y) in direction sgn; return coordinate of first outline edge hit, or null
function ray(p, k, sgn){
  if(!PL().closed) return null;
  const o = 1-k; let best = null;
  for(const e of polyEdges()){
    if(k===0 ? e.hz : !e.hz) continue;
    const lo = Math.min(e.a[o], e.b[o]), hi = Math.max(e.a[o], e.b[o]);
    if(p[o] < lo-EPS || p[o] > hi+EPS) continue;
    const d = (e.a[k]-p[k])*sgn;
    if(d > EPS && (best === null || d < (best-p[k])*sgn)) best = e.a[k];
  }
  return best;
}
function polyStats(){
  const v = PL().verts, n = v.length;
  if(!PL().closed || n < 3) return null;
  let A=0, cx=0, cy=0, per=0, minx=Infinity, maxx=-Infinity, miny=Infinity, maxy=-Infinity;
  for(let i=0;i<n;i++){
    const [x0,y0]=v[i], [x1,y1]=v[(i+1)%n], c = x0*y1 - x1*y0;
    A += c; cx += (x0+x1)*c; cy += (y0+y1)*c; per += Math.hypot(x1-x0, y1-y0);
    minx=Math.min(minx,x0); maxx=Math.max(maxx,x0); miny=Math.min(miny,y0); maxy=Math.max(maxy,y0);
  }
  A /= 2;
  return {A:Math.abs(A), sA:A, cx: A ? cx/(6*A) : NaN, cy: A ? cy/(6*A) : NaN, per, minx, maxx, miny, maxy};
}
function selfIntersects(){
  const E = polyEdges(), n = E.length;
  const bb = e => [Math.min(e.a[0],e.b[0]), Math.max(e.a[0],e.b[0]), Math.min(e.a[1],e.b[1]), Math.max(e.a[1],e.b[1])];
  for(let i=0;i<n;i++) for(let j=i+1;j<n;j++){
    if(j===i+1 || (i===0 && j===n-1)) continue;
    const a = bb(E[i]), b = bb(E[j]);
    if(a[0] <= b[1]+EPS && b[0] <= a[1]+EPS && a[2] <= b[3]+EPS && b[2] <= a[3]+EPS) return true;
  }
  return false;
}
// remove duplicate and collinear vertices of a closed orthogonal polygon
function cleanup(v){
  const a = v.map(p => [rnd(p[0]), rnd(p[1])]); let again = true;
  while(again && a.length > 2){
    again = false;
    for(let i=0;i<a.length;i++){
      const p = a[(i-1+a.length)%a.length], c = a[i], n = a[(i+1)%a.length];
      if(same(c,n) || (Math.abs(p[0]-c[0])<EPS && Math.abs(c[0]-n[0])<EPS) || (Math.abs(p[1]-c[1])<EPS && Math.abs(c[1]-n[1])<EPS)){
        a.splice(i,1); again = true; break;
      }
    }
  }
  return a;
}

/* ================= LOAD AREAS (LR file, 10/15) =================
   Rectangles with an ID; each ID has a psf. They must cover the whole plan without overlapping. When module 3's
   "Seismic weight from load areas" is on (and the areas are valid), the floor weight is Σ psf·A, the beam load in
   sections 9 to 11 is w(s) = F·m(s)/W with m(s) the weight per foot of the cut at s, and the CM is the weight centroid. */
// cut of the outline at station m along axis k (0 = x, 1 = y): intervals of the other coordinate inside the plan
function cutAt(k, m){
  const v = PL().verts, n = v.length, o = 1-k, c = [];
  for(let j=0;j<n;j++){ const a = v[j], b = v[(j+1)%n]; if(Math.abs(a[o]-b[o]) < EPS && Math.min(a[k],b[k]) < m && Math.max(a[k],b[k]) > m) c.push(a[o]); }
  c.sort((a,b) => a-b); const iv = [];
  for(let j=0;j+1<c.length;j+=2) iv.push([c[j], c[j+1]]);
  return iv;
}
const laR = r => ({x1:Math.min(r.x1,r.x2), x2:Math.max(r.x1,r.x2), y1:Math.min(r.y1,r.y2), y2:Math.max(r.y1,r.y2)});
// area of a rectangle inside the plan, and its centroid (slabs in x, each cut against the outline)
function rectInPlan(r){
  const q = laR(r), xs = [...new Set([q.x1, q.x2, ...PL().verts.map(p => p[0]).filter(x => x > q.x1 && x < q.x2)].map(rnd))].sort((a,b) => a-b);
  let A = 0, Mx = 0, My = 0;
  for(let i=0;i<xs.length-1;i++){
    const a = xs[i], b = xs[i+1], w = b-a; if(w < EPS) continue;
    cutAt(0, (a+b)/2).forEach(([y0,y1]) => { const lo = Math.max(y0, q.y1), hi = Math.min(y1, q.y2); if(hi > lo){ const d = hi-lo; A += w*d; Mx += w*d*(a+b)/2; My += w*d*(lo+hi)/2; } });
  }
  return {A, cx: A ? Mx/A : NaN, cy: A ? My/A : NaN};
}
function laOverlapA(p, q){
  const a = laR(p), b = laR(q), x1 = Math.max(a.x1,b.x1), x2 = Math.min(a.x2,b.x2), y1 = Math.max(a.y1,b.y1), y2 = Math.min(a.y2,b.y2);
  return (x2-x1 > EPS && y2-y1 > EPS) ? rectInPlan({x1,y1,x2,y2}).A : 0;
}
const laPsf = (id, lv) => { const e = (LV(lv).laIds || []).find(q => q.id === id); return e ? num(e.psf) : NaN; };
const laCache = new Map();
function laStats(lv){
  lv = lv || padLv();
  const pl = PL(), D = LV(lv), key = JSON.stringify([pl.closed, pl.verts, D.la, D.laIds]);
  const hit = laCache.get(lv); if(hit && hit.key === key) return hit.v;
  const st = polyStats(), L = D.la || [];
  const out = {n:L.length, ok:false, err:'', A: st ? st.A : NaN, cover:0, unc:NaN, W:NaN, cx:NaN, cy:NaN, byId:{}, rects:[], overlap:null};
  if(!st) out.err = 'Close the floor plan outline first.';
  else if(!L.length) out.err = 'No load areas are drawn yet.';
  else {
    let W = 0, Mx = 0, My = 0, bad = null;
    L.forEach(r => {
      const g = rectInPlan(r), p = laPsf(r.id, lv); out.rects.push(g); out.cover += g.A;
      const b = out.byId[r.id] || (out.byId[r.id] = {A:0, W:0, n:0}); b.A += g.A; b.n++;
      if(!(p >= 0)) { if(bad === null) bad = r.id; } else { const w = p*g.A/1000; b.W += w; W += w; Mx += w*g.cx; My += w*g.cy; }
    });
    for(let i=0;i<L.length && !out.overlap;i++) for(let j=i+1;j<L.length;j++) if(laOverlapA(L[i], L[j]) > 1e-6){ out.overlap = [i,j]; break; }
    out.W = W; out.cx = W > 0 ? Mx/W : NaN; out.cy = W > 0 ? My/W : NaN; out.unc = out.A - out.cover;
    const tol = Math.max(0.01, 1e-9*out.A);
    if(out.overlap) out.err = `Load areas ${L[out.overlap[0]].id} and ${L[out.overlap[1]].id} overlap. Load areas cannot overlap.`;
    else if(bad !== null) out.err = `Load area ID ${bad} has no psf.`;
    else if(out.unc > tol) out.err = `${fmt(out.unc,1)} ft² of the plan (${fmt(out.unc/out.A*100,2)}%) has no load area. Load areas must cover the whole plan.`;
    else if(!(W > 0)) out.err = 'The load areas weigh nothing (all psf are 0).';
    else out.ok = true;
  }
  laCache.set(lv, {key, v:out});
  return out;
}
// load areas drive the weight, the beam load and the CM only when module 3's switch is on and the areas are valid
const laOn = lv => !!(lv && S.levels[lv-1] && S.levels[lv-1].la) && laStats(lv).ok;
function cmPt(st, lv){ if(laOn(lv)){ const L = laStats(lv); return [L.cx, L.cy]; } return st ? [st.cx, st.cy] : [NaN, NaN]; }
// parts of the plan no load area covers (rectangles, by slabs in x): drawn hatched
function laGaps(lv){
  const pl = PL(), D = LV(lv); if(!pl.closed || !(D.la || []).length) return [];
  const L = D.la.map(laR), v = pl.verts, mn = Math.min(...v.map(p => p[0])), mx = Math.max(...v.map(p => p[0]));
  const xs = [...new Set([...v.map(p => p[0]), ...L.flatMap(r => [r.x1, r.x2]).filter(x => x > mn && x < mx)].map(rnd))].sort((a,b) => a-b), out = [];
  for(let i=0;i<xs.length-1;i++){
    const a = xs[i], b = xs[i+1], m = (a+b)/2; if(b-a < EPS) continue;
    const cov = L.filter(r => r.x1 < m && r.x2 > m).map(r => [r.y1, r.y2]).sort((p,q) => p[0]-q[0]);
    cutAt(0, m).forEach(([y0,y1]) => { let y = y0;
      cov.forEach(([c0,c1]) => { if(c1 <= y || c0 >= y1) return; if(c0 > y + EPS) out.push({x1:a, x2:b, y1:y, y2:Math.min(c0,y1)}); y = Math.max(y, c1); });
      if(y1 > y + EPS) out.push({x1:a, x2:b, y1:y, y2:y1}); });
  }
  return out;
}
const LA_COLS = ['#14b8a6','#f97316','#eab308','#d946ef','#3b82f6','#84cc16','#ef4444','#8b5cf6','#06b6d4','#a16207'];
const laCol = id => { const i = (LVP().laIds || []).findIndex(q => q.id === id); return LA_COLS[(i < 0 ? 0 : i) % LA_COLS.length]; };
function laNewId(){ let m = 0; (LVP().laIds || []).forEach(q => { const n = parseInt(q.id, 10); if(String(n) === q.id) m = Math.max(m, n); }); return String(m+1); }
function laEnsureId(){
  const pl = LVP(), L0 = S.levels[padLv()-1];
  if(!pl.laIds.length) pl.laIds.push({id:'1', psf: num(L0 && L0.psf) >= 0 ? num(L0.psf) : 100});
  if(!pl.laIds.some(q => q.id === pl.laActive)) pl.laActive = pl.laIds[0].id;
  return pl.laActive;
}
const laShown = () => !!OPT().la || mode === 'loads';
// snapping for load-area corners: outline corners, load-area corners, outline and load-area edges, alignments, grid
function snapLA(raw){
  const tol = 9/view.s, guides = [], L = (LVP().la || []).map(laR);
  const refs = PL().verts.concat(L.flatMap(r => [[r.x1,r.y1],[r.x2,r.y1],[r.x2,r.y2],[r.x1,r.y2]]));
  let best = null, bd = tol;
  refs.forEach(q => { const d = Math.hypot(q[0]-raw[0], q[1]-raw[1]); if(d < bd){ bd = d; best = q; } });
  if(best) return {p:[best[0],best[1]], guides, kind:'vertex'};
  const edges = (PL().closed ? polyEdges() : []).concat(L.flatMap(r => [{a:[r.x1,r.y1], b:[r.x2,r.y1], hz:true}, {a:[r.x1,r.y2], b:[r.x2,r.y2], hz:true}, {a:[r.x1,r.y1], b:[r.x1,r.y2], hz:false}, {a:[r.x2,r.y1], b:[r.x2,r.y2], hz:false}]));
  for(const e of edges){
    const k = e.hz ? 0 : 1, o = 1-k, lo = Math.min(e.a[k],e.b[k]), hi = Math.max(e.a[k],e.b[k]);
    if(Math.abs(raw[o]-e.a[o]) < tol && raw[k] >= lo-tol && raw[k] <= hi+tol){
      const q = alignTo(raw[k], k, e.a[o], refs, tol), val = rnd(Math.min(hi, Math.max(lo, q ? q[k] : sg(raw[k]))));
      const p = k===0 ? [val, e.a[1]] : [e.a[0], val];
      if(q && !same(q,p)) guides.push([q,p]);
      return {p, guides, kind:'edge'};
    }
  }
  const qx = alignTo(raw[0],0,raw[1],refs,tol), qy = alignTo(raw[1],1,raw[0],refs,tol);
  const p = [rnd(qx ? qx[0] : sg(raw[0])), rnd(qy ? qy[1] : sg(raw[1]))];
  if(qx && !same(qx,p)) guides.push([qx,p]);
  if(qy && !same(qy,p)) guides.push([qy,p]);
  return {p, guides, kind: (qx||qy) ? 'align' : 'grid'};
}
function laClick(p){
  if(!PL().closed){ planMsg('Close the floor plan outline first.'); return; }
  if(!laStart){ laStart = p; updSnap(); render(); return; }
  const r = {x1:Math.min(laStart[0],p[0]), y1:Math.min(laStart[1],p[1]), x2:Math.max(laStart[0],p[0]), y2:Math.max(laStart[1],p[1])};
  if(r.x2-r.x1 < EPS || r.y2-r.y1 < EPS){ planMsg('A load area needs a width and a depth: click the opposite corner.'); return; }
  if(!(rectInPlan(r).A > 1e-6)){ planMsg('That rectangle is outside the floor plan.'); laStart = null; render(); return; }
  const hit = LVP().la.findIndex(q => laOverlapA(q, r) > 1e-6);
  if(hit >= 0){ planMsg(`That rectangle overlaps load area ${LVP().la[hit].id}. Load areas cannot overlap.`); laStart = null; render(); return; }
  pushHist();
  r.id = laEnsureId(); LVP().la.push(r); laStart = null; $('#planMsg').hidden = true; changed(); renderLaPanel();
}

/* ---- snapping ---- */
function refPts(excl){
  const pts = PL().verts.slice();
  PL().lfrs.forEach(l => { pts.push([l.x1,l.y1], [l.x2,l.y2]); });
  return excl ? pts.filter(q => !same(q, excl)) : pts;
}
function alignTo(val, k, other, refs, tol){
  let best = null, bs = Infinity;
  refs.forEach(q => { const d = Math.abs(q[k]-val); if(d < tol){ const sc = d*1e3 + Math.abs(q[1-k]-other); if(sc < bs){ bs = sc; best = q; } } });
  return best;
}
function snapAt(raw, from){
  const tol = 9/view.s, guides = [];
  if(from){
    const dx = raw[0]-from[0], dy = raw[1]-from[1], hz = Math.abs(dx) >= Math.abs(dy), k = hz ? 0 : 1;
    const q = alignTo(raw[k], k, from[1-k], refPts(from), tol);
    const val = rnd(q ? q[k] : sg(raw[k]));
    const p = hz ? [val, from[1]] : [from[0], val];
    if(q && !same(q,p)) guides.push([q,p]);
    return {p, guides, kind: q ? 'align' : 'grid'};
  }
  const refs = refPts();
  let best = null, bd = tol;
  refs.forEach(q => { const d = Math.hypot(q[0]-raw[0], q[1]-raw[1]); if(d < bd){ bd = d; best = q; } });
  if(best) return {p:[best[0],best[1]], guides, kind:'vertex'};
  if(PL().closed){
    for(const e of polyEdges()){
      const k = e.hz ? 0 : 1, o = 1-k;
      const lo = Math.min(e.a[k],e.b[k]), hi = Math.max(e.a[k],e.b[k]);
      if(Math.abs(raw[o]-e.a[o]) < tol && raw[k] >= lo-tol && raw[k] <= hi+tol){
        const q = alignTo(raw[k], k, e.a[o], refs, tol);
        const val = rnd(Math.min(hi, Math.max(lo, q ? q[k] : sg(raw[k]))));
        const p = k===0 ? [val, e.a[1]] : [e.a[0], val];
        if(q && !same(q,p)) guides.push([q,p]);
        return {p, guides, kind:'edge'};
      }
    }
  }
  const qx = alignTo(raw[0],0,raw[1],refs,tol), qy = alignTo(raw[1],1,raw[0],refs,tol);
  const p = [rnd(qx ? qx[0] : sg(raw[0])), rnd(qy ? qy[1] : sg(raw[1]))];
  if(qx && !same(qx,p)) guides.push([qx,p]);
  if(qy && !same(qy,p)) guides.push([qy,p]);
  return {p, guides, kind: (qx||qy) ? 'align' : 'grid'};
}
function fromPt(){
  if(mode==='outline' && !PL().closed && PL().verts.length) return PL().verts[PL().verts.length-1];
  if(mode==='lfrs' && lfStart) return lfStart;
  return null;
}
function updSnap(){
  const drawing = (mode==='outline' && !PL().closed) || mode==='lfrs';
  snapR = (cur && drawing) ? snapAt(cur, fromPt()) : (cur && mode==='loads' && PL().closed) ? snapLA(cur) : null;
}

/* ---- actions ---- */
function addOutlinePoint(p){
  const v = PL().verts;
  if(v.length && same(p, v[v.length-1])) return;
  pushHist();
  if(v.length >= 3 && same(p, v[0])){ closeOutline(); return; }
  if(v.length >= 2){
    const a = v[v.length-2], b = v[v.length-1];
    const prevHz = Math.abs(a[1]-b[1]) < EPS, newHz = Math.abs(b[1]-p[1]) < EPS;
    if(prevHz === newHz){ if(same(p,a)) v.pop(); else v[v.length-1] = p; changed(); return; }
  }
  v.push(p); changed();
}
function closeOutline(){
  const v = PL().verts; if(v.length < 3) return;
  const f = v[0], l = v[v.length-1], w = v.slice();
  if(!(Math.abs(f[0]-l[0]) < EPS || Math.abs(f[1]-l[1]) < EPS)){
    const a = v[v.length-2], lastHz = Math.abs(a[1]-l[1]) < EPS;
    w.push(lastHz ? [l[0], f[1]] : [f[0], l[1]]);
  }
  const c = cleanup(w);
  if(c.length < 4) return;
  // place the lower-left corner of the outline at the origin (0, 0)
  const mnx = Math.min(...c.map(p => p[0])), mny = Math.min(...c.map(p => p[1]));
  PL().verts = c.map(p => [rnd(p[0]-mnx), rnd(p[1]-mny)]);
  PL().lfrs.forEach(l => { l.x1 = rnd(l.x1-mnx); l.x2 = rnd(l.x2-mnx); l.y1 = rnd(l.y1-mny); l.y2 = rnd(l.y2-mny); });
  view.ox += mnx*view.s; view.oy -= mny*view.s;
  PL().closed = true; typed = '';
  setMode('lfrs', true); changed();
}
function lfrsClick(p){
  if(!lfStart){ lfStart = p; typed = ''; updSnap(); render(); return; }
  if(same(p, lfStart)) return;
  pushHist();
  const a = lfStart, hz = Math.abs(p[1]-a[1]) < EPS;
  const l = hz ? {x1:Math.min(a[0],p[0]), y1:a[1], x2:Math.max(a[0],p[0]), y2:a[1]}
               : {x1:a[0], y1:Math.min(a[1],p[1]), x2:a[0], y2:Math.max(a[1],p[1])};
  l.label = nextLabel(hz ? 'X' : 'Y');
  l.A = num(PL().aDef) > 0 ? num(PL().aDef) : 10; l.As = Array(nLv()).fill(l.A); l.up = 'b'; l.alt = false; l.type = 'single'; l.mid = 'top';
  PL().lfrs.push(l); lfStart = null; changed();
}
function nextLabel(d){
  let m = 0; const re = new RegExp('^' + d + '(\\d+)$');
  PL().lfrs.forEach(l => { const r = re.exec(l.label || ''); if(r) m = Math.max(m, +r[1]); });
  return d + (m+1);
}
function clickAt(p){
  if(mode==='outline' && !PL().closed) addOutlinePoint(p);
  else if(mode==='lfrs') lfrsClick(p);
  else if(mode==='loads') laClick(p);
}
function placeTyped(){
  const L = parseFloat(typed); typed = '';
  const from = fromPt();
  if(!from || !cur || !(L > 0)){ render(); return; }
  const dx = cur[0]-from[0], dy = cur[1]-from[1], hz = Math.abs(dx) >= Math.abs(dy);
  const sgn = (hz ? dx : dy) < 0 ? -1 : 1;
  clickAt(hz ? [rnd(from[0]+sgn*L), from[1]] : [from[0], rnd(from[1]+sgn*L)]);
  updSnap(); render();
}
function delSel(){
  if(sel && sel.type === 'la'){ pushHist(); LVP().la.splice(sel.idx,1); sel = null; changed(); renderLaPanel(); return; }
  if(!sel || sel.type !== 'lfrs') return; pushHist(); PL().lfrs.splice(sel.idx,1); sel = null; changed();
}
function cancel(){ if(typed) typed = ''; else if(lfStart) lfStart = null; else if(laStart) laStart = null; else sel = null; updSnap(); render(); renderPlanTables(); renderLaPanel(); }
function setMode(m, silent){
  if(m==='outline' && PL().closed && !silent){
    pushHist(); PL().verts = []; PL().closed = false; changed();
  }
  mode = m; lfStart = null; laStart = null; typed = ''; sel = null; hover = null;
  if(m === 'loads' && PL().closed) laEnsureId();
  document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('on', b.dataset.mode === m));
  updSnap(); render(); renderPlanTables();
}

/* ---- view ---- */
function zoomAt(sp, f){
  closeDimEd();
  const s2 = Math.min(300, Math.max(0.3, view.s*f)); f = s2/view.s;
  view.ox = sp[0] - (sp[0]-view.ox)*f; view.oy = sp[1] - (sp[1]-view.oy)*f; view.s = s2;
  updSnap(); render();
}
function fit(){
  const pts = refPts();
  let minx=0, miny=0, maxx=100, maxy=60;
  if(pts.length){
    minx = Math.min(...pts.map(p=>p[0])); maxx = Math.max(...pts.map(p=>p[0]));
    miny = Math.min(...pts.map(p=>p[1])); maxy = Math.max(...pts.map(p=>p[1]));
    if(maxx-minx < 20){ const c=(maxx+minx)/2; minx=c-10; maxx=c+10; }
    if(maxy-miny < 20){ const c=(maxy+miny)/2; miny=c-10; maxy=c+10; }
  }
  const m = 95, bw = maxx-minx, bh = maxy-miny;
  view.s = Math.max(0.3, Math.min((PW-2*m)/bw, (PH-2*m-30)/bh));
  view.ox = (PW - bw*view.s)/2 - minx*view.s;
  view.oy = (PH + 30 + bh*view.s)/2 + miny*view.s;
  updSnap(); render();
}
function sizePlan(){
  if(!wrap.clientWidth) return;   // module collapsed: size it when shown
  PW = Math.max(200, wrap.clientWidth); PH = Math.max(200, wrap.clientHeight);
  planEl.setAttribute('viewBox', `0 0 ${PW} ${PH}`);
  if(!fitted){ fitted = true; fit(); } else render();
}

/* ---- drawing helpers ---- */
const f1 = v => v.toFixed(1);
const ln = (x1,y1,x2,y2,c) => `<line x1="${f1(x1)}" y1="${f1(y1)}" x2="${f1(x2)}" y2="${f1(y2)}" class="${c}"/>`;
const tx = (x,y,t,c,rot,anchor) => `<text x="${f1(x)}" y="${f1(y)}" class="${c}"${anchor?` text-anchor="${anchor}"`:''}${rot?` transform="rotate(-90 ${f1(x)} ${f1(y)})"`:''}>${esc(t)}</text>`;
// dimension between screen points A0,B0, offset d px along unit normal n
function dimS(A0, B0, n, d, text, cls, ext, ed){
  const A = [A0[0]+n[0]*d, A0[1]+n[1]*d], B = [B0[0]+n[0]*d, B0[1]+n[1]*d];
  const L = Math.hypot(B[0]-A[0], B[1]-A[1]); if(L < 2) return '';
  let s = '';
  if(ext){
    s += ln(A0[0]+n[0]*3, A0[1]+n[1]*3, A[0]+n[0]*5, A[1]+n[1]*5, 'dx '+cls);
    s += ln(B0[0]+n[0]*3, B0[1]+n[1]*3, B[0]+n[0]*5, B[1]+n[1]*5, 'dx '+cls);
  }
  s += ln(A[0],A[1],B[0],B[1],'dl '+cls);
  const u = [(B[0]-A[0])/L, (B[1]-A[1])/L], t = [(u[0]-u[1])*3.5, (u[1]+u[0])*3.5];
  [A,B].forEach(P => { s += ln(P[0]-t[0], P[1]-t[1], P[0]+t[0], P[1]+t[1], 'dt '+cls); });
  const vert = Math.abs(u[1]) > Math.abs(u[0]);
  let M = [(A[0]+B[0])/2, (A[1]+B[1])/2];
  if(L < text.length*6.3 + 10) M = [M[0]+n[0]*12, M[1]+n[1]*12];
  if(ed){   // editable: highlight box behind the value, and a hit box for clicks
    const w = text.length*6.8 + 12, h = 18, bw = vert ? h : w, bh = vert ? w : h;
    s += `<rect x="${f1(M[0]-bw/2)}" y="${f1(M[1]-bh/2)}" width="${f1(bw)}" height="${f1(bh)}" rx="4" class="dimed"/>`;
    dimEdits.push({x0:M[0]-bw/2-3, x1:M[0]+bw/2+3, y0:M[1]-bh/2-3, y1:M[1]+bh/2+3, cx:M[0], cy:M[1], ed});
    cls += ' edt';
  }
  s += tx(M[0], M[1], text, 'dtx '+cls, vert);
  return s;
}
function outlineDims(st){
  const v = PL().verts; let s = '';
  const ux = [...new Set(v.map(p => rnd(p[0])))].sort((a,b)=>a-b);
  const uy = [...new Set(v.map(p => rnd(p[1])))].sort((a,b)=>a-b);
  const at = (k, c, pick) => v.filter(p => Math.abs(p[k]-c) < EPS).reduce((m,p) => pick(p,m) ? p : m);
  const yb = toS([0,st.miny])[1] + 36, yt = toS([0,st.maxy])[1] - 36;
  const xl = toS([st.minx,0])[0] - 36, xr = toS([st.maxx,0])[0] + 36;
  // X chain along the bottom
  ux.forEach(x => { const p = toS(at(0, x, (p,m) => p[1] < m[1])); s += ln(p[0], p[1]+4, p[0], yb+5, 'dx'); });
  for(let i=0;i<ux.length-1;i++) s += dimS([toS([ux[i],0])[0], yb], [toS([ux[i+1],0])[0], yb], [0,1], 0, ft(ux[i+1]-ux[i]), '', false);
  // Y chain along the left
  uy.forEach(y => { const p = toS(at(1, y, (p,m) => p[0] < m[0])); s += ln(p[0]-4, p[1], xl-5, p[1], 'dx'); });
  for(let i=0;i<uy.length-1;i++) s += dimS([xl, toS([0,uy[i]])[1]], [xl, toS([0,uy[i+1]])[1]], [-1,0], 0, ft(uy[i+1]-uy[i]), '', false);
  // overall dimensions (top and right)
  const a = toS(at(0, st.minx, (p,m) => p[1] > m[1])), b = toS(at(0, st.maxx, (p,m) => p[1] > m[1]));
  s += ln(a[0], a[1]-4, a[0], yt-5, 'dx') + ln(b[0], b[1]-4, b[0], yt-5, 'dx');
  s += dimS([a[0], yt], [b[0], yt], [0,-1], 0, ft(st.maxx-st.minx), 'ov', false);
  const c = toS(at(1, st.miny, (p,m) => p[0] > m[0])), d = toS(at(1, st.maxy, (p,m) => p[0] > m[0]));
  s += ln(c[0]+4, c[1], xr+5, c[1], 'dx') + ln(d[0]+4, d[1], xr+5, d[1], 'dx');
  s += dimS([xr, c[1]], [xr, d[1]], [1,0], 0, ft(st.maxy-st.miny), 'ov', false);
  return s;
}
// which side of a line its chain dimension goes on, and its distances to the outline
function lfrsGeom(l){
  const hz = lDir(l)==='X', k = hz ? 0 : 1, o = 1-k;
  const a0 = hz ? l.x1 : l.y1, a1 = hz ? l.x2 : l.y2, c = hz ? l.y1 : l.x1;
  const P2 = (al,pp) => hz ? [al,pp] : [pp,al];
  const mid = (a0+a1)/2, at = a0 + (a1-a0)*0.25;
  let side = 1, perpTo = null, lo = null, hi = null;
  if(PL().closed){
    const e = 3/view.s, inP = pip(P2(at,c+e)), inM = pip(P2(at,c-e));
    if(inP !== inM) side = inP ? -1 : 1;           // on the outline: dimension outside
    else if(inP){                                     // interior: dimension away from nearest edge
      const dP = ray(P2(at,c), o, 1), dM = ray(P2(at,c), o, -1);
      const DP = dP===null ? Infinity : dP-c, DM = dM===null ? Infinity : c-dM;
      if(DP <= DM){ side = -1; perpTo = dP; } else { side = 1; perpTo = dM; }
    }
    lo = ray(P2(a0,c), k, -1); hi = ray(P2(a1,c), k, 1);
  }
  return {hz, k, a0, a1, c, P2, mid, at, side, perpTo, lo, hi, n: hz ? [0,-side] : [side,0]};
}
// one dimension chain per line: frames on the same line share it (outline → frame → gap → frame → outline)
function lfrsDims(group){
  const gs = group.map(l => Object.assign(lfrsGeom(l), {l})).sort((p,q) => Math.min(p.a0,p.a1) - Math.min(q.a0,q.a1)), g = gs[0], last = gs[gs.length-1]; let s = '';
  const pts = [];   // [value, is the start of a frame bay]
  if(g.lo !== null && g.a0-g.lo > EPS) pts.push([g.lo, false]);
  gs.forEach(f => { const prev = pts.length ? pts[pts.length-1][0] : -Infinity;
    if(f.a0 > prev + EPS) pts.push([f.a0, true]); else if(pts.length) pts[pts.length-1][1] = true;
    if(isX2(f.l)){ const fc = frameCols(f.l), m = f.a0 + (f.a1 - f.a0)*fc[1]/fc[2]; pts.push([m, true]); }   // two-bay X: both bays dimensioned
    if(f.a1 > Math.max(prev, f.a0) + EPS) pts.push([f.a1, false]); });
  const hiEnd = pts[pts.length-1][0];
  if(last.hi !== null && last.hi-hiEnd > EPS) pts.push([last.hi, false]);
  // selected frame on this line: the gaps on either side of it are editable (it moves along the line; nothing else moves)
  const selL = (sel && sel.type==='lfrs') ? PL().lfrs[sel.idx] : null, fs = selL ? gs.find(f => f.l === selL) : null;
  let iLo = -1, iHi = -1, fLo = 0, fHi = 0, bnd = null, gapLo = false, gapHi = false;
  if(fs){
    fLo = Math.min(fs.a0, fs.a1); fHi = Math.max(fs.a0, fs.a1);
    iLo = pts.findIndex(p => Math.abs(p[0]-fLo) < EPS); iHi = pts.findIndex(p => Math.abs(p[0]-fHi) < EPS);
    // how far it can move: to the outline, or to the nearest frame on the same line (a touching neighbor leaves no gap on that side)
    const others = gs.filter(f => f !== fs).map(f => [Math.min(f.a0, f.a1), Math.max(f.a0, f.a1)]);
    const below = others.filter(o => o[1] <= fLo + EPS).map(o => o[1]), above = others.filter(o => o[0] >= fHi - EPS).map(o => o[0]);
    bnd = {lo: Math.max(fs.lo !== null ? Math.min(fs.lo, fLo) : -Infinity, ...below), hi: Math.min(fs.hi !== null ? Math.max(fs.hi, fHi) : Infinity, ...above)};
    gapLo = iLo > 0 && fLo - bnd.lo > EPS; gapHi = iHi >= 0 && iHi < pts.length-1 && bnd.hi - fHi > EPS;
  }
  for(let i=0;i<pts.length-1;i++){
    const wall = pts[i][1];
    let ed = null;
    if(fs && gapLo && i === iLo-1) ed = {kind:'gap', side:'lo', idx:sel.idx, other:pts[i][0], fLo, fHi, bnd};
    else if(fs && gapHi && i === iHi) ed = {kind:'gap', side:'hi', idx:sel.idx, other:pts[i+1][0], fLo, fHi, bnd};
    s += dimS(toS(g.P2(pts[i][0],g.c)), toS(g.P2(pts[i+1][0],g.c)), g.n, 18, ft(pts[i+1][0]-pts[i][0]), wall ? 'lfd strong' : 'lfd', true, ed);
  }
  // offset from the outline (frames inside the plan): drawn for the line's first frame, and for the selected frame (editable)
  const perpEd = f => ({kind:'perp', idx:sel.idx, c:f.c, perpTo:f.perpTo, a0:f.a0, a1:f.a1, hz:f.hz});
  if(g.perpTo !== null && Math.abs(g.perpTo-g.c) > EPS){
    s += dimS(toS(g.P2(g.at,g.c)), toS(g.P2(g.at,g.perpTo)), g.hz ? [1,0] : [0,-1], 0, ft(Math.abs(g.perpTo-g.c)), 'lfd', false, fs === g ? perpEd(g) : null);
  }
  if(fs && fs !== g && fs.perpTo !== null && Math.abs(fs.perpTo-fs.c) > EPS){
    s += dimS(toS(fs.P2(fs.at,fs.c)), toS(fs.P2(fs.at,fs.perpTo)), fs.hz ? [1,0] : [0,-1], 0, ft(Math.abs(fs.perpTo-fs.c)), 'lfd', false, perpEd(fs));
  }
  return s;
}
function lfrsLabel(l){
  const g = lfrsGeom(l), P = toS(g.P2(g.mid, g.c));
  const off = [-g.n[0]*15, -g.n[1]*15];
  const anchor = g.hz ? 'middle' : (off[0] > 0 ? 'start' : 'end');
  return tx(P[0]+off[0], P[1]+off[1], l.label + (isX2(l) ? ' · 2X' : ''), 'lflbl', false, anchor);
}
function vertexNums(st){
  const v = PL().verts, n = v.length; let s = '';
  const ccw = st ? st.sA > 0 : true;
  v.forEach((p,i) => {
    const S0 = toS(p); let dir = [-0.7, 0.7];
    if(PL().closed){
      const pr = v[(i-1+n)%n], nx = v[(i+1)%n];
      const nrm = (a,b) => { const dx=b[0]-a[0], dy=b[1]-a[1], L=Math.hypot(dx,dy)||1; return ccw ? [dy/L, -dx/L] : [-dy/L, dx/L]; };
      const n1 = nrm(pr,p), n2 = nrm(p,nx), d = [n1[0]+n2[0], n1[1]+n2[1]], L = Math.hypot(d[0],d[1]) || 1;
      dir = [d[0]/L, d[1]/L];
    }
    s += tx(S0[0] + dir[0]*13, S0[1] - dir[1]*13, String(i+1), 'vnum');
  });
  return s;
}
function axesGlyph(){
  const x = 26, y = PH - 26;
  return ln(x, y, x+34, y, 'ax') + ln(x, y, x, y-34, 'ax') +
    `<path d="M${x+42} ${y} L${x+32} ${y-5} L${x+32} ${y+5}Z" class="axh"/><path d="M${x} ${y-42} L${x-5} ${y-32} L${x+5} ${y-32}Z" class="axh"/>` +
    tx(x+48, y, 'X', 'axt') + tx(x-4, y-52, 'Y', 'axt');
}

function render(){
  if(!planEl) return;
  dimEdits = [];
  const pl = PL(), v = pl.verts, o = OPT(); let s = '';
  // grid
  const [x0, y1] = toW(0,0), [x1, y0] = toW(PW,PH);
  let st = gstep(); const mult = [2,2.5,2]; let mi = 0;
  while(st*view.s < 9) st *= mult[mi++ % 3];
  for(let i=Math.ceil(x0/st); i*st<=x1; i++){ const X = toS([i*st,0])[0]; s += ln(X,0,X,PH, i%5===0 ? 'gM' : 'gm'); }
  for(let i=Math.ceil(y0/st); i*st<=y1; i++){ const Y = toS([0,i*st])[1]; s += ln(0,Y,PW,Y, i%5===0 ? 'gM' : 'gm'); }
  const O = toS([0,0]); s += ln(O[0]-7,O[1],O[0]+7,O[1],'org') + ln(O[0],O[1]-7,O[0],O[1]+7,'org');
  // outline
  if(v.length){
    const pts = v.map(p => toS(p).map(f1).join(',')).join(' ');
    s += pl.closed ? `<polygon points="${pts}" class="poly"/>` : `<polyline points="${pts}" class="draft"/>`;
  }
  [[hover,'ehov'],[sel,'esel']].forEach(([h,c]) => {
    if(h && h.type==='edge' && pl.closed && v[h.idx]){ const a = toS(v[h.idx]), b = toS(v[(h.idx+1)%v.length]); s += ln(a[0],a[1],b[0],b[1],c); }
  });
  // segment lengths while drafting the outline
  if(!pl.closed) for(let i=0;i<v.length-1;i++){
    const a = toS(v[i]), b = toS(v[i+1]), hz = Math.abs(a[1]-b[1]) < 1;
    s += tx((a[0]+b[0])/2 + (hz?0:-16), (a[1]+b[1])/2 + (hz?-12:0), ft(Math.hypot(v[i+1][0]-v[i][0], v[i+1][1]-v[i][1])), 'seglbl', !hz);
  }
  // load areas (under the frames): translucent by ID, clipped to the outline; uncovered parts hatched; overlaps in red
  const D = LVP();
  if(pl.closed && laShown() && (D.la || []).length){
    const poly = v.map(p => toS(p).map(f1).join(',')).join(' ');
    s += `<defs><clipPath id="laClip"><polygon points="${poly}"/></clipPath><pattern id="laHatch" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="9" class="lahatch"/></pattern></defs>`;
    const LS = laStats(), sr = r => { const a = toS([r.x1, r.y2]), b = toS([r.x2, r.y1]); return `x="${f1(a[0])}" y="${f1(a[1])}" width="${f1(b[0]-a[0])}" height="${f1(b[1]-a[1])}"`; };
    s += '<g clip-path="url(#laClip)">';
    D.la.forEach(r0 => { const r = laR(r0), c = laCol(r0.id); s += `<rect ${sr(r)} class="la" style="fill:color-mix(in srgb, ${c} 30%, transparent);stroke:${c}"/>`; });
    laGaps().forEach(g => { s += `<rect ${sr(g)} class="lagap"/>`; });
    if(LS.overlap){ const a = laR(D.la[LS.overlap[0]]), b = laR(D.la[LS.overlap[1]]); s += `<rect ${sr({x1:Math.max(a.x1,b.x1), x2:Math.min(a.x2,b.x2), y1:Math.max(a.y1,b.y1), y2:Math.min(a.y2,b.y2)})} class="laov"/>`; }
    s += '</g>';
    D.la.forEach((r0,i) => { const g = LS.rects[i]; if(!g || !(g.A > 0)) return; const P = toS([g.cx, g.cy]), w = 10 + 8*String(r0.id).length;
      s += `<rect x="${f1(P[0]-w/2)}" y="${f1(P[1]-11)}" width="${f1(w)}" height="22" rx="3" class="lalb"/>` + tx(P[0], P[1], String(r0.id), 'lalt'); });
    if(sel && sel.type==='la' && D.la[sel.idx]) s += `<rect ${sr(laR(D.la[sel.idx]))} class="lasel"/>`;
  }
  if(mode==='loads' && laStart && snapR){ const a = laStart, b = snapR.p; s += `<rect ${(() => { const A = toS([Math.min(a[0],b[0]), Math.max(a[1],b[1])]), B = toS([Math.max(a[0],b[0]), Math.min(a[1],b[1])]); return `x="${f1(A[0])}" y="${f1(A[1])}" width="${f1(B[0]-A[0])}" height="${f1(B[1]-A[1])}"`; })()} class="larub"/>`;
    const w = Math.abs(b[0]-a[0]), d = Math.abs(b[1]-a[1]); if(w > EPS && d > EPS){ const P = toS([(a[0]+b[0])/2, Math.max(a[1],b[1])]); s += tx(P[0], P[1]-10, `${ft(w)} × ${ft(d)}`, 'rubtx'); } }
  // LFRS lines
  pl.lfrs.forEach((l,i) => {
    const a = toS([l.x1,l.y1]), b = toS([l.x2,l.y2]);
    if(sel && sel.type==='lfrs' && sel.idx===i) s += ln(a[0],a[1],b[0],b[1],'lfsel');
    else if(hover && hover.type==='lfrs' && hover.idx===i) s += ln(a[0],a[1],b[0],b[1],'lfhov');
    s += ln(a[0],a[1],b[0],b[1],'lf');
    // braced frame: a column square at every column; a triangle at the top of each story-1 brace, pointing up the brace
    const Lw = lLen(l), cols = frameCols(l), Ls = Math.hypot(b[0]-a[0], b[1]-a[1]), P = off => [a[0] + (b[0]-a[0])*off/Lw, a[1] + (b[1]-a[1])*off/Lw];
    cols.forEach(off => { const C = P(off); s += `<rect x="${f1(C[0]-4.5)}" y="${f1(C[1]-4.5)}" width="9" height="9" class="bfcol"/>`; });
    if(o.bdir && Ls > 30){
      const u0 = [(b[0]-a[0])/Ls, (b[1]-a[1])/Ls], nrm = [-u0[1]*6, u0[0]*6];
      storyBraces(l, 1).forEach(br => {
        const dirn = cols[br.to] > cols[br.from] ? 1 : -1, u = [u0[0]*dirn, u0[1]*dirn], E0 = P(cols[br.to]);
        const tip = [E0[0]-u[0]*10, E0[1]-u[1]*10], base = [tip[0]-u[0]*11, tip[1]-u[1]*11];
        s += `<path d="M${f1(tip[0])} ${f1(tip[1])} L${f1(base[0]+nrm[0])} ${f1(base[1]+nrm[1])} L${f1(base[0]-nrm[0])} ${f1(base[1]-nrm[1])}Z" class="bfdir"/>`;
        if(l.alt && !isX2(l)){ const m = [base[0]-u[0]*13, base[1]-u[1]*13]; s += `<text x="${f1(m[0])}" y="${f1(m[1])}" class="bfalt">alt</text>`; }
      });
    }
  });
  // dimensions
  const stt = polyStats();
  if(stt && o.chain) s += outlineDims(stt);
  if(o.lfdim){ const grp = new Map(); pl.lfrs.forEach(l => { if(!(lLen(l) > 0)) return; const key = lDir(l) + ':' + rnd(lDir(l)==='X' ? l.y1 : l.x1); if(!grp.has(key)) grp.set(key, []); grp.get(key).push(l); });
    grp.forEach(gr => { s += lfrsDims(gr); }); }
  pl.lfrs.forEach(l => { s += lfrsLabel(l); });
  if(o.nums && v.length) s += vertexNums(stt);
  if(stt && o.cm && ok(stt.cx)){
    const c = toS(cmPt(stt, padLv()));
    s += `<circle cx="${f1(c[0])}" cy="${f1(c[1])}" r="6" class="cm"/>` + ln(c[0]-10,c[1],c[0]+10,c[1],'cm') + ln(c[0],c[1]-10,c[0],c[1]+10,'cm') + tx(c[0]+10, c[1]-11, 'C.M.', 'cmtx');
  }
  const RP = rigidProps(padLv());
  if(o.cr && ok(RP.xR) && ok(RP.yR)){
    const c = toS([RP.xR, RP.yR]);
    s += `<rect x="${f1(c[0]-6)}" y="${f1(c[1]-6)}" width="12" height="12" class="cr" transform="rotate(45 ${f1(c[0])} ${f1(c[1])})"/>` + ln(c[0]-11,c[1],c[0]+11,c[1],'cr') + ln(c[0],c[1]-11,c[0],c[1]+11,'cr') + tx(c[0]+11, c[1]+13, 'C.R.', 'crtx');
  }
  // rubber band
  const from = fromPt();
  if(from && snapR){
    const a = toS(from), b = toS(snapR.p), hz = Math.abs(a[1]-b[1]) < 1;
    s += ln(a[0],a[1],b[0],b[1], mode==='lfrs' ? 'rub lfr' : 'rub');
    const L = Math.hypot(snapR.p[0]-from[0], snapR.p[1]-from[1]);
    if(L > EPS) s += tx((a[0]+b[0])/2 + (hz?0:-16), (a[1]+b[1])/2 + (hz?-13:0), ft(L), 'rubtx', !hz);
  }
  // snap guides and marker
  if(snapR){
    snapR.guides.forEach(([q,p]) => { const a = toS(q), b = toS(p); s += ln(a[0],a[1],b[0],b[1],'guide'); });
    const P = toS(snapR.p);
    if(snapR.kind==='vertex') s += `<rect x="${f1(P[0]-5)}" y="${f1(P[1]-5)}" width="10" height="10" class="snapm"/>`;
    else if(snapR.kind==='edge') s += `<path d="M${f1(P[0]-6)} ${f1(P[1]+5)} L${f1(P[0])} ${f1(P[1]-6)} L${f1(P[0]+6)} ${f1(P[1]+5)}Z" class="snapm"/>`;
    else s += `<circle cx="${f1(P[0])}" cy="${f1(P[1])}" r="4" class="snapm"/>`;
    if(mode==='outline' && !pl.closed && v.length >= 3 && same(snapR.p, v[0])) s += `<circle cx="${f1(P[0])}" cy="${f1(P[1])}" r="10" class="closem"/>`;
  }
  if(lfStart){ const P = toS(lfStart); s += `<circle cx="${f1(P[0])}" cy="${f1(P[1])}" r="4.5" class="axh"/>`; }
  s += axesGlyph();
  planEl.innerHTML = s;
  hintUpdate(); readout();
}
function hintUpdate(){
  const pl = PL(); let h = '';
  if(mode==='outline'){
    if(pl.closed) h = 'Outline is closed. Choose <b>Braced frames</b> to add frames, or <b>Select / move</b> to adjust edges.';
    else if(!pl.verts.length) h = 'Click to place the first corner of the floor plan. Points snap to the grid and line up with existing points.';
    else h = 'Click the next corner (segments stay orthogonal), or <b>type a length + Enter</b> in the direction of the cursor. Click corner 1 or press <b>Enter</b> to close. Backspace removes the last corner.';
  } else if(mode==='lfrs'){
    h = lfStart ? 'Click the end of the line, or <b>type a length + Enter</b>. Esc cancels.'
                : 'Click the first column of a braced bay (the line runs column to column). Snaps to the outline, corners and grid.';
  } else if(mode==='loads'){
    h = !pl.closed ? 'Close the floor plan outline first.' : laStart ? 'Click the opposite corner of the load area. Esc cancels.'
      : 'Click one corner of a load area, then the opposite corner. Corners snap to the outline, other load areas and the grid. New areas get the highlighted ID in <b>Loading areas</b>. Areas cannot overlap and must cover the whole plan.';
  } else if(sel && sel.type==='la'){
    h = 'Load area selected: drag to move it, Delete removes it, or click an ID in <b>Loading areas</b> to change its ID.';
  } else h = (sel && sel.type==='lfrs') ? 'Frame selected: click one of its blue dimensions and type a new value (Enter applies, Esc cancels); the frame moves to suit. Drag to move it freely; Delete removes it.' : 'Click a line or outline edge to select it. Drag to move it (outline edges move perpendicular). Delete removes a selected line. Drag empty space to pan; click the canvas, then scroll to zoom.';
  $('#planHint').innerHTML = h;
  const tEl = $('#planTyped');
  if(typed && curS){ tEl.hidden = false; tEl.textContent = `Length: ${typed} ft ↵`; tEl.style.left = (curS[0]+16)+'px'; tEl.style.top = (curS[1]+14)+'px'; }
  else tEl.hidden = true;
}
function readout(){
  const p = snapR ? snapR.p : cur;
  $('#planReadout').textContent = (p ? `X ${ft(p[0])}   Y ${ft(p[1])}   ·   ` : '') + `grid ${gstep()} ft`;
}

/* ---- wall thickness panel ---- */
/* ---- braced frames panel ---- */
const E_STEEL = 29000;   // ksi, same for every brace (cancels in the distribution)
const flipEnd = e => e === 'a' ? 'b' : 'a';
// end ('a' = start x1/y1, 'b' = end x2/y2) that the brace in story i rises to
function topEnd(l, i){ const up = l.up === 'a' ? 'a' : 'b'; return (l.alt && i % 2 === 0) ? flipEnd(up) : up; }
const endCoord = (l, e) => lDir(l) === 'X' ? (e === 'a' ? l.x1 : l.x2) : (e === 'a' ? l.y1 : l.y2);
const endArrow = (l, e) => lDir(l) === 'X' ? (e === 'b' ? '→' : '←') : (e === 'b' ? '↑' : '↓');
const endName = (l, e) => lDir(l) === 'X' ? (e === 'b' ? 'end (x2)' : 'start (x1)') : (e === 'b' ? 'end (y2)' : 'start (y1)');
function nStories(){ return S.levels ? S.levels.length : 0; }
function dispStory(){ const n = nStories(), v = Math.round(num(S.plan.kStory)); return v >= 1 && v <= n ? v : 1; }
function storyH(i){ const L = S.levels; if(!L || !(i >= 1 && i <= L.length)) return NaN; return num(L[i-1].h) - (i > 1 ? num(L[i-2].h) : 0); }
// frame geometry and lateral stiffness in story i: k = A·E·cos²θ / L_br = A·E·Lb²/Lbr³ (kip/in, A in², lengths in ft → in)
// Frame types: 'single' = one diagonal per story in one bay; 'x2' = two-bay X (two stories form one X over both bays,
// the braces meet at the middle column: Λ in one story, V in the next).
const isX2 = l => l.type === 'x2';
function frameCols(l){ const L = lLen(l); if(!isX2(l)) return [0, L]; let m = num(l.L1); if(!(m > 0 && m < L)) m = L/2; return [0, m, L]; }   // column offsets from the start
const colName = (l, c) => isX2(l) ? ['start', 'middle', 'end'][c] : ['start', 'end'][c];
// x2: story with both braces meeting at the middle column at the top (Λ). mid = 'top' → story 1 is Λ; 'bot' → story 1 is V.
const lamStory = (l, i) => (l.mid === 'bot' ? 0 : 1) === (i % 2);
// braces in story i: from / to = column index at the bottom / top of the brace
function storyBraces(l, i){
  if(!isX2(l)) return topEnd(l, i) === 'b' ? [{bay:0, from:0, to:1}] : [{bay:0, from:1, to:0}];
  return lamStory(l, i) ? [{bay:0, from:0, to:1}, {bay:1, from:2, to:1}] : [{bay:0, from:1, to:0}, {bay:1, from:1, to:2}];
}
// frame geometry and lateral stiffness in story i. Each brace: k = A·E·cos²θ / L_br (kip/in); braces in a story act in parallel (k = Σ).
function frameGeo(l, i){
  const cols = frameCols(l), h = storyH(i), A = braceA(l, i);
  const br = storyBraces(l, i).map(b => {
    const Lb = Math.abs(cols[b.to] - cols[b.from]), Lbr = Math.hypot(Lb, h), c = Lb / Lbr;
    const k = (A > 0 && Lb > 0 && h > 0) ? A*E_STEEL*c*c/(Lbr*12) : NaN;
    return Object.assign({}, b, {Lb, Lbr, cos:c, sin:h/Lbr, theta: Math.atan2(h, Lb)*180/Math.PI, k, up: cols[b.to] > cols[b.from] ? 1 : -1});
  });
  const k = br.reduce((t,b) => t + b.k, 0);
  br.forEach(b => { b.share = k > 0 ? b.k / k : NaN; });
  return {Lb:lLen(l), h, Lbr:br[0].Lbr, cos:br[0].cos, theta:br[0].theta, A, k, top: topEnd(l, i), braces:br, cols, x2:isX2(l), lam: isX2(l) ? lamStory(l, i) : null};
}
// where the braces of story i meet the floor above (level i): along-line offsets and the share of the frame force at each
function frameConns(l, i){
  const g = frameGeo(l, i), m = new Map();
  g.braces.forEach(b => { const off = g.cols[b.to]; m.set(off, (m.get(off) || 0) + b.share); });
  return [...m.entries()].map(([off, frac]) => ({off, frac, col: g.cols.indexOf(off)}));
}
function wallK(l, i){ return frameGeo(l, i === undefined ? dispStory() : i).k; }   // relative stiffness used everywhere
function renderTPanel(){
  const L = PL().lfrs, el = $('#tpList'); if(!el) return;
  if(document.activeElement && el.contains(document.activeElement) && document.activeElement.tagName === 'INPUT') return;   // do not rebuild while typing
  $('#aDef').value = PL().aDef ?? '';
  if(!L.length){ el.innerHTML = '<div class="tp-empty">Draw braced frames to list them here.</div>'; return; }
  const ord = L.map((l,i) => ({l,i})).sort((a,b) => lDir(a.l).localeCompare(lDir(b.l)) || a.l.label.localeCompare(b.l.label, undefined, {numeric:true}));
  el.innerHTML = ord.map(({l,i}) => `<label class="tp-row${sel && sel.type==='lfrs' && sel.idx===i ? ' on' : ''}"><b>${esc(l.label)}</b><span class="muted">${ft(lLen(l))}</span>
    <input class="in" type="number" min="0" step="0.01" data-ta="${i}" value="${esc(braceA(l, padLv()))}" aria-label="Brace area of ${esc(l.label)} at ${esc(label(padLv()))}, in square inches">${isX2(l)
      ? `<button class="xbtn" type="button" data-tu="${i}" title="Two-bay X: story 1 braces meet at the middle column at the ${l.mid === 'bot' ? 'bottom (V)' : 'top (Λ)'}. Click to flip.">${l.mid === 'bot' ? 'V' : 'Λ'}</button>`
      : `<button class="xbtn" type="button" data-tu="${i}" title="Story-1 brace rises to the ${endName(l, topEnd(l,1))} end${l.alt ? ', alternating each story' : ''}. Click to flip.">${endArrow(l, topEnd(l,1))}${l.alt ? '<sup>⇅</sup>' : ''}</button>`}<button class="xbtn x2btn${isX2(l) ? ' on' : ''}" type="button" data-tx="${i}" title="${isX2(l) ? 'Two-bay X frame. Click to make it a single diagonal.' : 'Single diagonal. Click to make it a two-bay X (middle column at mid-length; set L1 in the frame table).'}">2X</button></label>`).join('');
}

/* ---- rigidity: frame stiffness per story ---- */
function rigidProps(story){
  const i = story === undefined ? dispStory() : story, K = l => wallK(l, i);
  const L = S.plan.lfrs, st = (S.plan.closed) ? polyStats() : null;
  const ys = L.filter(l => lDir(l)==='Y'), xs = L.filter(l => lDir(l)==='X');
  const sum = (a,f) => a.reduce((t,l) => t + f(l), 0);
  const kY = sum(ys, K), kX = sum(xs, K);
  const xR = kY > 0 ? sum(ys, l => K(l)*l.x1)/kY : NaN;
  const yR = kX > 0 ? sum(xs, l => K(l)*l.y1)/kX : NaN;
  const J = (ok(xR) ? sum(ys, l => K(l)*(l.x1-xR)**2) : 0) + (ok(yR) ? sum(xs, l => K(l)*(l.y1-yR)**2) : 0);
  const bad = L.some(l => !(K(l) > 0));
  return {st, ys, xs, kY, kX, xR, yR, J, bad, story:i, K};
}
// Rigid diaphragm: translation + rotation about the center of rigidity of story `story`. acc = +1 / -1 / 0 (accidental torsion, 5% of plan dimension)
// lvCM: the level whose centre of mass the force acts at (its own load areas); defaults to the level above the story
function rigidSolve(dir, F, acc, story, lvCM){
  const P = rigidProps(story), st = P.st;
  if(!st) return {err:'Draw and close the floor plan outline in section 2 first.'};
  if(P.bad) return {err: !(storyH(P.story) > 0) ? `Story ${P.story} has no height. Check the level heights in section 3.` : 'Every braced frame needs a brace area A (section 2) for the rigid analysis.'};
  const kD = dir==='Y' ? P.kY : P.kX;
  if(!(kD > 0)) return {err:`No braced frames are parallel to ${dir}. Draw at least one ${dir} frame in section 2.`};
  const Lx = st.maxx-st.minx, Ly = st.maxy-st.miny, a = ok(acc) ? acc : 0;
  const cm = cmPt(st, lvCM !== undefined ? lvCM : P.story);   // area centroid, or that level's weight centroid with load areas
  let e, T, dX = 0, dY = 0, xF = cm[0], yF = cm[1];
  if(dir==='Y'){ e = a*0.05*Lx; xF = cm[0] + e; T = F*(xF - P.xR); dY = F/P.kY; }   // T counterclockwise +
  else         { e = a*0.05*Ly; yF = cm[1] + e; T = -F*(yF - P.yR); dX = F/P.kX; }
  let th = 0;
  if(Math.abs(T) > 1e-12*Math.max(1, Math.abs(F))*(Lx+Ly)){
    if(!(P.J > 0)) return {err:'The frame layout cannot resist torsion (all frames on one line, with no perpendicular frames). Add frames so the rigid diaphragm is stable.'};
    th = T/P.J;
  }
  const W = new Map();
  S.plan.lfrs.forEach(l => {
    const k = P.K(l);
    if(lDir(l)==='Y'){ const dir_ = k*dY, tor = k*th*(l.x1-P.xR); W.set(l, {direct:dir_, tors:tor, f:dir_+tor, k}); }
    else             { const dir_ = k*dX, tor = -k*th*(l.y1-P.yR); W.set(l, {direct:dir_, tors:tor, f:dir_+tor, k}); }
  });
  return {W, P, e, T, th, xF, yF, cm, Lx, Ly, acc:a, story:P.story};
}

/* ---- tables & stats ---- */
function renderPlanTables(){
  const v = PL().verts, n = v.length, cl = PL().closed;
  let h = '';
  v.forEach((p,i) => {
    const q = (i < n-1 || cl) ? v[(i+1)%n] : null;
    const edge = q ? `${i+1}→${(i+1)%n+1}: <b>${ft(Math.hypot(q[0]-p[0], q[1]-p[1]))}</b> <span class="muted">(${Math.abs(q[1]-p[1])<EPS ? 'X' : 'Y'})</span>` : '—';
    h += `<tr><td><b>${i+1}</b></td>
      <td><input class="in" type="number" step="any" data-v="${i}" data-a="0" value="${p[0]}" aria-label="X of vertex ${i+1}"></td>
      <td><input class="in" type="number" step="any" data-v="${i}" data-a="1" value="${p[1]}" aria-label="Y of vertex ${i+1}"></td>
      <td>${edge}</td></tr>`;
  });
  $('#tv tbody').innerHTML = h || '<tr><td colspan="4" class="empty">No outline yet. Draw it on the canvas or use Quick rectangle.</td></tr>';
  const ks = dispStory();
  document.querySelectorAll('#tl .kst').forEach(e => { e.textContent = ks; });
  let g = '';
  PL().lfrs.forEach((l,i) => {
    const d = lDir(l), up = l.up === 'a' ? 'a' : 'b';
    g += `<tr class="${sel && sel.type==='lfrs' && sel.idx===i ? 'rowsel' : ''}">
      <td><input class="name-in" data-l="${i}" data-f="label" value="${esc(l.label)}" aria-label="Label"></td>
      <td><select class="mini" data-l="${i}" data-f="dir"><option${d==='X'?' selected':''}>X</option><option${d==='Y'?' selected':''}>Y</option></select></td>
      <td><input class="in" type="number" step="any" data-l="${i}" data-f="x1" value="${l.x1}"></td>
      <td><input class="in" type="number" step="any" data-l="${i}" data-f="y1" value="${l.y1}"></td>
      <td><input class="in" type="number" step="any" min="0" data-l="${i}" data-f="len" value="${rnd(lLen(l))}"></td>
      <td><select class="mini" data-l="${i}" data-f="type"><option value="single"${!isX2(l)?' selected':''}>Single</option><option value="x2"${isX2(l)?' selected':''}>2X</option></select>${isX2(l) ? `<input class="in" type="number" step="any" min="0" data-l="${i}" data-f="L1" value="${rnd(frameCols(l)[1])}" style="width:52px;margin-left:6px" title="L₁ = middle column distance from the start column, ft" aria-label="Middle column distance from start">` : ''}</td>
      <td><input class="in" type="number" step="0.01" min="0" data-l="${i}" data-f="A" value="${esc(braceA(l, padLv()))}"></td>
      <td>${isX2(l) ? `<select class="mini" data-l="${i}" data-f="mid"><option value="top"${l.mid!=='bot'?' selected':''}>Λ at top</option><option value="bot"${l.mid==='bot'?' selected':''}>V at bottom</option></select>` : `<select class="mini" data-l="${i}" data-f="up"><option value="a"${up==='a'?' selected':''}>${endArrow(l,'a')} ${endName(l,'a')}</option><option value="b"${up==='b'?' selected':''}>${endArrow(l,'b')} ${endName(l,'b')}</option></select>`}</td>
      <td>${isX2(l) ? '<span class="muted">auto</span>' : `<input type="checkbox" data-l="${i}" data-f="alt"${l.alt ? ' checked' : ''} aria-label="Alternate brace direction each story" style="width:16px;height:16px;accent-color:var(--input)">`}</td>
      <td>${fmt(wallK(l, ks),1)}</td>
      <td>${ft(l.x2)}, ${ft(l.y2)}</td>
      <td><button class="xbtn" data-del="${i}" title="Delete frame" aria-label="Delete frame">×</button></td></tr>`;
  });
  $('#tl tbody').innerHTML = g || '<tr><td colspan="12" class="empty">No frames yet. Choose Braced frames and click the two columns of each braced bay.</td></tr>';
  renderTPanel(); renderStiff();
}
function renderStiff(){
  const tb = $('#tK'); if(!tb) return;
  const L = PL().lfrs, n = nStories(), ks = dispStory();
  const opts = Array.from({length:n}, (_,j) => j+1).reverse().map(i => `<option value="${i}"${i===ks?' selected':''}>${label(i)} · story ${i}, ${label(i)} to ${i>1 ? label(i-1) : 'base'} · h = ${ok(storyH(i)) ? ft(storyH(i)) : '—'}</option>`).join('');
  if($('#kStory').innerHTML !== opts) $('#kStory').innerHTML = opts;
  $('#kStory').value = String(ks);
  const RP = rigidProps(ks);
  $('#kCR').value = `x = ${ok(RP.xR) ? ft(RP.xR) : '—'}, y = ${ok(RP.yR) ? ft(RP.yR) : '—'}`;
  const hs = Array.from({length:n}, (_,j) => storyH(j+1)).filter(ok), same = hs.length && hs.every(h => Math.abs(h - hs[0]) < 1e-9);
  $('#stSum').textContent = `k = A·E·Lb²/Lbr³ per story · ${same ? `all stories h = ${ft(hs[0])}` : 'story heights vary, so frame shares vary by story'} · showing story ${ks}`;
  tb.querySelector('thead').innerHTML = `<tr><th>Frame</th><th>Dir.</th><th>Bay, L<sub>b</sub><br><span>ft (2X: L₁, L₂)</span></th><th>Story h<br><span>ft</span></th><th>Brace, L<sub>br</sub><br><span>ft</span></th><th>θ<br><span>deg</span></th><th>cos²θ</th><th>A<br><span>in²</span></th><th>k = Σ A·E·cos²θ / L<sub>br</sub><br><span>kip/in (2X: k₁ + k₂)</span></th><th>k / Σk<br><span>same direction</span></th><th>Brace rises to<br><span>this story (2X: Λ or V)</span></th></tr>`;
  if(!L.length){ tb.querySelector('tbody').innerHTML = '<tr><td colspan="11" class="empty">No frames yet.</td></tr>'; return; }
  const SK = {X:RP.kX, Y:RP.kY};
  const ord = L.slice().sort((a,b) => lDir(a).localeCompare(lDir(b)) || a.label.localeCompare(b.label, undefined, {numeric:true}));
  const per = (g, f, d) => g.braces.map(b => fmt(f(b), d)).join('<br>');   // 2X: bay 1 over bay 2
  tb.querySelector('tbody').innerHTML = ord.map(l => { const g = frameGeo(l, ks), d = lDir(l);
    const pat = g.x2 ? (g.lam ? 'Λ <span class="muted">meet at middle col., top</span>' : 'V <span class="muted">leave middle col., bottom</span>') : `${endArrow(l, g.top)} ${endName(l, g.top)}${l.alt ? ' <span class="muted">(alt.)</span>' : ''}`;
    return `<tr><td><b>${esc(l.label)}</b>${g.x2 ? ' <span class="gtag">2X</span>' : ''}</td><td>${d}</td><td>${per(g, b => b.Lb, 2)}</td><td>${fmt(g.h,2)}</td><td>${per(g, b => b.Lbr, 2)}</td><td>${per(g, b => b.theta, 1)}</td><td>${per(g, b => b.cos*b.cos, 3)}</td><td>${fmt(g.A,2)}</td><td><b>${fmt(g.k,1)}</b>${g.x2 ? `<br><span class="muted">${g.braces.map(b => fmt(b.k,1)).join(' + ')}</span>` : ''}</td><td>${SK[d] > 0 && ok(g.k) ? fmt(g.k/SK[d]*100,1)+'%' : '—'}</td><td>${pat}</td></tr>`; }).join('');
}
function planStatsUI(){
  const st = polyStats(), L = PL().lfrs;
  const nx = L.filter(l => lDir(l)==='X').length, ny = L.length - nx;
  $('#ps-A').textContent = st ? fmt(st.A,1) + ' ft²' : '—';
  $('#ps-P').textContent = st ? fmt(st.per,1) + ' ft' : '—';
  $('#ps-L').textContent = st ? `${ft(st.maxx-st.minx)} × ${ft(st.maxy-st.miny)}` : '—';
  $('#ps-C').textContent = st && ok(st.cx) ? `${ft(st.cx)}, ${ft(st.cy)}` : '—';
  $('#ps-n').textContent = `${nx} X · ${ny} Y frames`;
  const RP = rigidProps(padLv());
  $('#ps-R').textContent = `${ok(RP.xR) ? ft(RP.xR) : '—'}, ${ok(RP.yR) ? ft(RP.yR) : '—'}`;
  $('#ps-Rn').textContent = `${label(RP.story)} (drawing pad level), braces below it`;
  const cmE = cmPt(st, padLv());
  $('#ps-e').textContent = st && (ok(RP.xR) || ok(RP.yR)) ? `${ok(RP.xR) ? ft(cmE[0]-RP.xR) : '—'}, ${ok(RP.yR) ? ft(cmE[1]-RP.yR) : '—'}` : '—';
  const m = [];
  if(st && selfIntersects()) m.push('The outline crosses or touches itself. Adjust the vertices so the edges only meet at consecutive corners.');
  if(PL().closed) L.forEach(l => { const mp = [(l.x1+l.x2)/2, (l.y1+l.y2)/2]; if(!pip(mp) && !onBoundary(mp)) m.push(`${l.label} lies outside the floor plan.`); });
  L.forEach(l => { const bad = S.levels.map((_,j) => j+1).filter(i => !(braceA(l, i) > 0)); if(bad.length) m.push(`${l.label}: enter a brace area A at ${bad.map(label).join(', ')}.`); if(!(lLen(l) > 0)) m.push(`${l.label}: the bay has no length.`);
    if(isX2(l) && !(num(l.L1) > 0 && num(l.L1) < lLen(l))) m.push(`${l.label}: middle column L1 must be between 0 and ${ft(lLen(l))}; the midpoint is used.`); });
  if(L.length && (nx===0 || ny===0)) m.push(`No braced frames in the ${nx===0 ? 'X' : 'Y'} direction yet. Frames parallel to X resist X-direction forces, and likewise for Y.`);
  $('#msgsP').innerHTML = m.map(t => `<div class="msg">${esc(t)}</div>`).join('');
}
function changed(){ renderPlanTables(); planStatsUI(); updSnap(); render(); update(); if(typeof renderLaPanel === 'function'){ const L = $('#laList'); if(L && L.contains(document.activeElement)) laFoot(); else renderLaPanel(); } }
function moveVertex(i, a, val){
  const v = PL().verts, n = v.length, cl = PL().closed, old = v[i].slice();
  v[i] = v[i].slice(); v[i][a] = val;
  const nb = [];
  if(i > 0 || cl) nb.push((i-1+n)%n);
  if(i < n-1 || cl) nb.push((i+1)%n);
  nb.forEach(j => { if(j !== i && Math.abs(v[j][a]-old[a]) < EPS){ v[j] = v[j].slice(); v[j][a] = val; } });
  if(cl) PL().verts = cleanup(v);
}
function syncPlanControls(){
  $('#gridSel').value = String(gstep());
  document.querySelectorAll('[data-opt]').forEach(c => { c.checked = !!OPT()[c.dataset.opt]; });
}

/* ---- events ---- */
function evS(e){ const r = planEl.getBoundingClientRect(); return [(e.clientX-r.left)*PW/r.width, (e.clientY-r.top)*PH/r.height]; }
function hitTest(sp){
  const L = PL().lfrs;
  for(let i=L.length-1;i>=0;i--) if(segDist(sp, toS([L[i].x1,L[i].y1]), toS([L[i].x2,L[i].y2])) < 7) return {type:'lfrs', idx:i};
  if(PL().closed) for(const e of polyEdges()) if(segDist(sp, toS(e.a), toS(e.b)) < 6) return {type:'edge', idx:e.i};
  if(laShown() && LVP().la){ const w = toW(...sp); for(let i=LVP().la.length-1;i>=0;i--){ const r = laR(LVP().la[i]); if(w[0] > r.x1 && w[0] < r.x2 && w[1] > r.y1 && w[1] < r.y2) return {type:'la', idx:i}; } }
  return null;
}
function doDrag(){
  if(drag.kind==='pan'){ view.ox = drag.v0.ox + curS[0]-drag.s0[0]; view.oy = drag.v0.oy + curS[1]-drag.s0[1]; render(); return; }
  const d = [sg(cur[0]-drag.w0[0]), sg(cur[1]-drag.w0[1])], o = drag.orig, t = drag.t;
  if(t.type==='la'){
    const r = LVP().la[t.idx], orr = o.lv[padLv()-1].la[t.idx];
    r.x1 = rnd(orr.x1+d[0]); r.x2 = rnd(orr.x2+d[0]); r.y1 = rnd(orr.y1+d[1]); r.y2 = rnd(orr.y2+d[1]);
    drag.moved = d[0]!==0 || d[1]!==0;
  } else if(t.type==='lfrs'){
    const l = PL().lfrs[t.idx], ol = o.lfrs[t.idx];
    l.x1 = rnd(ol.x1+d[0]); l.x2 = rnd(ol.x2+d[0]); l.y1 = rnd(ol.y1+d[1]); l.y2 = rnd(ol.y2+d[1]);
    drag.moved = d[0]!==0 || d[1]!==0;
  } else {
    const n = o.verts.length, i = t.idx, j = (i+1)%n, a = o.verts[i], b = o.verts[j], hz = Math.abs(a[1]-b[1]) < EPS;
    if(hz){ PL().verts[i] = [a[0], rnd(a[1]+d[1])]; PL().verts[j] = [b[0], rnd(b[1]+d[1])]; drag.moved = d[1]!==0; }
    else { PL().verts[i] = [rnd(a[0]+d[0]), a[1]]; PL().verts[j] = [rnd(b[0]+d[0]), b[1]]; drag.moved = d[0]!==0; }
  }
  render();
}
planEl.addEventListener('pointermove', e => {
  curS = evS(e); cur = toW(...curS);
  if(drag){ doDrag(); return; }
  updSnap();
  if(dimAt(curS)){ hover = null; wrap.style.cursor = 'text'; render(); return; }
  hover = mode==='select' ? hitTest(curS) : null;
  wrap.style.cursor = mode==='select' ? (hover ? 'move' : 'grab') : 'crosshair';
  render();
});
planEl.addEventListener('pointerleave', () => { if(drag) return; cur = null; curS = null; snapR = null; hover = null; render(); });
planEl.addEventListener('pointerdown', e => {
  e.preventDefault();
  if(!dimAt(evS(e))) wrap.focus({preventScroll:true});
  curS = evS(e); cur = toW(...curS);
  closeDimEd();
  if(e.button===0 && !spaceDown){ const d = dimAt(curS); if(d){ openDimEd(d); return; } }
  if(e.button===1 || (e.button===0 && spaceDown)){ e.preventDefault(); drag = {kind:'pan', s0:curS, v0:{...view}}; planEl.setPointerCapture(e.pointerId); return; }
  if(e.button===2){ cancel(); return; }
  if(e.button!==0) return;
  if(mode==='select'){
    const h = hitTest(curS);
    if(h){ sel = h; pushHist(); drag = {kind:'move', t:h, w0:cur, orig:JSON.parse(JSON.stringify(PL())), moved:false}; }
    else { sel = null; drag = {kind:'pan', s0:curS, v0:{...view}}; }
    planEl.setPointerCapture(e.pointerId); render(); renderPlanTables(); renderLaPanel(); return;
  }
  updSnap(); if(snapR) clickAt(snapR.p);
});
planEl.addEventListener('pointerup', () => {
  if(!drag) return;
  if(drag.kind==='move'){
    if(!drag.moved) hist.pop();
    else {
      if(drag.t.type==='edge'){ const n0 = PL().verts.length; PL().verts = cleanup(PL().verts); if(PL().verts.length !== n0) sel = null; }
      if(drag.t.type==='la'){ const r = LVP().la[drag.t.idx], hit = LVP().la.findIndex((q,i) => i !== drag.t.idx && laOverlapA(q, r) > 1e-6);
        if(hit >= 0){ S.plan = drag.orig; hist.pop(); planMsg(`Load areas cannot overlap (it would overlap area ${LVP().la[hit].id}); the move was undone.`); } }
      changed();
    }
  }
  drag = null; render();
});
planEl.addEventListener('contextmenu', e => e.preventDefault());
planEl.addEventListener('wheel', e => {
  if(document.activeElement !== wrap) return;
  e.preventDefault(); zoomAt(evS(e), e.deltaY < 0 ? 1.15 : 1/1.15);
}, {passive:false});
wrap.addEventListener('keydown', e => {
  const k = e.key;
  if((e.ctrlKey || e.metaKey) && k.toLowerCase()==='z'){ e.preventDefault(); undo(); return; }
  if(k===' '){ spaceDown = true; e.preventDefault(); return; }
  if(fromPt() && /^[0-9.]$/.test(k)){ typed += k; e.preventDefault(); render(); return; }
  if(k==='Enter'){
    e.preventDefault();
    if(typed) placeTyped();
    else if(mode==='outline' && !PL().closed && PL().verts.length >= 3){ pushHist(); closeOutline(); }
    return;
  }
  if(k==='Escape'){ cancel(); return; }
  if(k==='Backspace'){
    e.preventDefault();
    if(typed){ typed = typed.slice(0,-1); render(); }
    else if(mode==='outline' && !PL().closed && PL().verts.length){ pushHist(); PL().verts.pop(); changed(); }
    else if(sel && sel.type==='lfrs') delSel();
    return;
  }
  if(k==='Delete'){ delSel(); return; }
});
wrap.addEventListener('keyup', e => { if(e.key===' ') spaceDown = false; });
document.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => {
  if(b.dataset.mode==='outline' && PL().closed && mode!=='outline') armed(b, 'Click again: redraw outline', () => setMode('outline'));
  else if(b.dataset.mode!=='outline' || !PL().closed) setMode(b.dataset.mode);
}));
$('#btnUndo').addEventListener('click', undo);
$('#btnFit').addEventListener('click', fit);
$('#btnZin').addEventListener('click', () => zoomAt([PW/2,PH/2], 1.25));
$('#btnZout').addEventListener('click', () => zoomAt([PW/2,PH/2], 0.8));
$('#btnClearPlan').addEventListener('click', () => armed($('#btnClearPlan'), 'Click again: delete all', () => {
  pushHist(); PL().verts = []; PL().closed = false; PL().lfrs = []; LVP().la = [];
  sel = null; hover = null; lfStart = null; typed = '';
  setMode('outline', true); changed(); fit();
}));
$('#btnRect').addEventListener('click', () => {
  const w = num($('#rectW').value), d = num($('#rectD').value);
  if(!(w > 0 && d > 0)) return;
  pushHist(); PL().verts = [[0,0],[w,0],[w,d],[0,d]]; PL().closed = true;
  $('#planCard details.pop').open = false;
  setMode('lfrs', true); changed(); fit();
});
$('#gridSel').addEventListener('change', e => { PL().grid = num(e.target.value); changed(); });
$('#kStory').addEventListener('change', e => { PL().kStory = +e.target.value; changed(); });
$('#aDef').addEventListener('change', e => { const v = num(e.target.value); if(v > 0){ PL().aDef = v; save(); } else e.target.value = PL().aDef; });
$('#tpList').addEventListener('click', e => {
  const bx = e.target.closest('[data-tx]');
  if(bx){ e.preventDefault(); const l = PL().lfrs[+bx.dataset.tx]; if(!l) return; pushHist();
    l.type = isX2(l) ? 'single' : 'x2'; if(isX2(l) && !(num(l.L1) > 0 && num(l.L1) < lLen(l))) l.L1 = rnd(lLen(l)/2); if(l.mid !== 'bot') l.mid = 'top'; changed(); return; }
  const b = e.target.closest('[data-tu]'); if(!b) return; e.preventDefault();
  const l = PL().lfrs[+b.dataset.tu]; if(!l) return;
  pushHist(); if(isX2(l)) l.mid = l.mid === 'bot' ? 'top' : 'bot'; else l.up = l.up === 'a' ? 'b' : 'a'; changed();
});
$('#tpList').addEventListener('change', e => {
  const t = e.target; if(t.dataset.ta === undefined) return;
  const v = num(t.value), l = PL().lfrs[+t.dataset.ta]; if(!l) return;
  if(!(v > 0)){ t.value = braceA(l, padLv()); return; }
  pushHist(); l.As[padLv()-1] = v; t.blur(); changed();
});
document.querySelectorAll('[data-opt]').forEach(c => c.addEventListener('change', () => { OPT()[c.dataset.opt] = c.checked; save(); render(); }));
$('#tv tbody').addEventListener('change', e => {
  const t = e.target; if(t.dataset.v === undefined) return;
  const val = num(t.value); if(!ok(val)){ renderPlanTables(); return; }
  pushHist(); moveVertex(+t.dataset.v, +t.dataset.a, rnd(val)); changed();
});
$('#tl tbody').addEventListener('change', e => {
  const t = e.target; if(t.dataset.l === undefined) return;
  const l = PL().lfrs[+t.dataset.l], f = t.dataset.f; if(!l) return;
  pushHist();
  if(f==='label') l.label = t.value.trim() || l.label;
  else if(f==='A'){ const v = num(t.value); if(v > 0) l.As[padLv()-1] = v; }
  else if(f==='up'){ l.up = t.value === 'a' ? 'a' : 'b'; }
  else if(f==='alt'){ l.alt = t.checked; }
  else if(f==='type'){ l.type = t.value === 'x2' ? 'x2' : 'single'; if(isX2(l) && !(num(l.L1) > 0 && num(l.L1) < lLen(l))) l.L1 = rnd(lLen(l)/2); if(l.mid !== 'bot') l.mid = 'top'; }
  else if(f==='L1'){ const v = num(t.value); if(v > 0 && v < lLen(l)) l.L1 = rnd(v); }
  else if(f==='mid'){ l.mid = t.value === 'bot' ? 'bot' : 'top'; }
  else {
    const dir = f==='dir' ? t.value : lDir(l); let L = lLen(l);
    const v = num(t.value);
    if(f==='x1' && ok(v)) l.x1 = rnd(v);
    if(f==='y1' && ok(v)) l.y1 = rnd(v);
    if(f==='len' && v > 0) L = v;
    if(dir==='X'){ l.x2 = rnd(l.x1+L); l.y2 = l.y1; } else { l.x2 = l.x1; l.y2 = rnd(l.y1+L); }
  }
  changed();
});
$('#tl tbody').addEventListener('click', e => {
  const b = e.target.closest('[data-del]'); if(!b) return;
  pushHist(); PL().lfrs.splice(+b.dataset.del, 1); sel = null; changed();
});
function planReset(){
  renderHD(); renderCol(); renderChord(); renderVE();
  hist.length = 0; sel = null; hover = null; lfStart = null; typed = '';
  syncPlanControls(); setMode(PL().closed ? 'select' : 'outline', true);
  renderPlanTables(); planStatsUI(); renderLaPanel(); fit();
}
function initPlan(){
  syncPlanControls();
  mode = PL().closed ? 'select' : 'outline';
  document.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('on', b.dataset.mode === mode));
  renderPlanTables(); planStatsUI();
  new ResizeObserver(sizePlan).observe(wrap);
  sizePlan();
}

/* ---------- loading areas panel (section 2) and the section 3 switch ---------- */
function renderLaPanel(){
  const el = $('#laList'); if(!el) return;
  const pl = LVP(), LS = laStats(), selLa = (sel && sel.type==='la') ? pl.la[sel.idx] : null;
  el.innerHTML = pl.laIds.length ? pl.laIds.map((q,i) => { const b = LS.byId[q.id], on = selLa ? selLa.id === q.id : pl.laActive === q.id;
    return `<div class="la-row${on ? ' on' : ''}" data-lrow="${i}"><span class="la-sw" style="background:${laCol(q.id)}"></span><input class="in la-id" data-lid="${i}" value="${esc(q.id)}" aria-label="Load area ID"><input class="in" type="number" min="0" step="any" data-lpsf="${i}" value="${esc(q.psf)}" aria-label="psf for ID ${esc(q.id)}"><span class="la-a">${b ? fmt(b.A,0) : '0'}</span><button class="xbtn" data-ldel="${i}" title="Delete this ID and its load areas">×</button></div>`; }).join('')
    : '<div class="tp-empty">No IDs yet: add one, or draw with <b>Load areas</b>.</div>';
  laFoot();
}
function laFoot(){
  const f = $('#laFoot'); if(!f) return;
  const LS = laStats(), tip = 'Click a row: new areas use that ID; a selected area takes it.';
  if(!(LVP().la || []).length){ f.innerHTML = tip; return; }
  f.innerHTML = (LS.ok ? `<span class="good">Covers the whole plan ✓</span> W = <b>${fmt(LS.W,1)} k</b> per level, ${fmt(LS.W*1000/LS.A,1)} psf average`
                       : `<span class="bad">${esc(LS.err)}</span>`) + ((S.levels[padLv()-1] || {}).la ? '' : `<br>Not in use for ${esc(label(padLv()))}: tick its box in the section 3 psf column.`) + '<br>' + tip;
}
$('#laList').addEventListener('click', e => {
  const del = e.target.closest('[data-ldel]');
  if(del){
    const i = +del.dataset.ldel, id = LVP().laIds[i].id, nA = LVP().la.filter(r => r.id === id).length;
    const go = () => { pushHist(); LVP().la = LVP().la.filter(r => r.id !== id); LVP().laIds.splice(i,1); if(LVP().laActive === id) LVP().laActive = LVP().laIds.length ? LVP().laIds[0].id : ''; sel = null; changed(); renderLaPanel(); };
    if(nA) armed(del, '?', go); else go();
    return;
  }
  if(e.target.closest('input')) return;
  const row = e.target.closest('[data-lrow]'); if(!row) return;
  const id = LVP().laIds[+row.dataset.lrow].id;
  if(sel && sel.type==='la' && LVP().la[sel.idx]){ if(LVP().la[sel.idx].id !== id){ pushHist(); LVP().la[sel.idx].id = id; changed(); } }
  LVP().laActive = id; save(); renderLaPanel(); render();
});
$('#laList').addEventListener('change', e => {
  const t = e.target;
  if(t.dataset.lid !== undefined){
    const i = +t.dataset.lid, old = LVP().laIds[i].id, nu = t.value.trim();
    if(!nu || LVP().laIds.some((q,j) => j !== i && q.id === nu)){ planMsg(nu ? `ID ${nu} already exists.` : 'An ID cannot be blank.'); t.value = old; return; }
    pushHist(); LVP().laIds[i].id = nu; LVP().la.forEach(r => { if(r.id === old) r.id = nu; }); if(LVP().laActive === old) LVP().laActive = nu;
    t.blur(); changed(); renderLaPanel();
  }
});
$('#laList').addEventListener('input', e => {
  const t = e.target;
  if(t.dataset.lpsf !== undefined){ LVP().laIds[+t.dataset.lpsf].psf = t.value; changed(); }
});
$('#laList').addEventListener('keydown', e => { if(e.key === 'Enter' && e.target.matches('input')) e.target.blur(); });
$('#laAdd').addEventListener('click', () => {
  const pl = LVP(), L0 = S.levels[padLv()-1], id = laNewId(), psf = pl.laIds.length ? pl.laIds[pl.laIds.length-1].psf : (num(L0 && L0.psf) >= 0 ? num(L0.psf) : 100);
  pl.laIds.push({id, psf}); pl.laActive = id; save(); renderLaPanel();
});
// section 3: each level's load-area box
$('#t1').addEventListener('change', e => { const t = e.target; if(t.dataset.lauses === undefined) return; const L = S.levels[+t.dataset.lauses - 1]; if(!L) return; L.la = t.checked; changed(); });
// drawing pad level (load areas and brace areas of that level) and copying between levels
function fillLvSelects(){
  const n = nLv(), cur = padLv(), lvls = [...Array(n)].map((_,j) => n - j);
  $('#padLv').innerHTML = lvls.map(i => `<option value="${i}"${i === cur ? ' selected' : ''}>${esc(label(i))}</option>`).join('');
  $('#lvCopyFrom').innerHTML = '<option value="">choose…</option>' + lvls.filter(i => i !== cur).map(i => `<option value="${i}">${esc(label(i))}</option>`).join('');
  document.querySelectorAll('[data-lvtag]').forEach(t => { t.textContent = 'Level: ' + label(cur); });
}
function copyLevel(from, to){   // brace areas (story below) and load areas
  PL().lfrs.forEach(l => { l.As[to-1] = l.As[from-1]; });
  S.plan.lv[to-1] = JSON.parse(JSON.stringify(S.plan.lv[from-1]));
}
$('#padLv').addEventListener('change', e => { S.plan.padLv = +e.target.value; sel = null; laStart = null; save(); fillLvSelects(); changed(); });
$('#lvCopyAll').addEventListener('click', () => armed($('#lvCopyAll'), 'Click again: overwrite every level', () => {
  pushHist(); const f = padLv(); for(let i=1;i<=nLv();i++) if(i !== f) copyLevel(f, i); changed(); planMsg(`${label(f)}'s brace areas and load areas were copied to every level.`); }));
$('#lvCopyFrom').addEventListener('change', e => { const f = +e.target.value; if(!f) return; const t = padLv();
  pushHist(); copyLevel(f, t); e.target.value = ''; changed(); planMsg(`${label(f)}'s brace areas and load areas were copied to ${label(t)}. Undo reverses it.`); });

/* ---------- init ---------- */
S.n = S.levels.length;
initCollapse();
renderInputs(); renderT1(); renderT3();
update();
initPlan();
fillLvSelects();
renderLaPanel();
