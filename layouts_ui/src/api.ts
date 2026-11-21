// Calls to the layout generator's own server (layouts/server.py, port 8010).
export type Vec3 = [number, number, number];
export type Suggestion = {
  id: string; label: string; move: number[][]; bbox: [Vec3, Vec3]; size: Vec3; below: number; above: number;
  undercut_mm2?: number;
};
export type Upload = {
  upload: string; name: string; closed: boolean; closed_detail: string; volume_mm3: number;
  bbox: [Vec3, Vec3]; suggestions: Suggestion[];
};
export type Flask = { length: number; width: number; cope_height: number; drag_height: number };
export type Limits = {
  n_min: number | null; n_max: number | null; families: string[] | null; rotations_deg: number[] | null;
  templates?: string[] | null; ingates?: string[] | null; disabled_ingates: string[]; riser_keepouts: boolean;
  count_threshold: number | null;
};
export type PhaseState = { state: string; input_hash: string | null; runtime_s?: number | null; counts?: Record<string, number>; error?: string | null; issues?: string[] };
export type Progress = {
  running: boolean; phase?: string | null; message?: string; error?: string | null; counted?: number; placed?: number;
  rejected?: number; duplicates?: number; by_family?: Record<string, number>; live_count?: number;
  count_exceeded?: { count: number; threshold: number; by: Record<string, number> };
  status: Record<string, string>; phases: Record<string, PhaseState>; counts: Record<string, number>;
};
export type JobSummary = {
  id: string; name: string; recipe_set: string; inputs: any; recipe: { id: string; values: any };
  status: Record<string, string>; phases: Record<string, PhaseState>; counts: Record<string, number>;
  progress: Progress; starred: string[];
};
export type Metrics = { layout: string; poured_weight: number; yield_pct: number; runner_length: number; min_clearance: number; path_length_error_pct: number };
export type Card = {
  id: string; n: number; family: string; template: string; symmetry: string; rotation_deg: number; ingate: string;
  arrangement: any; state: "routed" | "rejected" | "pending"; metrics: Metrics | null; starred: boolean; reasons: any[] | null;
};
export type Grid = { total: number; all: number; offset: number; items: Card[]; facets: Record<string, any[]> };
export type Check = { code: string; passed: boolean; value?: any; limit?: any; message?: string };

export const PHASES = ["modulus", "ingate_candidates", "layouts", "runners", "metrics"] as const;
export const PHASE_LABEL: Record<string, string> = {
  modulus: "1 · Modulus analysis", ingate_candidates: "2 · Ingate candidates", layouts: "3 · Cavity layouts",
  runners: "4 · Runners and sizing", metrics: "Metrics",
};
export const METRIC_LABEL: Record<string, [string, string]> = {
  poured_weight: ["Poured weight", "kg"], yield_pct: ["Yield", "%"], runner_length: ["Runner length", "mm"],
  min_clearance: ["Min clearance", "mm"], path_length_error_pct: ["Path-length error", "%"],
};

async function call<T>(method: string, url: string, body?: unknown, raw?: BodyInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, {
      method,
      headers: raw ? undefined : body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: raw ?? (body !== undefined ? JSON.stringify(body) : undefined),
    });
  } catch {
    throw new Error("The layout server is not reachable (uvicorn layouts.server:app --port 8010).");
  }
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      const d = j.detail;
      msg = typeof d === "string" ? d : d?.problems ? d.problems.join("\n") : JSON.stringify(d);
    } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const B = "/api/layouts";
export const api = {
  settings: () => call<any>("GET", `${B}/settings`),
  recipe: (process: string, alloy: string, set: string) =>
    call<any>("GET", `${B}/recipe?process=${process}&alloy=${alloy}&recipe_set=${set}`),
  upload: (f: File) => call<Upload>("POST", `${B}/upload?name=${encodeURIComponent(f.name)}`, undefined, f),
  uploadGlb: (up: string) => `${B}/upload/${up}/part.glb`,
  create: (spec: unknown) => call<JobSummary>("POST", `${B}/jobs`, spec),
  jobs: () => call<JobSummary[]>("GET", `${B}/jobs`),
  job: (id: string) => call<JobSummary>("GET", `${B}/jobs/${id}`),
  setInputs: (id: string, changes: unknown) => call<JobSummary>("POST", `${B}/jobs/${id}/inputs`, changes),
  run: (id: string, start?: string) => call<Progress>("POST", `${B}/jobs/${id}/run`, { start: start ?? null }),
  progress: (id: string) => call<Progress>("GET", `${B}/jobs/${id}/progress`),
  analysis: (id: string) => call<any>("GET", `${B}/jobs/${id}/analysis`),
  partMesh: (id: string) => call<{ positions: number[]; triangles: number[]; faces: number[]; modulus: number[] }>("GET", `${B}/jobs/${id}/part_mesh`),
  layouts: (id: string, q: Record<string, string | number | boolean | undefined>) => {
    const s = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => v !== undefined && v !== "" && s.set(k, String(v)));
    return call<Grid>("GET", `${B}/jobs/${id}/layouts?${s}`);
  },
  thumb: (id: string, lid: string) => `${B}/jobs/${id}/layouts/${lid}/thumb.svg`,
  layout: (id: string, lid: string) => call<any>("GET", `${B}/jobs/${id}/layouts/${lid}`),
  star: (id: string, lid: string, starred: boolean) => call<{ starred: string[] }>("POST", `${B}/jobs/${id}/layouts/${lid}/star`, { starred }),
  build: (id: string, lid: string) => call<any>("POST", `${B}/jobs/${id}/layouts/${lid}/build`),
  model: (id: string, lid: string, what: "cavities" | "gating") => `${B}/jobs/${id}/layouts/${lid}/model/${what}.glb`,
  step: (id: string, lid: string) => `${B}/jobs/${id}/layouts/${lid}/step`,
  rejected: (id: string, code?: string) => call<any>("GET", `${B}/jobs/${id}/rejected${code ? `?code=${code}&limit=500` : ""}`),
};

export function fmt(v: unknown, d = 1): string {
  if (typeof v !== "number") return v === null || v === undefined ? "–" : String(v);
  return Math.abs(v) >= 1e5 ? v.toExponential(2) : v.toFixed(d).replace(/\.0+$/, "");
}
