/* ---- Bones glue, part 1: stand-ins for the parts of the full ELF app that are not in the handoff ---- */
const KEY = 'bones-pad-v1', KEY_BF = 'bones-pad-v1';   // where the pad keeps its project in this browser
function renderInputs(){} function renderT1(){} function renderT3(){}            // modules 1 and 3: not in Bones yet
function renderHD(){} function renderCol(){} function renderChord(){} function renderVE(){}   // modules 9 to 12
function initCollapse(){}
function update(){ fillLvSelects(); if(typeof bonesAfterUpdate === 'function') bonesAfterUpdate(); }
const V3 = {yaw: -35, pitch: 28, zoom: 1, px: 0, py: 0};   // 3D view state (glue-post.js draws it)
let bonesReady = false;   // set once glue-post.js has loaded; the pad redraws during its own start-up first
