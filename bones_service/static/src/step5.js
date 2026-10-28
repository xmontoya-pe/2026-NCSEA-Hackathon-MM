// Step 5, client view: hand the drawing-pad state to the client dashboard (/dashboard).
// Injected by build_ui.py, which also turns the "5 Client view" nav label into the #nav5 button.
// The dashboard runs gravity and lateral through POST /api/dashboard (bones_service/dashboard.py).
(function(){
  "use strict";
  const KEY = "bones.dashboard.state";
  const nav = document.getElementById("nav5");
  if(!nav) return;
  nav.addEventListener("click", () => {
    const pad = window.BonesPad;
    try{
      if(pad && typeof pad.state === "function")
        localStorage.setItem(KEY, JSON.stringify({state: pad.state(), saved_at: new Date().toISOString()}));
    }catch(e){
      // Storage blocked or full: the dashboard opens empty and offers the example building.
    }
    window.open("/dashboard", "_blank", "noopener");
  });
})();
