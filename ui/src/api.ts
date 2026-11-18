// Types and calls for the FastAPI backend (api/main.py).
export type Vec3 = [number, number, number];
export type Frame = { origin: Vec3; normal: Vec3; up: Vec3 };
export type Section = { shape: string; dims: Record<string, number>; area: number };
export type Port = { id: string; kind: string; frame: Frame; section: Section | null; section_policy: string };
export type Issue = {
  rule: string; severity: "error" | "warning" | "info"; message: string;
  value: unknown; limit: unknown; instances: string[]; connection: string | null;
};
export type ParamSchema = {
  name: string; label: string; unit: string; type: string; default: unknown;
  min: number | null; max: number | null; choices: unknown[] | null; driven_by: string | null; description: string;
};
export type ElementType = {
  id: string; version: string; category: string; is_path: boolean; gating_role: string | null; params: ParamSchema[];
};
export type Pick = { face: number; kind: string; area_mm2: number; point: Vec3; normal: Vec3; mesh: number[] };
export type FaceCheck = { face: number; kind: string; side: string; min_draft_deg: number; undercut_fraction: number; mesh?: number[] };
export type Calc = {
  inputs: Record<string, any>; readouts: Record<string, any>; outputs: Record<string, any>;
  planes: { name: string; area_mm2: number | null; flow_share: number; velocity_m_s?: number; reynolds?: number; role: string | null; z_mm: number }[];
  paths: { elements: string[]; choke_plane?: string; choke_mm2?: number; choke_role?: string | null; ratio?: number[]; ends_at_part: boolean }[];
  driven: Record<string, Record<string, number>>; issues: Issue[];
};
export type ElementState = {
  instance: string; type: string; name: string | null; built: boolean;
  params: Record<string, unknown>; derived: Record<string, number>; solid: string | null; ports: Port[];
};
// matrix: where the mold has this mesh (row-major 4x4, mm), when it is sent unplaced (a part: meshed
// once per file, moved by this matrix rather than meshed again)
export type MeshRef = { name: string; kind: string; url: string; matrix?: number[] | null };
export type DesignState = {
  id: string; path: string | null; document: any; can_undo: boolean; can_redo: boolean;
  elements: ElementState[];
  part: { volume_mm3: number; bbox: [Vec3, Vec3]; ports: Record<string, Frame>; source_unit: string | null; shrinkage_pct: number } | null;
  issues: Issue[]; meshes: MeshRef[]; measurement_planes: (Frame & { name: string; section: Section | null })[];
  timings_ms: Record<string, number>; calc: Calc | null; preset: Record<string, any> | null;
  flask: [Vec3, Vec3] | null;
  box: { size: Vec3; parting: number; bottom: number; top: number; centre: Vec3 } | null;
  parts: PartInBox[];
  saved: string | null; save_error: string | null; resumed: boolean;
  parting_outline: [number, number][][];
  notes?: string[];             // about the last action, not issues (e.g. a runner that could not be gated again)
};
export type PartInBox = { id: string; name: string; kind: "main" | "copy" | "file"; bbox: [Vec3, Vec3]; volume_mm3: number | null };

const UNREACHABLE = "The API is not reachable (is it running on port 8000?)";

/** An error's detail as text: a string, a list of strings or {message} / {msg} objects, or an object. */
function detailText(d: unknown): string | null {
  if (d === undefined || d === null) return null;
  if (typeof d === "string") return d;
  const one = (x: unknown): string => {
    if (typeof x === "string") return x;
    if (x && typeof x === "object") {
      const o = x as Record<string, unknown>;
      const m = o.message ?? o.msg;
      if (typeof m === "string") {
        // FastAPI's validation errors say where: loc ["body", "gate", "width"]
        const loc = Array.isArray(o.loc) ? o.loc.filter((l) => l !== "body").join(".") : "";
        return loc ? `${loc}: ${m}` : m;
      }
    }
    return JSON.stringify(x);
  };
  return Array.isArray(d) ? d.map(one).join("; ") : one(d);
}

/** fetch, then the JSON reply; a failed request throws an Error with readable text. */
async function send(url: string, init: RequestInit): Promise<unknown> {
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch {
    throw new Error(UNREACHABLE);
  }
  let text = "";
  try {
    text = await r.text();
  } catch {
    throw new Error(UNREACHABLE);
  }
  let j: unknown = undefined;
  if (text.trim()) {
    try {
      j = JSON.parse(text);
    } catch { /* not JSON: a proxy or server error page */ }
  }
  if (!r.ok) {
    const detail = j && typeof j === "object" ? detailText((j as Record<string, unknown>).detail) : null;
    if (detail) throw new Error(detail);
    // the dev server's proxy answers 500 with no body (or 502-504) when the API is down
    if ((r.status === 500 && !text.trim()) || r.status === 502 || r.status === 503 || r.status === 504) throw new Error(UNREACHABLE);
    throw new Error(`${r.status} ${r.statusText}${text.trim() ? `: ${text.trim().slice(0, 300)}` : ""}`);
  }
  if (j === undefined) throw new Error(`The API's reply to ${url} was not JSON`);
  return j;
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  return (await send(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })) as T;
}

/** Send a part file to the API; it is saved under parts/ and its path comes back. */
async function upload(file: File): Promise<string> {
  const j = (await send(`/api/upload?name=${encodeURIComponent(file.name)}`, {
    method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
  })) as { path: string };
  return j.path;
}

export type FitOption = {
  id: "widest" | "flat"; label: string; move: number[][]; bbox: [Vec3, Vec3]; size: Vec3; below: number; above: number;
  undercut_mm2: number; undercut_faces: number; up?: string; section_mm2?: number; face_mm2?: number;
};
export type BoxProposal = {
  length: number; width: number; height: number; parting: number; cope: number; drag: number;
  standard: string | null; shift: Vec3; need: Record<string, number>;
};
export type Clearances = { sides: number; below: number; above: number; extra: number; step: number };
/** The ingate put wherever a runner runs into a part. */
export type GateSpec = { width: number; thickness: number; length: number; side: string };

export const api = {
  upload,
  elementTypes: () => call<ElementType[]>("GET", "/api/elements"),
  open: (path: string) => call<DesignState>("POST", "/api/designs/open", { path }),
  newFromPart: (part: string, fresh = false) => call<DesignState>("POST", "/api/designs/new", { part, fresh }),
  setBox: (id: string, b: { length: number; width: number; height: number; parting: number }) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/box`, b),
  suggestFit: (id: string) => call<{ options: FitOption[] }>("POST", `/api/designs/${id}/workflow/suggest`),
  proposeBox: (id: string, bbox: [Vec3, Vec3], c: Clearances) =>
    call<BoxProposal>("POST", `/api/designs/${id}/workflow/propose_box`, { bbox, ...c }),
  applyFit: (id: string, f: { move: number[][]; length: number; width: number; height: number; parting: number; shift: Vec3 }) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/apply_fit`, f),
  matchboxes: () => call<{ name: string; length: number; width: number; cope: number; drag: number }[]>("GET", "/api/matchboxes"),
  addMatchbox: (b: { name?: string; length: number; width: number; cope: number; drag: number }) =>
    call<unknown[]>("POST", "/api/matchboxes", b),
  centrePart: (id: string, cavity: string | null) => call<DesignState>("POST", `/api/designs/${id}/workflow/centre_part`, { cavity }),
  movePart: (id: string, delta: Vec3, cavity: string | null) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/move_part`, { delta, cavity }),
  turnPart: (id: string, axis: string, angle_deg: number, cavity: string | null) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/turn_part`, { axis, angle_deg, cavity }),
  addCopy: (id: string, of: string | null) => call<DesignState>("POST", `/api/designs/${id}/workflow/add_copy`, { of }),
  addPartFile: (id: string, path: string) => call<DesignState>("POST", `/api/designs/${id}/workflow/add_part_file`, { path }),
  removePart: (id: string, cavity: string) => call<DesignState>("POST", `/api/designs/${id}/workflow/remove_part`, { cavity }),
  placeSprue: (id: string, s: { x: number; y: number; d_top: number; d_bottom: number }) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/sprue`, s),
  drawRunner: (id: string, r: {
    side: string; width: number; taper_pct: number; height: number; extension: number; points: Vec3[]; runner: string | null;
    branch?: { from: string; at: Vec3 } | null; gate?: { side: string; width: number; thickness: number; length: number } | null;
  }) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/runner`, r),
  /** the automatic ingate's size on every runner, in one undo step; null: no automatic ingates */
  setGate: (id: string, gate: GateSpec | null) => call<DesignState>("POST", `/api/designs/${id}/workflow/gate`, { gate }),
  drawIngate: (id: string, g: { runner: string; start: [number, number]; end: [number, number]; side: string;
                                width: number; thickness: number; ingate: string | null }) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/ingate`, g),
  removeIngate: (id: string, ingate: string) => call<DesignState>("POST", `/api/designs/${id}/workflow/remove_ingate`, { ingate }),
  setCup: (id: string, c: { d_top: number | null; height: number | null }) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/cup`, c),
  setWell: (id: string, w: { d: number | null; depth: number | null; filter: { shape: string; size: number; thickness: number } | null }) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/well`, w),
  addFeeder: (id: string, f: { kind: string; x: number; y: number; d: number; height: number;
                               neck: { width: number; thickness: number; side: string } | null }) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/feeder`, f),
  addVent: (id: string, v: { x: number; y: number; d: number }) => call<DesignState>("POST", `/api/designs/${id}/workflow/vent`, v),
  removeAny: (id: string, instance: string) => call<DesignState>("POST", `/api/designs/${id}/workflow/remove`, { instance }),
  /** move a runner up or down within its half: level is its parting-plane face's height (0 on the
   * plane, negative in the drag); its branches and ingates go with it, the sprue follows */
  setRunnerLevel: (id: string, runner: string, level: number) =>
    call<DesignState>("POST", `/api/designs/${id}/workflow/runner_level`, { runner, level }),
  removeRunner: (id: string, runner: string) => call<DesignState>("POST", `/api/designs/${id}/workflow/remove_runner`, { runner }),
  patch: (id: string, ops: unknown[]) => call<DesignState>("POST", `/api/designs/${id}/patch`, ops),
  undo: (id: string) => call<DesignState>("POST", `/api/designs/${id}/undo`),
  redo: (id: string) => call<DesignState>("POST", `/api/designs/${id}/redo`),
  save: (id: string) => call<{ saved: string }>("POST", `/api/designs/${id}/save`, {}),
  exportTo: (id: string, out_dir: string) => call<any>("POST", `/api/designs/${id}/export`, { out_dir }),
  presets: () => call<string[]>("GET", "/api/presets"),
  pick: (id: string, point: Vec3) => call<Pick>("POST", `/api/designs/${id}/part/pick`, { point }),
  orient: (id: string, op: Record<string, unknown>) => call<DesignState>("POST", `/api/designs/${id}/part/orient`, op),
  gatePort: (id: string, point: Vec3) => call<DesignState>("POST", `/api/designs/${id}/part/gate_port`, { point }),
  moldCheck: (id: string) => call<{ faces: FaceCheck[]; issues: Issue[] }>("POST", `/api/designs/${id}/part/moldcheck`),
  applyCalc: (id: string) => call<DesignState>("POST", `/api/designs/${id}/calculators/apply`),
  mirror: (id: string, instances: string[], origin: Vec3, normal: Vec3) =>
    call<DesignState>("POST", `/api/designs/${id}/ops/mirror`, { instances, origin, normal }),
  pattern: (id: string, spec: Record<string, unknown>) => call<DesignState>("POST", `/api/designs/${id}/ops/pattern`, spec),
  remove: (id: string, instance: string) => call<DesignState>("POST", `/api/designs/${id}/elements/remove`, { instance }),
};
