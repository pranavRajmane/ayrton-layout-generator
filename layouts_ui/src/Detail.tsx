// One layout in full: the large top view, its 3D model (built on demand by the CAD module), every
// parameter and check, the STEP download; and 2-4 layouts side by side.
import { useEffect, useState } from "react";
import { api, fmt, METRIC_LABEL, type Check } from "./api";
import { Built } from "./Model";

function Checks({ title, checks }: { title: string; checks: Check[] }) {
  return (
    <>
      <h4>{title}</h4>
      <table className="t"><tbody>{checks.map((c) => (
        <tr key={c.code}>
          <td className={c.passed ? "good" : "bad"}>{c.passed ? "pass" : "fail"}</td>
          <td className="mono small">{c.code}</td>
          <td className="num small">{c.value !== undefined ? JSON.stringify(c.value) : ""}{c.limit !== undefined ? ` / ${JSON.stringify(c.limit)}` : ""}</td>
          <td className="small muted">{c.message ?? ""}</td>
        </tr>))}</tbody></table>
    </>
  );
}

const SIZING_ROWS: [string, string, number][] = [
  ["castings_kg", "Castings (kg)", 3], ["poured_kg", "Poured (kg)", 3], ["yield_pct", "Yield (%)", 1],
  ["pour_time_s", "Pour time (s)", 2], ["head_mm", "Head H (mm)", 1], ["effective_head_mm", "Effective head (mm)", 1],
  ["choke_mm2", "Choke (mm²)", 1], ["sprue_area_mm2", "Sprue (mm²)", 1], ["runner_area_mm2", "Runner total (mm²)", 1],
  ["ingate_area_mm2", "Ingate total (mm²)", 1],
];

export function Detail({ job, lid, go }: { job: string; lid: string; go: (h: string) => void }) {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState("");
  const [building, setBuilding] = useState(false);
  const load = () => api.layout(job, lid).then(setD).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [job, lid]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!d) return err ? <div className="error">{err}</div> : <div className="muted">Loading…</div>;
  const lay = d.layout, rec = d.runners, a = d.assembly;
  const build = async () => {
    setBuilding(true); setErr("");
    try { await api.build(job, lid); await load(); } catch (e: any) { setErr(e.message); } finally { setBuilding(false); }
  };
  const star = async () => { await api.star(job, lid, !d.starred); load(); };
  // a box around the cavities for the 3D camera (from the layout: translations ± a part size)
  const T = lay.cavities.map((c: any) => c.translation);
  const span = 300;
  const lo = [Math.min(...T.map((t: number[]) => t[0])) - span, Math.min(...T.map((t: number[]) => t[1])) - span, -200];
  const hi = [Math.max(...T.map((t: number[]) => t[0])) + span, Math.max(...T.map((t: number[]) => t[1])) + span, 250];
  const s = rec?.sizing;
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row" style={{ alignItems: "baseline" }}>
        <a href={`#/job/${job}`}>← layouts</a>
        <h2 style={{ margin: 0 }}>{lid}</h2>
        <span className="muted">{lay.n} cavities · {lay.family} / {lay.template} · {lay.symmetry} · part turned {lay.rotation_deg}° · ingate {lay.ingate}</span>
        <button className={`star ${d.starred ? "on" : ""}`} onClick={star} title="Shortlist">★</button>
      </div>
      {err && <div className="error">{err}</div>}
      {d.rejected.length > 0 && <div className="note">Rejected: {d.rejected.flatMap((r: any) => r.reasons.map((x: any) => x.message)).join("; ")}</div>}
      <div className="row">
        <div className="panel grow" style={{ minWidth: 420 }}>
          <h4>Top view at the parting plane</h4>
          <img src={api.thumb(job, lid)} style={{ width: "100%", background: "#ece6d8", borderRadius: 4 }} alt={`Top view of ${lid}`} />
        </div>
        <div className="panel grow" style={{ minWidth: 420 }}>
          <h4>3D model</h4>
          {a ? (
            <div className="viewer">
              <Built cavities={api.model(job, lid, "cavities")} gating={api.model(job, lid, "gating")} lo={lo} hi={hi} />
              <div className="legend">Grey: cavities · orange: cup, sprue, well, runners, ingates</div>
            </div>
          ) : (
            <div className="viewer" style={{ display: "grid", placeItems: "center" }}>
              <button className="primary" disabled={!rec || building} onClick={build}>
                {building ? "Building in the CAD module…" : rec ? "Build the 3D model" : "Only routed layouts are built"}
              </button>
            </div>
          )}
          <div className="row" style={{ marginTop: 8, alignItems: "center" }}>
            {a ? <a href={api.step(job, lid)} download><button>Download STEP</button></a> : <button disabled>Download STEP</button>}
            {a && <button onClick={build} disabled={building}>Build again</button>}
            <button disabled title="Simulation comes later">Send to simulation</button>
          </div>
        </div>
      </div>
      <div className="row">
        <div className="panel grow" style={{ minWidth: 360 }}>
          <h4>Metrics (information, not a score)</h4>
          <table className="t"><tbody>{d.metrics ? Object.entries(METRIC_LABEL).map(([k, [l, u]]) => (
            <tr key={k}><td>{l}</td><td className="num">{fmt(d.metrics[k], 3)} {u}</td></tr>)) : <tr><td className="muted">Not routed</td></tr>}</tbody></table>
          {s && (
            <>
              <h4>Sizing</h4>
              <table className="t"><tbody>
                {SIZING_ROWS.map(([k, l, n]) => <tr key={k}><td>{l}</td><td className="num">{fmt(s[k], n)}</td></tr>)}
                <tr><td>Sprue top / bottom (mm)</td><td className="num">{fmt(s.sprue.top.dims.d, 1)} / {fmt(s.sprue.bottom.dims.d, 1)}</td></tr>
                {s.runner_levels.map((r: any, i: number) => (
                  <tr key={i}><td>Runner level {i + 1}: {r.branches} branch(es)</td>
                    <td className="num">{fmt(r.section.dims.top, 1)} / {fmt(r.section.dims.bottom, 1)} × {fmt(r.section.dims.height, 1)} mm</td></tr>))}
                <tr><td>Each ingate (mm)</td><td className="num">{fmt(s.ingate.dims.width, 2)} × {fmt(s.ingate.dims.height, 2)}</td></tr>
                <tr><td>Path to each ingate (mm)</td><td className="num">{s.path_lengths_mm.map((x: number) => fmt(x, 1)).join(", ")}</td></tr>
              </tbody></table>
              <div className="small muted">Pour-time formula {s.inputs.pour_time_formula} · density {s.inputs.density_kg_m3} · C_d {s.inputs.discharge_coefficient} · ratio {s.inputs.gating_ratio.join(" : ")}</div>
            </>
          )}
        </div>
        <div className="panel grow" style={{ minWidth: 420 }}>
          {rec && <Checks title="Phase 4 checks" checks={rec.validation} />}
          {a && <Checks title="Phase 5 checks (3D build)" checks={a.validation} />}
          <h4>Parameters</h4>
          <pre className="small mono" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{JSON.stringify({ arrangement: lay.arrangement, sprue: lay.sprue, cavities: lay.cavities }, null, 1)}</pre>
        </div>
      </div>
      <div><a href="#" onClick={(e) => { e.preventDefault(); go(`#/job/${job}`); }}>Back to the layouts</a></div>
    </div>
  );
}

export function Compare({ job, ids }: { job: string; ids: string[] }) {
  const [ds, setDs] = useState<any[]>([]);
  useEffect(() => { Promise.all(ids.map((i) => api.layout(job, i))).then(setDs); }, [job, ids.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row" style={{ alignItems: "baseline" }}><a href={`#/job/${job}`}>← layouts</a><h2 style={{ margin: 0 }}>Compare {ids.join(", ")}</h2></div>
      <div className="compare" style={{ gridTemplateColumns: `repeat(${ids.length}, minmax(0, 1fr))` }}>
        {ids.map((i) => <a key={i} href={`#/job/${job}/layout/${i}`}><img src={api.thumb(job, i)} alt={`Top view of ${i}`} /></a>)}
      </div>
      <div className="panel">
        <table className="t">
          <thead><tr><th></th>{ids.map((i) => <th key={i} className="num">{i}</th>)}</tr></thead>
          <tbody>
            {[["Cavities", (d: any) => d.layout.n], ["Runner", (d: any) => d.layout.template], ["Symmetry", (d: any) => d.layout.symmetry],
              ["Part turned", (d: any) => `${d.layout.rotation_deg}°`], ["Ingate", (d: any) => d.layout.ingate]].map(([l, fn]: any) => (
              <tr key={l}><td>{l}</td>{ds.map((d, k) => <td key={k} className="num">{fn(d)}</td>)}</tr>))}
            {Object.entries(METRIC_LABEL).map(([k, [l, u]]) => (
              <tr key={k}><td>{l} ({u})</td>{ds.map((d, j) => <td key={j} className="num">{d.metrics ? fmt(d.metrics[k], 2) : "–"}</td>)}</tr>))}
            <tr><td>Pour time (s)</td>{ds.map((d, j) => <td key={j} className="num">{d.runners ? fmt(d.runners.sizing.pour_time_s, 2) : "–"}</td>)}</tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
