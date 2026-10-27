"""FastAPI service for the Seismic ELF engine (elf_engine.py): steel braced frames or concrete shear walls.

Run:
    pip install -r requirements-api.txt
    uvicorn api:app --reload
then open http://127.0.0.1:8000/docs.

Endpoints:
    GET  /health
    POST /analyze        a project (the JSON the dashboard or the Bones drawing pad saves) + analysis options.
                         Shear walls when project.lateral_system is "shear_wall" or the request has a
                         "shear_walls" block (R, Cd, f'c and the other wall inputs); braced frames otherwise.
    POST /analyze/bones  a Bones gravity-input file + lateral parameters; converted, then analyzed (braced frames)

Problems with the calculation itself (no plan, no Cs, an unstable layout) come back in a 200 response as
"warnings" or per-section "error" strings, the way the dashboard shows them. 422 is only for malformed
input and for SDC D on /analyze/bones, where the R = 3 braced system is not permitted.
"""

from typing import Annotated, Any, Literal, Optional, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

import elf_engine
from bones_to_elf import convert


def _blank_to_none(v):
    return None if v == "" else v


# the dashboard saves empty inputs as ''
Num = Annotated[Optional[float], BeforeValidator(_blank_to_none)]
Id = Annotated[str, BeforeValidator(str)]
Acc = Literal[1, -1, 0]
AccEnv = Union[Literal["env"], Acc]
Method = Literal["rigid", "simple", "cont"]


class Level(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = ""
    h: Num = Field(None, description="Level height above the base, ft (cumulative)")
    psf: Num = Field(None, description="Seismic weight, psf; used when la is false or the load areas are invalid")
    la: bool = Field(False, description="Take this level's seismic weight and CM from its load areas")


class LoadArea(BaseModel):
    id: Id
    x1: float
    y1: float
    x2: float
    y2: float


class LoadAreaId(BaseModel):
    id: Id
    psf: Num = None


class LevelLoads(BaseModel):
    model_config = ConfigDict(extra="ignore")
    la: list[LoadArea] = []
    laIds: list[LoadAreaId] = []


class Frame(BaseModel):
    """A line of the lateral system: a braced frame or a shear wall, by the project's lateral_system."""
    model_config = ConfigDict(extra="ignore")
    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    A: Num = Field(None, description="Braced frames: brace area, in²")
    As: list[Num] = Field([], description="Braced frames: brace area by story (story 1 first), in²")
    up: str = Field("b", description="Braced frames: end the story-1 brace rises to: 'a' = (x1, y1), 'b' = (x2, y2)")
    alt: bool = Field(False, description="Braced frames: alternate the brace direction each story")
    type: str = Field("single", description="Braced frames: 'single' diagonal or 'x2' two-bay X")
    mid: str = Field("top", description="2X frames: story-1 braces meet at the middle column at the 'top' or 'bot'")
    L1: Num = Field(None, description="2X frames: middle column offset from the start, ft")
    t: Num = Field(None, description="Shear walls: thickness, in (every story without its own ts)")
    ts: list[Num] = Field([], description="Shear walls: thickness by story (story 1 first), in")


class Plan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verts: list[tuple[float, float]] = Field([], description="Outline vertices, ft (orthogonal)")
    closed: bool = False
    lfrs: list[Frame] = []
    aDef: Num = 10
    lv: list[LevelLoads] = Field([], description="Load areas for each level, level 1 first")
    tDef: Num = Field(None, description="Shear walls: default thickness, in")
    kShear: Optional[bool] = Field(None, description="Shear walls: include shear deformation")
    kh: Num = Field(None, description="Shear walls: wall height for the point-spring stiffness, ft (blank: roof)")
    icr: Num = Field(None, description="Shear walls: I_eff / I_g")
    fc: Num = Field(None, description="Shear walls: f'c, ksi")
    jointsOff: list[str] = Field([], description="Shear walls: released core joints, as 'x,y' keys")


class Drift(BaseModel):
    type: Literal["other", "low"] = "other"
    at: Literal["edge", "cm"] = "edge"


class Project(BaseModel):
    """The dashboard's project file. Keys the engine doesn't use (hd, col, opts, ...) are ignored."""
    model_config = ConfigDict(extra="ignore")
    project: str = ""
    lateral_system: Literal["braced_frame", "shear_wall"] = "braced_frame"
    cs: Num = Field(None, description="Seismic response coefficient. Braced frames: blank = computed from SDS, SD1 "
                                      "and options.R. Shear walls: used when R is blank or csAuto is false")
    csAuto: Optional[bool] = Field(None, description="Shear walls: calculate Cs from R (default true)")
    ie: Num = None
    sds: Num = None
    sd1: Num = None
    S1: Num = None
    TL: Num = None
    R: Num = Field(None, description="Shear walls: response modification coefficient, Table 12.2-1")
    cd: Num = Field(None, description="Shear walls: deflection amplification factor Cd")
    rc: Optional[Literal["I", "II", "III", "IV"]] = Field(None, description="Shear walls: Risk Category")
    sys: str = Field("other", description="smrf, cmrf, ebf, brbf, other or custom (Table 12.8-2)")
    ct: Num = 0.02
    xexp: Num = 0.75
    Tan: Num = Field(None, description="Period from analysis, s (optional)")
    k: Num = None
    kAuto: bool = True
    levels: list[Level] = Field(..., min_length=1)
    plan: Plan = Plan()
    dr: Optional[Drift] = None
    bones: Optional[dict[str, Any]] = Field(None, description="The Bones UI's extra data; hazard.query.risk_category "
                                                              "is used when no Risk Category is given")


class ShearWallInputs(BaseModel):
    """Shear wall inputs that the drawing pad doesn't collect. A field left out keeps the project's value (or the
    dashboard's default, shown in brackets). Wall thicknesses come from the walls themselves (t, ts)."""
    R: Optional[float] = Field(None, gt=0, description="Response modification coefficient, Table 12.2-1")
    Cd: Optional[float] = Field(None, gt=0, description="Deflection amplification factor [5]")
    Omega0: float = Field(2.5, gt=0, description="Overstrength factor for the collector forces")
    risk_category: Optional[Literal["I", "II", "III", "IV"]] = Field(None, description="[the Bones hazard panel's, or II]")
    S1: Optional[float] = Field(None, ge=0, description="g; Eq. 12.8-6 is checked when S1 >= 0.6 [blank]")
    TL: Optional[float] = Field(None, gt=0, description="Long-period transition period, s [blank: T <= TL]")
    cs_from_R: Optional[bool] = Field(None, description="Calculate Cs from R, Eq. 12.8-2 to 12.8-6 [true]")
    fc_ksi: Optional[float] = Field(None, gt=0, description="Concrete strength f'c [5]")
    I_eff_ratio: Optional[float] = Field(None, gt=0, le=1, description="Cracked section, I_eff / I_g [0.35]")
    shear_deformation: Optional[bool] = Field(None, description="Include shear deformation [true]")
    stiffness_height_ft: Optional[float] = Field(None, gt=0, description="Wall height for the point springs [roof]")
    default_thickness_in: Optional[float] = Field(None, gt=0, description="Thickness of walls with none [6]")
    released_joints: Optional[list[str]] = Field(None, description="Core joints that act separately, as 'x,y' keys")
    drift_table: Optional[Literal["other", "low"]] = Field(
        None, description="Table 12.12-1 row: 'other' structures, or 'low' (4 stories or less, walls designed for the drifts)")
    drift_at: Optional[Literal["edge", "cm"]] = Field(None, description="Story drift at the plan edges or at the CM [edge]")


class Options(BaseModel):
    hd_force: Literal["Fx", "Fpx"] = Field("Fx", description="Force used for the horizontal distribution (module 9)")
    hd_acc: Acc = Field(1, description="Module 9 accidental torsion: +5%, -5% or none")
    ve_acc: AccEnv = Field("env", description="Module 12 accidental torsion: envelope of ±5%, +5%, -5% or none")
    R: Optional[float] = Field(3.0, description="Braced frames: response modification coefficient, used only when cs is "
                                                "blank (shear walls: shear_walls.R)")
    hd_method: Method = Field("rigid", description="Shear walls, module 9: rigid diaphragm, simple spans or continuous beam")
    col_method: Method = Field("rigid", description="Shear walls, module 10 (collectors, design Fpx)")
    col_acc: AccEnv = Field("env", description="Shear walls, module 10 accidental torsion (rigid)")
    ch_force: Literal["Fx", "Fpx"] = Field("Fx", description="Shear walls, module 11 (chords)")
    ch_method: Method = "rigid"
    ch_acc: Acc = 1
    ve_method: Method = Field("rigid", description="Shear walls, module 12 (wall forces, Fx)")
    stiffness_acc: Acc = Field(1, description="Shear walls, module 2: accidental torsion of the cantilever comparison")


class AnalyzeRequest(BaseModel):
    project: Project
    options: Options = Options()
    shear_walls: Optional[ShearWallInputs] = None


class BonesRequest(BaseModel):
    bones: dict[str, Any] = Field(..., description="The Bones gravity-input file (schema bones.gravity-input)")
    self_weight_psf: float = Field(..., description="Structural self-weight of a typical floor, psf")
    roof_self_weight_psf: Optional[float] = None
    roof_extra_psf: float = Field(0.0, description="Rooftop equipment, 20% of pf when pf > 30 psf")
    parapet_ft: float = 0.0
    sds: Optional[float] = None
    sd1: Optional[float] = None
    brace_area: float = Field(10.0, description="Brace area for every frame, in²")
    drop_frames: list[str] = []
    options: Options = Options()


app = FastAPI(title="Seismic ELF: braced frames or shear walls, rigid diaphragm",
              description="ASCE 7-22 ELF, diaphragm distribution, and braced frame or concrete shear wall forces. "
                          "Port of the Seismic ELF BraceFrame dashboard (modules 1-9 and 12) and the ShearWall "
                          "dashboard (modules 1-13: cores, flexible diaphragms, collectors, chords and story drift).")


def _wall_project(project: dict, w: Optional[ShearWallInputs]) -> dict:
    """The project as the ShearWall dashboard holds it: the request fields go where the dashboard keeps them."""
    project["lateral_system"] = "shear_wall"
    pl = project["plan"]
    w = w or ShearWallInputs()
    for value, where, key in ((w.R, project, "R"), (w.Cd, project, "cd"), (w.risk_category, project, "rc"),
                              (w.S1, project, "S1"), (w.TL, project, "TL"), (w.cs_from_R, project, "csAuto"),
                              (w.fc_ksi, pl, "fc"), (w.I_eff_ratio, pl, "icr"), (w.shear_deformation, pl, "kShear"),
                              (w.stiffness_height_ft, pl, "kh"), (w.default_thickness_in, pl, "tDef"),
                              (w.released_joints, pl, "jointsOff")):
        if value is not None:
            where[key] = value
    if not project.get("rc"):
        rc = ((project.get("bones") or {}).get("hazard") or {}).get("query", {}).get("risk_category")
        if rc in elf_engine.IE_BY_RISK:
            project["rc"] = rc
    dr = dict(project.get("dr") or {})
    if w.drift_table:
        dr["type"] = w.drift_table
    if w.drift_at:
        dr["at"] = w.drift_at
    project["dr"] = dr
    return project


def _run(project: dict, opt: Options, walls: Optional[ShearWallInputs] = None) -> dict:
    try:
        if walls is not None or project.get("lateral_system") == "shear_wall":
            project = _wall_project(project, walls)
            return elf_engine.analyze(project, **opt.model_dump(exclude={"R"}),
                                      omega0=(walls or ShearWallInputs()).Omega0)
        return elf_engine.analyze(project, hd_force=opt.hd_force, hd_acc=opt.hd_acc, ve_acc=opt.ve_acc, R=opt.R)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    return _run(req.project.model_dump(), req.options, req.shear_walls)


@app.post("/analyze/bones")
def analyze_bones(req: BonesRequest):
    try:
        project, summary = convert(req.bones, req.self_weight_psf, roof_self_weight_psf=req.roof_self_weight_psf,
                                   roof_extra_psf=req.roof_extra_psf, parapet_ft=req.parapet_ft, sds=req.sds,
                                   sd1=req.sd1, brace_area=req.brace_area, drop_frames=req.drop_frames)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Could not convert the Bones file: {e}")
    result = _run(project, req.options)
    result["conversion"] = elf_engine.clean({"warnings": summary["warnings"], "project": project})
    return result
