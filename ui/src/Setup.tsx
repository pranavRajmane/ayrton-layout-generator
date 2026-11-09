// The guided set-up: 1 the box and the parts in it, 2 the sprue, 3 the runner.
// Steps 2 and 3 are sketched in plan view (looking down on the parting plane).
// useSetup gives the panel for the right-hand side and what the viewer should do.
import { useEffect, useRef, useState } from "react";
import { api, type BoxProposal, type Clearances, type DesignState, type FitOption, type Vec3 } from "./api";
import type { SetupView } from "./Viewer";
import { nearestOn, type Guides } from "./Setup3D";

type Run = (f: () => Promise<DesignState | void>) => void;
type Step = 1 | 2 | 3 | 4 | 5;
type XY = [number, number];

// key names as the keyboard prints them: a Mac's Option is the browser's Alt, its
// delete is Backspace (both remove a point here), its return is Enter
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform);
const FREE_KEY = IS_MAC ? "Option ⌥" : "Alt";
const DEL_KEY = IS_MAC ? "delete" : "Delete";
const ENTER_KEY = IS_MAC ? "return" : "Enter";
const BACK_KEY = IS_MAC ? "delete" : "Backspace";

const DBL_MM = 10;     // a double-click this close to the last point only finishes

const CLEAR_KEY = "gating.clearances";          // the clearances typed last time (this browser only)
function lastClearances(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(CLEAR_KEY) ?? "{}");
  } catch {
    return {};
  }
}

/** a @ b for 4x4 row-major matrices */
function mul(a: number[][], b: number[][]): number[][] {
  return a.map((row) => b[0].map((_, j) => row.reduce((t, x, k) => t + x * b[k][j], 0)));
}
const shiftBy = (v: number[]): number[][] => [[1, 0, 0, v[0]], [0, 1, 0, v[1]], [0, 0, 1, v[2]], [0, 0, 0, 1]];

const GATE_KEY = "gating.autogate";               // the automatic ingate's size typed last time (this browser)

const num = (s: string | number | null | undefined) => (String(s ?? "").trim() === "" ? NaN : Number(s));
const ok = (...v: number[]) => v.every((x) => Number.isFinite(x) && x > 0);
const str = (v: unknown) => (v === undefined || v === null ? "" : String(v));
const xy = (p: number[]): XY => [p[0], p[1]];
const v3 = (p: XY): Vec3 => [p[0], p[1], 0];

function el(state: DesignState, id: string) {
  return (state.document.elements as any[]).find((e) => e.id === id) ?? null;
}

function Field({ label, value, set, onCommit, unit = "mm", width = 70 }: {
  label: string; value: string; set: (v: string) => void; onCommit?: () => void; unit?: string; width?: number;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input style={{ width }} value={value} inputMode="decimal" onChange={(e) => set(e.target.value)}
        onBlur={onCommit} onKeyDown={(e) => e.key === "Enter" && onCommit?.()} />
      <span className="muted">{unit}</span>
    </label>
  );
}

const typing = () => {
  const a = document.activeElement;
  return !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT");
};

export function useSetup(state: DesignState | null, run: Run, setPlan: (plan: boolean) => void): { panel: JSX.Element | null; view: SetupView } {
  const sprue = state ? el(state, "sprue") : null;
  // every runner, in drawing order: dropped into from the sprue, or branched off another runner
  const runnerIds: string[] = state ? (state.document.elements as any[]).filter((e) => e.type === "runner").map((e) => e.id) : [];
  /** where a runner starts (under the sprue, or its branch point) and its points after that */
  const geo = (id: string): { start: XY; pts: XY[]; branched: boolean; width: number; from: string | null } => {
    const r = el(state!, id);
    // edited as drawn (a gated runner is stored cut back; it is cut again from these)
    const drawn = (r.params.drawn as number[][] | undefined) ?? [];
    const wp = (drawn.length ? drawn : (r.params.waypoints as number[][])).map(xy);
    const feed = (state!.document.connections as any[]).find((c) => c.to.instance === id && (c.joint === "drop" || c.joint === "tee"));
    const branched = feed?.joint === "tee";
    const s0 = el(state!, "sprue")?.anchor.frame.origin ?? [0, 0];
    return { start: branched ? wp[0] : [s0[0], s0[1]], pts: branched ? wp.slice(1) : wp, branched,
             width: Number(r.params.w_parting), from: branched ? feed.from.instance : null };
  };
  const [active, setActive] = useState<string | null>(null);      // the runner being edited; null: a new one
  const runner = state && active ? el(state, active) : null;
  const [step, setStep] = useState<Step>(1);
  // step 1
  const [L, setL] = useState(""), [W, setW] = useState(""), [H, setH] = useState(""), [P, setP] = useState("");
  const [part, setPart] = useState("part");
  // the suggested fit: how to part it, the clearances, the proposed box
  const [suggesting, setSuggesting] = useState(true);
  const [options, setOptions] = useState<FitOption[] | null>(null);
  const [choice, setChoice] = useState<"widest" | "flat">("widest");
  const last = lastClearances();
  const [cSides, setCSides] = useState(last.sides ?? ""), [cBelow, setCBelow] = useState(last.below ?? "");
  const [cAbove, setCAbove] = useState(last.above ?? ""), [cExtra, setCExtra] = useState(last.extra ?? "");
  const [cStep, setCStep] = useState(last.step ?? "");
  const [proposal, setProposal] = useState<BoxProposal | null>(null);
  const [noteText, setNoteText] = useState<string | null>(null);
  const addInput = useRef<HTMLInputElement>(null);
  // step 2
  const [dTop, setDTop] = useState(""), [dBot, setDBot] = useState("");
  const [sx, setSx] = useState(""), [sy, setSy] = useState("");
  // step 3
  const [side, setSide] = useState("drag");
  const [wp, setWp] = useState(""), [wf, setWf] = useState(""), [rh, setRh] = useState("");
  const [extending, setExtending] = useState(false);
  const [draft, setDraftState] = useState<XY[]>([]);
  const draftRef = useRef<XY[]>([]);                 // the latest points, for a finish in the same event as a click
  const setDraft = (f: XY[] | ((d: XY[]) => XY[])) => {
    draftRef.current = typeof f === "function" ? f(draftRef.current) : f;
    setDraftState(draftRef.current);
  };
  const [sel, setSel] = useState<number | null>(null);
  // a new runner starts under the sprue or off a runner: picked by its first click
  const [origin, setOrigin] = useState<null | { kind: "sprue" } | { kind: "branch"; from: string; at: XY }>(null);
  // the ingate put wherever a runner runs into a part
  const lastGate = (() => { try { return JSON.parse(localStorage.getItem(GATE_KEY) ?? "{}"); } catch { return {}; } })();
  const [gSide, setGSide] = useState<string>(lastGate.side ?? "drag");
  const [gW, setGW] = useState<string>(str(lastGate.width)), [gT, setGT] = useState<string>(str(lastGate.thickness));
  const [gL, setGL] = useState<string>(str(lastGate.length));
  // step 2: cup, well, filter
  const [cupD, setCupD] = useState(""), [cupH, setCupH] = useState("");
  const [wellD, setWellD] = useState(""), [wellDepth, setWellDepth] = useState("");
  const [useFilter, setUseFilter] = useState(false);
  const [fShape, setFShape] = useState("square"), [fSize, setFSize] = useState(""), [fT, setFT] = useState("");
  // step 5: feeders and vents
  const [tool, setTool] = useState<"top" | "side" | "vent">("top");
  const [fdD, setFdD] = useState(""), [fdH, setFdH] = useState("");
  const [nW, setNW] = useState(""), [nT, setNT] = useState(""), [nSide, setNSide] = useState("cope");
  const [vD, setVD] = useState("");
  // step 4
  const [igSide, setIgSide] = useState("drag");
  const [igW, setIgW] = useState(""), [igT, setIgT] = useState("");
  const [selIg, setSelIg] = useState<string | null>(null);
  const gates: { id: string; runner: string; params: any }[] = state
    ? (state.document.elements as any[]).filter((e) => e.type === "ingate").map((e) => ({
        id: e.id, params: e.params,
        runner: (state.document.connections as any[]).find((c) => c.joint === "tee" && c.to.instance === e.id)?.from.instance,
      }))
    : [];
  const gate = gates.find((g) => g.id === selIg) ?? null;

  // a new design: start at the first unfinished step, forms filled from what is there
  useEffect(() => {
    if (!state) return;
    const b = state.box;
    setL(str(b?.size[0])); setW(str(b?.size[1])); setH(str(b?.size[2])); setP(str(b?.parting));
    setDTop(str(sprue?.params.d_top)); setDBot(str(sprue?.params.d_bottom));
    setActive(runnerIds[0] ?? null);
    setSelIg(null);
    {
      const cup = state ? el(state, "pouring_cup") : null, well = state ? el(state, "sprue_well") : null;
      const fl = state ? el(state, "filter") : null;
      setCupD(str(cup?.params.d_top)); setCupH(str(cup?.params.height));
      setWellD(str(well?.params.d)); setWellDepth(str(well?.params.depth));
      setUseFilter(!!fl); setFShape(fl?.params.shape ?? "square"); setFSize(str(fl?.params.size)); setFT(str(fl?.params.thickness));
    }
    setSuggesting(!b); setOptions(null); setProposal(null); setChoice("widest");
    setStep(gates.length ? 4 : sprue ? 3 : 1);
    setPart("part"); setExtending(false); setDraft([]); setSel(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.id]);
  useEffect(() => {
    setSx(sprue ? String(sprue.anchor.frame.origin[0]) : ""); setSy(sprue ? String(sprue.anchor.frame.origin[1]) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sprue?.anchor.frame.origin[0], sprue?.anchor.frame.origin[1]]);
  useEffect(() => setPlan(step !== 1), [step]); // eslint-disable-line react-hooks/exhaustive-deps
  // ask for the two ways to part it when the suggestion screen opens
  const fitting = step === 1 && suggesting && !!state?.part;
  useEffect(() => {
    if (!fitting || options || !state) return;
    let live = true;
    api.suggestFit(state.id).then((r) => {
      if (!live) return;
      setOptions(r.options);
      setChoice(r.options[0]?.id ?? "widest");
    }).catch(() => undefined);
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitting, options, state?.id]);
  const opt = options?.find((o) => o.id === choice) ?? options?.[0] ?? null;
  const clear: Clearances = {
    sides: num(cSides) || 0, below: num(cBelow) || 0, above: num(cAbove) || 0, extra: num(cExtra) || 0, step: num(cStep) || 0,
  };
  // the proposed box follows the choice and the clearances (and fills the box fields)
  useEffect(() => {
    if (!fitting || !opt || !state) return;
    try {
      localStorage.setItem(CLEAR_KEY, JSON.stringify({ sides: cSides, below: cBelow, above: cAbove, extra: cExtra, step: cStep }));
    } catch { /* private window: nothing remembered */ }
    const t = setTimeout(() => {
      api.proposeBox(state.id, opt.bbox, clear).then((p) => {
        setProposal(p);
        setL(String(p.length)); setW(String(p.width)); setH(String(p.height)); setP(String(p.parting));
      }).catch(() => undefined);
    }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitting, opt?.id, options, cSides, cBelow, cAbove, cExtra, cStep]);
  // picking a runner shows its sizes; a new runner starts from the last sizes used
  useEffect(() => {
    if (!runner) return;
    setSide(runner.params.side ?? "drag");
    setWp(str(runner.params.w_parting)); setWf(str(runner.params.w_far)); setRh(str(runner.params.height));
    setSel(null); setExtending(false); setDraft([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, state?.id]);

  const sid = state?.id ?? "";
  const g0 = runner && state ? geo(runner.id) : null;
  const waypoints: XY[] = g0 ? g0.pts : [];             // the active runner's points after its start
  const size = { side, w_parting: num(wp), w_far: num(wf), height: num(rh) };
  const sizeOk = ok(size.w_parting, size.w_far, size.height);
  const drawing = step === 3 && !!sprue && sizeOk && (extending || !runner);
  const needOrigin = drawing && !runner && runnerIds.length > 0 && !origin;    // the first click says where it starts
  const gateSpec = ok(num(gW), num(gT), num(gL)) ? { side: gSide, width: num(gW), thickness: num(gT), length: num(gL) } : null;
  const sendRunner = (points: XY[], s = size, rid: string | null = active, gate = gateSpec) =>
    run(async () => {
      const g = rid && state ? geo(rid) : null;
      const full = g?.branched ? [g.start, ...points] : points;       // a branch keeps its branch point first
      const branch = !rid && origin?.kind === "branch" ? { from: origin.from, at: v3(origin.at) } : null;
      const st = await api.drawRunner(sid, { ...s, points: full.map(v3), runner: rid, branch, gate });
      if (!rid) {                                        // a new runner: edit it from now on
        const ids = (st.document.elements as any[]).filter((e) => e.type === "runner").map((e) => e.id);
        setActive(ids[ids.length - 1] ?? null);
        setOrigin(null);
      }
      return st;
    });
  /** the ingate settings changed: every runner is drawn again with them (one undo step each) */
  const regate = (gate = gateSpec) => {
    try {   // the fields as typed (text), so they come back into the fields as they were
      localStorage.setItem(GATE_KEY, JSON.stringify({ side: gate?.side ?? gSide, width: gW, thickness: gT, length: gL }));
    } catch { /* private window: nothing remembered */ }
    for (const id of runnerIds) {
      const r = el(state!, id);
      sendRunner(geo(id).pts, { side: r.params.side, w_parting: r.params.w_parting, w_far: r.params.w_far, height: r.params.height }, id, gate);
    }
  };
  const finish = (at?: XY) => {
    let d = draftRef.current;
    const lastPt = d.length ? d[d.length - 1] : null;
    // the double-click's own point counts unless it lands on the point its first click just added
    if (at && (!lastPt || Math.hypot(at[0] - lastPt[0], at[1] - lastPt[1]) > DBL_MM)) d = [...d, at];
    const all = extending ? [...waypoints, ...d] : d;
    const pts = all.filter((p, i) => i === 0 || Math.hypot(p[0] - all[i - 1][0], p[1] - all[i - 1][1]) > 0.5);
    if (pts.length && !needOrigin) sendRunner(pts);
    setDraft([]); setExtending(false);
  };

  // a picked ingate shows its sizes; a new one uses the sizes as they are
  useEffect(() => {
    if (!gate) return;
    setIgSide(gate.params.side ?? "drag"); setIgW(str(gate.params.width)); setIgT(str(gate.params.thickness));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selIg]);
  const igSize = { side: igSide, width: num(igW), thickness: num(igT) };
  const igOk = ok(igSize.width, igSize.thickness);
  const sendIngate = (runnerId: string, start: XY, end: XY, id: string | null, sz = igSize) =>
    run(async () => {
      const st = await api.drawIngate(sid, { runner: runnerId, start, end, ...sz, ingate: id });
      if (!id) {
        const all = (st.document.elements as any[]).filter((e) => e.type === "ingate");
        setSelIg(all[all.length - 1]?.id ?? null);
      }
      return st;
    });
  const resizeIngate = (sz = igSize) => {
    if (!gate || !ok(sz.width, sz.thickness)) return;
    const [a, b] = (gate.params.waypoints as number[][]).map(xy);
    sendIngate(gate.runner, a, b, gate.id, sz);
  };
  useEffect(() => {
    if (step !== 4) return;
    const onKey = (e: KeyboardEvent) => {
      if (typing() || !selIg || (e.key !== "Backspace" && e.key !== "Delete")) return;
      e.preventDefault();
      run(() => api.removeIngate(sid, selIg));
      setSelIg(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // keys while sketching: Enter finishes, Escape stops, Backspace/Delete removes a point
  useEffect(() => {
    if (step !== 3) return;
    const onKey = (e: KeyboardEvent) => {
      if (typing()) return;
      if (drawing && e.key === "Enter") { e.preventDefault(); finish(); }
      else if (drawing && e.key === "Escape") { e.preventDefault(); if (draft.length) finish(); else setExtending(false); }
      else if (drawing && (e.key === "Backspace" || e.key === "Delete")) { e.preventDefault(); setDraft((d) => d.slice(0, -1)); }
      else if (!drawing && sel !== null && (e.key === "Backspace" || e.key === "Delete") && waypoints.length > 1) {
        e.preventDefault(); sendRunner(waypoints.filter((_, i) => i !== sel)); setSel(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!state || !state.part) return { panel: null, view: { mode: "none" } };
  const box = state.box;
  const parts = state.parts ?? [];
  const cur = parts.find((p) => p.id === part) ?? parts[0];
  const cav = cur && cur.id !== "part" ? cur.id : null;
  const outside = state.issues.filter((i) => (i.rule === "mold.outside_flask" || i.rule === "mold.parts_overlap") && i.instances.includes("part"));
  const sprueAt: XY | null = sprue ? xy(sprue.anchor.frame.origin) : null;
  const diaOk = ok(num(dTop), num(dBot));
  const placeSprue = (p: XY) => run(() => api.placeSprue(sid, { x: Math.round(p[0]), y: Math.round(p[1]), d_top: num(dTop), d_bottom: num(dBot) }));
  const others = runnerIds.filter((id) => id !== active).map((id) => {
    const g = geo(id);
    return { id, start: g.start, points: g.pts, width: g.width };
  });
  const newStart: XY | null = origin?.kind === "branch" ? origin.at : origin?.kind === "sprue" || !runnerIds.length ? sprueAt : null;
  const total = (() => {
    const c = [g0?.start ?? sprueAt!, ...waypoints];
    return c.slice(1).reduce((s, b, i) => s + Math.hypot(b[0] - c[i][0], b[1] - c[i][1]), 0);
  })();

  // ---- lines to align with: sprue, runner points, ingate ends, part centres and edges, the box axes ----
  const guides: Guides = { xs: [0], ys: [0] };
  if (sprueAt) { guides.xs.push(sprueAt[0]); guides.ys.push(sprueAt[1]); }
  for (const id of runnerIds) for (const p of [geo(id).start, ...geo(id).pts]) { guides.xs.push(p[0]); guides.ys.push(p[1]); }
  for (const g of gates) for (const p of g.params.waypoints as number[][]) { guides.xs.push(p[0]); guides.ys.push(p[1]); }
  for (const p of parts) {
    const [lo, hi] = p.bbox;
    guides.xs.push(lo[0], hi[0], (lo[0] + hi[0]) / 2);
    guides.ys.push(lo[1], hi[1], (lo[1] + hi[1]) / 2);
  }

  // a click that starts a new runner: on the sprue, or on a runner (it branches there)
  const pickOrigin = (p: XY) => {
    if (sprueAt && Math.hypot(p[0] - sprueAt[0], p[1] - sprueAt[1]) <= Math.max(num(dTop) || 0, 20) / 2 + 5) {
      setOrigin({ kind: "sprue" });
      return;
    }
    let best: { id: string; at: XY; d: number } | null = null;
    for (const id of runnerIds) {
      const g = geo(id);
      const n = nearestOn([g.start, ...g.pts], p);
      if (n.d <= g.width / 2 + 5 && (!best || n.d < best.d)) best = { id, at: [Math.round(n.at[0]), Math.round(n.at[1])], d: n.d };
    }
    if (best) setOrigin({ kind: "branch", from: best.id, at: best.at });
  };

  // ---- what the viewer does ----
  let view: SetupView = { mode: "none" };
  const boxDims = { length: num(L), width: num(W), height: num(H), parting: num(P) };
  const boxOk = ok(boxDims.length, boxDims.width, boxDims.height, boxDims.parting) && boxDims.parting < boxDims.height;
  if (fitting && opt) {
    view = {
      mode: "preview",
      move: mul(shiftBy(proposal?.shift ?? [0, 0, 0]), opt.move),
      box: boxOk ? {
        size: [boxDims.length, boxDims.width, boxDims.height], parting: boxDims.parting, bottom: -boxDims.parting,
        top: boxDims.height - boxDims.parting, centre: [0, 0, boxDims.height / 2 - boxDims.parting],
      } : null,
    };
  }
  if (step === 1 && box && cur && !fitting) {
    view = {
      mode: "fit", part: cur.id, onSelectPart: setPart, guides: sprueAt ? { xs: [sprueAt[0]], ys: [sprueAt[1]] } : undefined,
      onMovePart: (d, id) => run(() => api.movePart(sid, d, (id ?? cur.id) === "part" ? null : (id ?? cur.id))),
    };
  }
  if (step >= 2 && box) {
    let hint = "";
    if (step === 2) hint = !sprue ? (diaOk ? "Click where the sprue goes." : "Type the sprue's top and bottom diameter first.") : "Drag the sprue to move it.";
    if (step === 3) {
      if (!sizeOk) hint = "Type the runner's widths and height first.";
      else if (needOrigin) hint = "Click the sprue, or any point on a runner, to start the new runner there";
      else if (drawing) hint = `Click to add points${draft.length ? ` · double-click or ${ENTER_KEY} to finish · ${BACK_KEY} removes the last` : ""} · run it into a part to gate it · hold ${FREE_KEY} to turn snapping off`;
      else hint = `Drag a blue point to move it · drag a white dot to add a bend · click a point and press ${DEL_KEY} to remove it`;
    }
    view = {
      mode: "plan",
      hint,
      sprue: { at: sprueAt, dTop: num(dTop) || 0, dBottom: num(dBot) || 0, canPlace: step === 2 && diaOk, onPlace: placeSprue },
      others: step === 3 ? others : [],
      guides,
      onPickOther: (id) => { setActive(id); setDraft([]); setExtending(false); },
      runner: step === 3 && sprue && sizeOk ? {
        width: size.w_parting,
        start: runner ? g0!.start : newStart,
        points: drawing ? (extending ? [...waypoints, ...draft] : draft) : waypoints,
        drawing,
        onAdd: (p) => (needOrigin ? pickOrigin(p) : setDraft((d) => [...d, p])),
        onFinish: finish,
        onChange: (pts) => sendRunner(pts),
        selected: sel,
        onSelect: setSel,
      } : null,
    };
  }

  if (step === 5 && box) {
    const d = tool === "vent" ? num(vD) : num(fdD);
    const ready = tool === "vent" ? ok(d) : tool === "top" ? ok(d, num(fdH)) : ok(d, num(fdH), num(nW), num(nT));
    view = {
      mode: "place",
      d: ready ? d : 0,
      guides,
      hint: !ready ? "Type the sizes first."
        : tool === "top" ? "Click on the part where the feeder stands"
        : tool === "side" ? "Click beside the part: the feeder stands there and its neck runs to the part"
        : "Click on the part where the vent goes up",
      onPlace: (p) => {
        if (!ready) return;
        const [x, y] = [Math.round(p[0]), Math.round(p[1])];
        if (tool === "vent") run(() => api.addVent(sid, { x, y, d }));
        else run(() => api.addFeeder(sid, { kind: tool, x, y, d, height: num(fdH),
          neck: tool === "side" ? { width: num(nW), thickness: num(nT), side: nSide } : null }));
      },
    };
  }

  if (step === 4 && box && sprueAt) {
    view = {
      mode: "ingates",
      hint: !igOk ? "Type the ingate's width and thickness first."
        : `Press on a runner and drag to the part's edge · drag an end of an ingate to move it · click one and press ${DEL_KEY} to remove it`,
      runners: runnerIds.map((id) => {
        const g = geo(id);
        return { id, chain: [g.start, ...g.pts], width: g.width };
      }),
      guides,
      ingates: gates.map((g) => {
        const [a, b] = (g.params.waypoints as number[][]).map(xy);
        return { id: g.id, runner: g.runner, start: a, end: b, width: Number(g.params.width) };
      }),
      width: igOk ? igSize.width : null,
      selected: selIg,
      onSelect: setSelIg,
      onCreate: (r, a, b) => sendIngate(r, a, b, null),
      onEdit: (id, r, a, b) => sendIngate(r, a, b, id, (() => {
        const g = gates.find((x) => x.id === id)!;
        return { side: g.params.side, width: Number(g.params.width), thickness: Number(g.params.thickness) };
      })()),
    };
  }

  const head = (n: Step, title: string, done: boolean, enabled: boolean) => (
    <button className={`step ${step === n ? "on" : ""}`} disabled={!enabled} onClick={() => { setStep(n); setExtending(false); setDraft([]); }}>
      <span className="n">{done ? "✓" : n}</span> {title}
    </button>
  );

  const panel = (
    <div className="setup">
      <div className="steps">
        {head(1, "Box & parts", !!box, true)}
        {head(2, "Sprue", !!sprue, !!box)}
        {head(3, "Runners", runnerIds.length > 0, !!sprue)}
        {head(4, "Ingates", gates.length > 0, runnerIds.length > 0)}
        {head(5, "Feeders & vents", (state.document.elements as any[]).some((e) => e.type === "feeder" || e.type === "vent"), !!box)}
      </div>

      {fitting && (
        <section>
          <h4>How to part it</h4>
          {!options ? (
            <p className="hint">Working out how to turn and part the casting…</p>
          ) : (
            <div className="parts">
              {options.map((o) => (
                <div key={o.id} className={`partrow option ${o.id === opt?.id ? "on" : ""}`} onClick={() => setChoice(o.id)}>
                  <div className="grow">
                    <b>{o.id === "widest" ? "Parted at its widest section" : "Largest flat face on the parting plane"}</b>
                    <div className="muted">
                      {o.above < 0.05 ? `whole part in the drag, ${o.below.toFixed(0)} mm deep`
                        : o.below < 0.05 ? `whole part in the cope, ${o.above.toFixed(0)} mm high`
                        : `${o.below.toFixed(0)} mm in the drag · ${o.above.toFixed(0)} mm in the cope`}
                      {" · "}
                      {o.undercut_mm2 > 0 ? <span className="badtext">about {(o.undercut_mm2 / 100).toFixed(1)} cm² undercut</span> : "no undercuts"}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
          {opt && (
            <>
              <h4>Box</h4>
              <p className="muted">Sand to leave around the part (mm). Blank is none.</p>
              <div className="fields">
                <Field label="At the sides" value={cSides} set={setCSides} />
                <Field label="Below the part (drag)" value={cBelow} set={setCBelow} />
                <Field label="Above the part (cope)" value={cAbove} set={setCAbove} />
                <Field label="Extra length for sprue & runner" value={cExtra} set={setCExtra} />
                <Field label="Round the box up to" value={cStep} set={setCStep} />
              </div>
              <p className="muted">
                {proposal?.standard ? <>Your standard box <b>{proposal.standard}</b> fits.</>
                  : clear.step ? `Part plus sand, rounded up to ${clear.step} mm.` : "Part plus sand."}
                {" "}Edit the box if you want it different:
              </p>
              <div className="fields">
                <Field label="Length" value={L} set={setL} />
                <Field label="Width" value={W} set={setW} />
                <Field label="Height" value={H} set={setH} />
                <Field label="Parting plane height" value={P} set={setP} />
              </div>
              <div className="row">
                <button className="primary" disabled={!boxOk}
                  onClick={() => {
                    run(() => api.applyFit(sid, { move: opt.move, ...boxDims, shift: proposal?.shift ?? [0, 0, 0] }));
                    setSuggesting(false);
                  }}>Use this</button>
                <button disabled={!boxOk}
                  onClick={() => run(async () => {
                    await api.addMatchbox({ length: boxDims.length, width: boxDims.width, cope: boxDims.height - boxDims.parting, drag: boxDims.parting });
                    setNoteText(`Saved ${boxDims.length} × ${boxDims.width}, cope ${boxDims.height - boxDims.parting}, drag ${boxDims.parting} as a standard box`);
                  })}>Save as a standard box</button>
                {box && <button onClick={() => setSuggesting(false)}>Cancel</button>}
              </div>
              {noteText && <p className="good">{noteText}</p>}
            </>
          )}
        </section>
      )}

      {step === 1 && !fitting && (
        <section>
          <button onClick={() => { setOptions(null); setSuggesting(true); }}>Suggest how to part it again</button>
          <h4>Molding box</h4>
          <p className="muted">Inside size of the box. The parting plane is measured up from the box bottom.</p>
          <div className="fields">
            <Field label="Length" value={L} set={setL} />
            <Field label="Width" value={W} set={setW} />
            <Field label="Height" value={H} set={setH} />
            <Field label="Parting plane height" value={P} set={setP} />
          </div>
          <button
            className="primary"
            disabled={!ok(num(L), num(W), num(H), num(P))}
            onClick={() => run(() => api.setBox(sid, { length: num(L), width: num(W), height: num(H), parting: num(P) }))}
          >
            {box ? "Update box" : "Make box"}
          </button>
          {box && cur && (
            <>
              <h4>Parts in the box</h4>
              <p className="muted">Click a part (here or in the view), then drag its arrows to move it. In <b>Plan</b> view drag
                a part by its outline: it snaps to line up with the other parts, and green lines show which already line up.</p>
              <div className="parts">
                {parts.map((p) => (
                  <div key={p.id} className={`partrow ${p.id === cur.id ? "on" : ""}`} onClick={() => setPart(p.id)}>
                    <span className="grow">{p.name}{p.kind === "copy" ? " (copy)" : ""}</span>
                    <span className="muted">{p.volume_mm3 ? `${(p.volume_mm3 / 1000).toFixed(1)} cm³` : ""}</span>
                  </div>
                ))}
              </div>
              <div className="row">
                {(["x", "y", "z"] as const).map((a) => (
                  <button key={a} onClick={() => run(() => api.turnPart(sid, a, 90, cav))}>Turn 90° about {a.toUpperCase()}</button>
                ))}
              </div>
              <div className="row">
                <button onClick={() => run(() => api.centrePart(sid, cav))}>Centre in box</button>
                <button onClick={() => run(() => api.movePart(sid, [0, 0, -cur.bbox[1][2]], cav))}>Top on parting plane</button>
                <button onClick={() => run(() => api.movePart(sid, [0, 0, -cur.bbox[0][2]], cav))}>Bottom on parting plane</button>
              </div>
              <div className="row">
                <button onClick={() => run(() => api.addCopy(sid, cav))}>Add a copy</button>
                <button onClick={() => addInput.current?.click()}>Add another part file…</button>
                <input ref={addInput} type="file" accept=".step,.stp,.stl" hidden
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = "";
                    if (f) run(async () => api.addPartFile(sid, await api.upload(f)));
                  }} />
                {cav && <button onClick={() => { run(() => api.removePart(sid, cav)); setPart("part"); }}>Remove this part</button>}
              </div>
              {outside.length ? (
                outside.map((o, k) => (
                  <p key={k} className="bad">
                    {parts.reduce((m, p) => m.replace(p.id === "part" ? /^part (?=reaches)/ : `part.${p.id}`, `${p.name}${p.kind === "copy" ? " (copy)" : ""} `), o.message)
                      .replace("molding box", "box").replace(/\s+/g, " ")}.
                  </p>
                ))
              ) : (
                <p className="good">Every part is inside the box, none overlapping.</p>
              )}
              <button className="primary" onClick={() => setStep(2)}>Next: sprue</button>
            </>
          )}
        </section>
      )}

      {step === 2 && box && (
        <section>
          <h4>Sprue</h4>
          <p className="muted">Straight down from the top of the box to the runner. You are looking down on the parting plane.</p>
          <div className="fields">
            <Field label="Top diameter" value={dTop} set={setDTop}
              onCommit={() => { if (sprue && diaOk && num(dTop) !== sprue.params.d_top) placeSprue(sprueAt!); }} />
            <Field label="Bottom diameter" value={dBot} set={setDBot}
              onCommit={() => { if (sprue && diaOk && num(dBot) !== sprue.params.d_bottom) placeSprue(sprueAt!); }} />
          </div>
          {sprue && (
            <>
              <div className="fields">
                <Field label="Position x" value={sx} set={setSx}
                  onCommit={() => { if (Number.isFinite(num(sx)) && num(sx) !== sprueAt![0]) placeSprue([num(sx), sprueAt![1]]); }} />
                <Field label="Position y" value={sy} set={setSy}
                  onCommit={() => { if (Number.isFinite(num(sy)) && num(sy) !== sprueAt![1]) placeSprue([sprueAt![0], num(sy)]); }} />
              </div>
              <p className="muted">Length {Number(sprue.params.length).toFixed(1)} mm.</p>

              <h4>Pouring cup</h4>
              <p className="muted">A cone cut into the top of the cope over the sprue; its bottom is the sprue's top.</p>
              <div className="fields">
                <Field label="Top diameter" value={cupD} set={setCupD} />
                <Field label="Height" value={cupH} set={setCupH} />
              </div>
              <div className="row">
                <button disabled={!ok(num(cupD), num(cupH))} onClick={() => run(() => api.setCup(sid, { d_top: num(cupD), height: num(cupH) }))}>
                  {el(state, "pouring_cup") ? "Update cup" : "Add cup"}
                </button>
                {el(state, "pouring_cup") && <button onClick={() => run(() => api.setCup(sid, { d_top: null, height: null }))}>Remove cup</button>}
              </div>

              <h4>Sprue well and filter</h4>
              <p className="muted">The well under the sprue that the runners leave from, reaching below the deepest runner.
                A filter lies over it on the parting plane, the sprue coming down onto it.</p>
              <div className="fields">
                <Field label="Well diameter" value={wellD} set={setWellD} />
                <Field label="Depth below the runners" value={wellDepth} set={setWellDepth} />
              </div>
              <label className="check"><input type="checkbox" checked={useFilter} onChange={(e) => setUseFilter(e.target.checked)} /> Filter over the well</label>
              {useFilter && (
                <>
                  <div className="row">
                    {["square", "round"].map((sh) => (
                      <button key={sh} className={fShape === sh ? "toggle on" : "toggle"} onClick={() => setFShape(sh)}>{sh}</button>
                    ))}
                  </div>
                  <div className="fields">
                    <Field label={fShape === "square" ? "Filter side" : "Filter diameter"} value={fSize} set={setFSize} />
                    <Field label="Filter thickness" value={fT} set={setFT} />
                  </div>
                </>
              )}
              <div className="row">
                <button disabled={!ok(num(wellD)) || !(num(wellDepth) >= 0) || (useFilter && !ok(num(fSize), num(fT)))}
                  onClick={() => run(() => api.setWell(sid, { d: num(wellD), depth: num(wellDepth),
                    filter: useFilter ? { shape: fShape, size: num(fSize), thickness: num(fT) } : null }))}>
                  {el(state, "sprue_well") ? "Update well" : "Add well"}
                </button>
                {el(state, "sprue_well") && <button onClick={() => run(() => api.setWell(sid, { d: null, depth: null, filter: null }))}>Remove well</button>}
              </div>
              <button className="primary" onClick={() => setStep(3)}>Next: runner</button>
            </>
          )}
        </section>
      )}

      {step === 3 && sprue && (
        <section>
          <h4>Runners</h4>
          <div className="parts">
            {runnerIds.map((id, k) => {
              const r = el(state, id);
              const g = geo(id);
              const c: XY[] = [g.start, ...g.pts];
              const len = c.slice(1).reduce((t, b, i) => t + Math.hypot(b[0] - c[i][0], b[1] - c[i][1]), 0);
              const gated = gates.some((x) => x.runner === id && x.params.placed === "auto");
              return (
                <div key={id} className={`partrow ${id === active ? "on" : ""}`}
                  onClick={() => { setActive(id); setDraft([]); setExtending(false); setOrigin(null); }}>
                  <span className="grow">Runner {k + 1}{g.from ? <span className="muted"> · off runner {runnerIds.indexOf(g.from) + 1}</span> : null}</span>
                  <span className="muted">{r.params.side} · {len.toFixed(0)} mm{gated ? " · gated" : ""}</span>
                </div>
              );
            })}
            {!active && (
              <div className="partrow on">
                <span className="grow">New runner</span>
                <span className="muted">{origin?.kind === "branch" ? `off runner ${runnerIds.indexOf(origin.from) + 1}` : origin || !runnerIds.length ? "from the sprue" : "pick its start"}</span>
              </div>
            )}
          </div>
          {active && (
            <button onClick={() => { setActive(null); setDraft([]); setExtending(false); setOrigin(null); }}>
              + New runner (from the sprue or off a runner)
            </button>
          )}
          <h4>{active ? `Runner ${runnerIds.indexOf(active) + 1}` : "New runner"}</h4>
          <div className="row">
            {["drag", "cope"].map((s) => (
              <button key={s} className={side === s ? "toggle on" : "toggle"}
                onClick={() => { setSide(s); if (runner && sizeOk) sendRunner(waypoints, { ...size, side: s }); }}>
                {s === "drag" ? "In the drag (below)" : "In the cope (above)"}
              </button>
            ))}
          </div>
          <div className="fields">
            <Field label="Width at parting plane" value={wp} set={setWp} onCommit={() => { if (runner && sizeOk) sendRunner(waypoints); }} />
            <Field label="Width away from it" value={wf} set={setWf} onCommit={() => { if (runner && sizeOk) sendRunner(waypoints); }} />
            <Field label="Height" value={rh} set={setRh} onCommit={() => { if (runner && sizeOk) sendRunner(waypoints); }} />
          </div>
          {drawing ? (
            <div className="row">
              <button className="primary" disabled={!draft.length} onClick={() => finish()}>Finish</button>
              {(runner || runnerIds.length > 0) && (
                <button onClick={() => { setExtending(false); setDraft([]); setOrigin(null); if (!runner) setActive(runnerIds[0]); }}>Cancel</button>
              )}
            </div>
          ) : runner ? (
            <>
              <p className="muted">{waypoints.length} point{waypoints.length === 1 ? "" : "s"} · {total.toFixed(0)} mm long</p>
              <div className="row">
                <button onClick={() => { setDraft([]); setExtending(true); }}>Continue drawing</button>
                <button onClick={() => {
                  const rest = runnerIds.filter((x) => x !== active);
                  run(() => api.removeRunner(sid, active!));
                  setActive(rest[0] ?? null);
                }}>Delete this runner</button>
              </div>
              <button onClick={() => setStep(4)}>Ingates…</button>
            </>
          ) : null}
          <h4>Ingate where a runner meets a part</h4>
          <p className="muted">Run a runner into a part: it is cut back by the ingate's length, and this ingate joins it to the part's edge. Blank: no ingate.</p>
          <div className="row">
            {["drag", "cope"].map((sd) => (
              <button key={sd} className={gSide === sd ? "toggle on" : "toggle"}
                onClick={() => { setGSide(sd); if (gateSpec) regate({ ...gateSpec, side: sd }); }}>
                {sd === "drag" ? "In the drag" : "In the cope"}
              </button>
            ))}
          </div>
          <div className="fields">
            <Field label="Ingate width" value={gW} set={setGW} onCommit={() => regate()} />
            <Field label="Ingate thickness" value={gT} set={setGT} onCommit={() => regate()} />
            <Field label="Ingate length" value={gL} set={setGL} onCommit={() => regate()} />
          </div>
        </section>
      )}

      {step === 5 && box && (
        <section>
          <h4>Feeders and vents</h4>
          <div className="row">
            {([["top", "Top feeder"], ["side", "Side feeder"], ["vent", "Vent"]] as const).map(([k, label]) => (
              <button key={k} className={tool === k ? "toggle on" : "toggle"} onClick={() => setTool(k)}>{label}</button>
            ))}
          </div>
          {tool !== "vent" ? (
            <div className="fields">
              <Field label="Feeder diameter" value={fdD} set={setFdD} />
              <Field label="Feeder height" value={fdH} set={setFdH} />
              {tool === "side" && (
                <>
                  <Field label="Neck width" value={nW} set={setNW} />
                  <Field label="Neck thickness" value={nT} set={setNT} />
                </>
              )}
            </div>
          ) : (
            <div className="fields"><Field label="Vent diameter" value={vD} set={setVD} /></div>
          )}
          {tool === "side" && (
            <div className="row">
              {["cope", "drag"].map((sd) => (
                <button key={sd} className={nSide === sd ? "toggle on" : "toggle"} onClick={() => setNSide(sd)}>Neck in the {sd}</button>
              ))}
            </div>
          )}
          <p className="muted">{tool === "top" ? "A closed feeder standing on the part's top, in the cope." : tool === "side"
            ? "A closed feeder standing on the parting plane beside the part, joined to it by a neck." : "A round vent from the part straight up to the top of the box."}</p>
          <div className="parts">
            {(state.document.elements as any[]).filter((e) => e.type === "feeder" || e.type === "vent").map((e) => (
              <div key={e.id} className="partrow">
                <span className="grow">{e.type === "vent" ? `Vent · ⌀${e.params.d}` : `${e.params.kind === "top" ? "Top" : "Side"} feeder · ⌀${e.params.d} × ${e.params.height}`}</span>
                <button className="link" onClick={() => run(() => api.removeAny(sid, e.id))}>remove</button>
              </div>
            ))}
          </div>
        </section>
      )}

      {step === 4 && sprue && (
        <section>
          <h4>Ingates</h4>
          <p className="muted">Press on a runner and drag to the part's edge. The end snaps onto the part's outline.</p>
          <div className="parts">
            {gates.map((g, k) => {
              const [a, b] = (g.params.waypoints as number[][]).map(xy);
              return (
                <div key={g.id} className={`partrow ${g.id === selIg ? "on" : ""}`} onClick={() => setSelIg(g.id)}>
                  <span className="grow">Ingate {k + 1}</span>
                  <span className="muted">
                    runner {runnerIds.indexOf(g.runner) + 1} · {g.params.side} · {Math.hypot(b[0] - a[0], b[1] - a[1]).toFixed(0)} mm
                  </span>
                </div>
              );
            })}
          </div>
          <h4>{gate ? `Ingate ${gates.indexOf(gate) + 1}` : "New ingates"}</h4>
          <div className="row">
            {["drag", "cope"].map((sd) => (
              <button key={sd} className={igSide === sd ? "toggle on" : "toggle"}
                onClick={() => { setIgSide(sd); resizeIngate({ ...igSize, side: sd }); }}>
                {sd === "drag" ? "In the drag (below)" : "In the cope (above)"}
              </button>
            ))}
          </div>
          <div className="fields">
            <Field label="Width" value={igW} set={setIgW} onCommit={() => resizeIngate()} />
            <Field label="Thickness" value={igT} set={setIgT} onCommit={() => resizeIngate()} />
          </div>
          {gate && (
            <div className="row">
              <button onClick={() => setSelIg(null)}>Done with this one</button>
              <button onClick={() => { run(() => api.removeIngate(sid, gate.id)); setSelIg(null); }}>Delete this ingate</button>
            </div>
          )}
        </section>
      )}
    </div>
  );
  return { panel, view };
}
