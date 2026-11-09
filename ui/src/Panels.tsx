// Right-hand panels: part and mold setup, process inputs, calculators, and building the gating graph.
import { useState } from "react";
import { api, type DesignState, type ElementType, type FaceCheck, type Vec3 } from "./api";

type Run = (f: () => Promise<DesignState | void>) => void;
export type PickMode = "none" | "gate" | "face_down";

const n = (v: unknown, d = 2) => (typeof v === "number" ? (Math.abs(v) >= 1e5 ? v.toExponential(3) : v.toFixed(d)) : "–");

function Num({ label, value, unit, onCommit }: { label: string; value: number | null | undefined; unit?: string; onCommit: (v: number | null) => void }) {
  const [t, setT] = useState(value == null ? "" : String(value));
  const commit = () => {
    const v = t.trim() === "" ? null : Number(t);
    if (v !== value && !(typeof v === "number" && Number.isNaN(v))) onCommit(v);
  };
  return (
    <tr>
      <td>{label}</td>
      <td>
        <input value={t} placeholder="not set" onChange={(e) => setT(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
      </td>
      <td className="muted">{unit}</td>
    </tr>
  );
}

const setOp = (path: string, value: unknown) => ({ op: "add", path, value });

export function PartPanel({ state, run, mode, setMode, checks, setChecks }: {
  state: DesignState; run: Run; mode: PickMode; setMode: (m: PickMode) => void;
  checks: FaceCheck[] | null; setChecks: (c: FaceCheck[] | null) => void;
}) {
  const part = state.part;
  const doc = state.document;
  const [up, setUp] = useState("0, 0, 1");
  const [z, setZ] = useState("0");
  if (!part) return <div className="muted">No part in the design.</div>;
  const rot = (axis: string, angle_deg: number) => run(() => api.orient(state.id, { op: "rotate", axis, angle_deg }));
  return (
    <>
      <h3>Part</h3>
      <table>
        <tbody>
          <tr><td>file</td><td colSpan={2} className="small">{doc.part.source}</td></tr>
          <tr><td>file units</td><td>{part.source_unit ?? "–"}</td><td className="muted">read as {doc.part.units ?? "auto"}</td></tr>
          <tr><td>volume (as cast)</td><td>{n(part.volume_mm3 / 1000, 1)}</td><td className="muted">cm³</td></tr>
          <tr><td>weight</td><td>{state.calc?.readouts.casting_kg != null ? n(state.calc.readouts.casting_kg, 2) : "needs density"}</td><td className="muted">kg</td></tr>
          <tr><td>shrinkage used</td><td>{n(part.shrinkage_pct, 2)}</td><td className="muted">%</td></tr>
        </tbody>
      </table>
      <table>
        <tbody>
          <tr>
            <td>units</td>
            <td>
              <select value={doc.part.units ?? "auto"} onChange={(e) => run(() => api.patch(state.id, [setOp("/part/units", e.target.value)]))}>
                {["auto", "mm", "cm", "m", "inch"].map((u) => <option key={u}>{u}</option>)}
              </select>
            </td>
          </tr>
          <Num label="shrinkage" unit="%" value={doc.part.shrinkage_pct} onCommit={(v) => run(() => api.patch(state.id, [setOp("/part/shrinkage_pct", v)]))} />
        </tbody>
      </table>
      <h4>Pouring position</h4>
      <div className="row">
        {["x", "y", "z"].map((a) => (
          <span key={a}>
            <button onClick={() => rot(a, 90)}>{a.toUpperCase()}+90</button>
            <button onClick={() => rot(a, -90)}>{a.toUpperCase()}−90</button>
          </span>
        ))}
      </div>
      <div className="row">
        part direction up <input className="short" value={up} onChange={(e) => setUp(e.target.value)} />
        <button onClick={() => run(() => api.orient(state.id, { op: "up_vector", vector: up.split(",").map(Number) }))}>Set</button>
      </div>
      <div className="row">
        <button className={mode === "face_down" ? "on" : ""} onClick={() => setMode(mode === "face_down" ? "none" : "face_down")}>Pick a face to put down</button>
      </div>
      <h4>Parting plane (Z = 0)</h4>
      <div className="row">
        move the plane now at z <input className="short" value={z} onChange={(e) => setZ(e.target.value)} />
        <button onClick={() => run(() => api.orient(state.id, { op: "parting_at", z: Number(z) }))}>to Z = 0</button>
      </div>
      <table>
        <tbody>
          <Num label="min draft" unit="deg" value={doc.mold.draft_min_deg} onCommit={(v) => run(() => api.patch(state.id, [setOp("/mold/draft_min_deg", v)]))} />
        </tbody>
      </table>
      <div className="row">
        <button onClick={() => run(async () => setChecks((await api.moldCheck(state.id)).faces))}>Check draft and undercuts</button>
        {checks && <button onClick={() => setChecks(null)}>Clear</button>}
      </div>
      {checks && (
        <div className="small">
          {checks.filter((f) => f.mesh).length} flagged face(s) of {checks.length}
          {checks.filter((f) => f.mesh).map((f) => (
            <div key={f.face}>
              face {f.face} · {f.kind} · {f.side} · draft {n(f.min_draft_deg, 1)}° {f.undercut_fraction > 0 && `· undercut ${n(100 * f.undercut_fraction, 0)}%`}
            </div>
          ))}
        </div>
      )}
      <h4>Gate faces</h4>
      <div className="row">
        <button className={mode === "gate" ? "on" : ""} onClick={() => setMode(mode === "gate" ? "none" : "gate")}>Pick a gate face on the part</button>
      </div>
      {Object.keys(part.ports).map((k) => <div key={k} className="small">{k}</div>)}
      <h4>Flask</h4>
      <table>
        <tbody>
          <Num label="margin" unit="mm" value={doc.mold.flask?.margin_mm} onCommit={(v) => run(() => api.patch(state.id, [setOp("/mold/flask", v == null ? null : { margin_mm: v })]))} />
        </tbody>
      </table>
    </>
  );
}

export function ProcessPanel({ state, run, presets }: { state: DesignState; run: Run; presets: string[] }) {
  const p = state.document.process;
  const set = (k: string, v: unknown) => run(() => api.patch(state.id, [setOp(`/process/${k}`, v)]));
  const sel = (k: string, opts: string[]) => (
    <select value={p[k] ?? ""} onChange={(e) => set(k, e.target.value || null)}>
      <option value="">–</option>
      {opts.map((o) => <option key={o}>{o}</option>)}
    </select>
  );
  return (
    <>
      <h3>Process</h3>
      <table>
        <tbody>
          <tr><td>process</td><td>{sel("process", ["green_sand", "no_bake"])}</td></tr>
          <tr><td>alloy</td><td>{sel("alloy", ["grey_iron", "sg_iron", "steel"])}</td></tr>
          <tr><td>preset</td><td>{sel("preset", presets)}</td></tr>
          <tr><td>gating system</td><td>{sel("gating_system", ["unpressurized", "pressurized"])}</td></tr>
          <tr><td>effective head</td><td>{sel("head_method", ["top", "bottom", "parting"])}</td></tr>
          <Num label="section thickness" unit="mm" value={p.section_thickness_mm} onCommit={(v) => set("section_thickness_mm", v)} />
          <Num label="basin head" unit="mm" value={p.basin_head_mm} onCommit={(v) => set("basin_head_mm", v)} />
          <Num label="pour time override" unit="s" value={p.pour_time_s} onCommit={(v) => set("pour_time_s", v)} />
        </tbody>
      </table>
      <h4>Preset values</h4>
      {state.preset ? (
        <pre className="small">{JSON.stringify(state.preset, null, 1)}</pre>
      ) : (
        <div className="muted">choose a process and alloy (or a preset)</div>
      )}
    </>
  );
}

export function CalcPanel({ state, run }: { state: DesignState; run: Run }) {
  const c = state.calc;
  if (!c) return null;
  const ro = c.readouts, out = c.outputs;
  const rows: [string, unknown, string][] = [
    ["casting", ro.casting_kg, "kg"], ["gating", ro.gating_kg, "kg"], ["poured", ro.poured_kg, "kg"], ["yield", ro.yield_pct, "%"],
    ["pour time", out.pour_time_s, "s"], ["flow", out.flow_l_s, "L/s"], ["metal head", out.head_mm, "mm"],
    ["effective head", out.effective_head_mm, "mm"], ["required choke", out.required_choke_mm2, "mm²"],
    ["sprue area (each)", out.sprue_area_each_mm2, "mm²"], ["runner area (each)", out.runner_area_each_mm2, "mm²"],
    ["ingate area (each)", out.ingate_area_each_mm2, "mm²"],
  ];
  const missing = c.issues.filter((i) => i.rule === "preset.missing" || i.rule === "calc.input_missing");
  return (
    <>
      <h3>Calculators</h3>
      <table>
        <tbody>
          {rows.map(([k, v, u]) => (
            <tr key={k}><td>{k}</td><td>{n(v)}</td><td className="muted">{u}</td></tr>
          ))}
        </tbody>
      </table>
      {out.pour_time_source && <div className="small muted">pour time: {out.pour_time_source}</div>}
      <button disabled={!Object.keys(c.driven).length && out.ingate_area_each_mm2 == null} onClick={() => run(() => api.applyCalc(state.id))}>
        Apply areas to elements
      </button>
      {missing.length > 0 && (
        <>
          <h4>Missing</h4>
          {missing.map((i, k) => <div key={k} className="small">{i.message}</div>)}
        </>
      )}
      <h4>Paths</h4>
      {c.paths.map((p, k) => (
        <div key={k} className="small">
          {p.elements.join(" → ")}{p.ends_at_part ? " → part" : " (blind)"}<br />
          choke {p.choke_plane} {n(p.choke_mm2, 1)} mm² ({p.choke_role ?? "no role"}){p.ratio && ` · ratio ${p.ratio.join(" : ")}`}
        </div>
      ))}
      <h4>Planes</h4>
      <table className="small">
        <thead><tr><td>plane</td><td>mm²</td><td>share</td><td>m/s</td><td>Re</td></tr></thead>
        <tbody>
          {c.planes.map((p) => (
            <tr key={p.name}>
              <td>{p.name}</td><td>{n(p.area_mm2, 0)}</td><td>{n(p.flow_share, 2)}</td><td>{n(p.velocity_m_s)}</td><td>{p.reynolds != null ? n(p.reynolds, 0) : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function BuildPanel({ state, run, types, selected }: { state: DesignState; run: Run; types: Record<string, ElementType>; selected: string | null }) {
  const ids = state.elements.map((e) => e.instance);
  const [type, setType] = useState(Object.keys(types)[0] ?? "");
  const [id, setId] = useState("");
  const [from, setFrom] = useState("");
  const [fromPort, setFromPort] = useState("outlet");
  const [side, setSide] = useState("");
  const [to, setTo] = useState("");
  const [toPort, setToPort] = useState("inlet");
  const [joint, setJoint] = useState("butt");
  const [tlen, setTlen] = useState("30");
  const [step, setStep] = useState("0, 300, 0");
  const [count, setCount] = useState("2");
  const [cav, setCav] = useState(false);
  const addElement = () => {
    const newId = id || `${type}_${ids.filter((i) => i.startsWith(type)).length + 1}`;
    run(() => api.patch(state.id, [{ op: "add", path: "/elements/-", value: { id: newId, type, type_version: types[type].version, params: {} } }]));
  };
  const connect = () => {
    const cid = `c_${from}_${to}`.replace(/[^a-z0-9_]/g, "_");
    const f: any = { instance: from, port: fromPort };
    if (fromPort.startsWith("tap")) f.side = side || "side";
    const value: any = { id: cid, from: f, to: { instance: to, port: toPort }, joint: to === "part" ? "part" : joint };
    if (joint === "transition") value.transition = { length_mm: Number(tlen) };
    run(() => api.patch(state.id, [{ op: "add", path: "/connections/-", value }]));
  };
  const partPorts = Object.keys(state.part?.ports ?? {});
  return (
    <>
      <h3>Build</h3>
      <h4>Add element</h4>
      <div className="row">
        <select value={type} onChange={(e) => setType(e.target.value)}>
          {Object.values(types).map((t) => <option key={t.id} value={t.id}>{t.id}</option>)}
        </select>
        <input className="short" placeholder="id (auto)" value={id} onChange={(e) => setId(e.target.value)} />
        <button onClick={addElement}>Add</button>
      </div>
      <h4>Connect</h4>
      <div className="row">
        <select value={from} onChange={(e) => setFrom(e.target.value)}><option value="">from…</option>{ids.map((i) => <option key={i}>{i}</option>)}</select>
        <input className="short" value={fromPort} onChange={(e) => setFromPort(e.target.value)} title="outlet, or tap_1, tap_2… on a runner" />
        {fromPort.startsWith("tap") && (
          <select value={side} onChange={(e) => setSide(e.target.value)}>{["side", "top", "bottom", "left", "right"].map((s) => <option key={s}>{s}</option>)}</select>
        )}
      </div>
      <div className="row">
        <select value={to} onChange={(e) => { setTo(e.target.value); setToPort(e.target.value === "part" ? partPorts[0] ?? "" : "inlet"); }}>
          <option value="">to…</option>{["part", ...ids].map((i) => <option key={i}>{i}</option>)}
        </select>
        {to === "part" ? (
          <select value={toPort} onChange={(e) => setToPort(e.target.value)}>{partPorts.map((p) => <option key={p}>{p}</option>)}</select>
        ) : (
          <input className="short" value={toPort} onChange={(e) => setToPort(e.target.value)} />
        )}
        {to !== "part" && (
          <select value={joint} onChange={(e) => setJoint(e.target.value)}>{["butt", "transition", "branch", "blind"].map((j) => <option key={j}>{j}</option>)}</select>
        )}
        {joint === "transition" && to !== "part" && <input className="short" value={tlen} onChange={(e) => setTlen(e.target.value)} title="transition length, mm" />}
        <button disabled={!from || !to} onClick={connect}>Connect</button>
      </div>
      <h4>Selected: {selected ?? "–"}</h4>
      <div className="row">
        <button disabled={!selected} onClick={() => selected && run(() => api.remove(state.id, selected))}>Remove</button>
        <button disabled={!selected} onClick={() => selected && run(() => api.mirror(state.id, [selected], [0, 0, 0], [1, 0, 0]))}>Mirror in X = 0</button>
        <button disabled={!selected} onClick={() => selected && run(() => api.mirror(state.id, [selected], [0, 0, 0], [0, 1, 0]))}>Mirror in Y = 0</button>
      </div>
      <h4>Pattern everything</h4>
      <div className="row">
        step <input className="short" value={step} onChange={(e) => setStep(e.target.value)} /> × <input className="tiny" value={count} onChange={(e) => setCount(e.target.value)} />
        <label><input type="checkbox" checked={cav} onChange={(e) => setCav(e.target.checked)} /> cavities</label>
        <button disabled={!ids.length} onClick={() => run(() => api.pattern(state.id, { kind: "linear", instances: ids, step: step.split(",").map(Number) as Vec3, count: Number(count), cavities: cav }))}>Pattern</button>
      </div>
    </>
  );
}
