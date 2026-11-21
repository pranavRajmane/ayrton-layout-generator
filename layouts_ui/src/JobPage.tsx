// A job: its run (progress per phase), the part analysis, the layout grid and the rejected layouts.
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmt, PHASES, PHASE_LABEL, type JobSummary, type Progress } from "./api";
import { Heatmap, type Marker } from "./Model";
import Grid from "./Grid";

type Tab = "run" | "analysis" | "layouts" | "rejected";

function Pill({ s }: { s: string }) {
  return <span className={`pill ${s}`}>{s}</span>;
}

function RunTab({ job, prog, onRun, onNarrow }: {
  job: JobSummary; prog: Progress | null; onRun: (start?: string) => void; onNarrow: (limits: any) => void;
}) {
  const p = prog ?? job.progress;
  const status = prog?.status ?? job.status;
  const phases = prog?.phases ?? job.phases;
  const lim = job.inputs.limits;
  const [nMax, setNMax] = useState<string>(lim.n_max ?? "");
  const [thr, setThr] = useState<string>(lim.count_threshold ?? "");
  const anyToDo = PHASES.some((ph) => status[ph] !== "done");
  return (
    <div className="row">
      <div className="panel col grow" style={{ minWidth: 420 }}>
        <div className="row" style={{ alignItems: "center" }}>
          <button className="primary" disabled={p.running || !anyToDo} onClick={() => onRun()}>
            {p.running ? "Running…" : anyToDo ? "Run the phases not done" : "Every phase is done"}
          </button>
          <button disabled={p.running} onClick={() => onRun("layouts")}>Re-run from Phase 3</button>
          <span className="muted">{p.running ? p.message : ""}</span>
        </div>
        {p.error && !p.count_exceeded && <div className="error">{p.error}</div>}
        {p.count_exceeded && (
          <div className="note">
            <b>{p.count_exceeded.count}</b> layout combinations counted, over the threshold of {p.count_exceeded.threshold}
            {" "}({Object.entries(p.count_exceeded.by).map(([k, v]) => `${k} ${v}`).join(", ")}). Narrow the search, or raise the threshold:
            <div className="row" style={{ marginTop: 6, alignItems: "flex-end" }}>
              <label className="f">Most cavities<input type="number" value={nMax} onChange={(e) => setNMax(e.target.value)} /></label>
              <label className="f">Threshold<input type="number" value={thr} onChange={(e) => setThr(e.target.value)} /></label>
              <button onClick={() => onNarrow({ n_max: nMax ? Number(nMax) : null, count_threshold: thr ? Number(thr) : null })}>Save and run again</button>
            </div>
          </div>
        )}
        <table className="t">
          <thead><tr><th>Phase</th><th>State</th><th className="num">Time</th><th>Result</th></tr></thead>
          <tbody>
            {PHASES.map((ph) => {
              const st = p.running && p.phase === ph ? "running" : status[ph];
              const c = phases[ph]?.counts ?? {};
              return (
                <tr key={ph}>
                  <td>{PHASE_LABEL[ph]}</td>
                  <td><Pill s={st} /></td>
                  <td className="num">{phases[ph]?.runtime_s != null ? `${fmt(phases[ph].runtime_s!, 1)} s` : ""}</td>
                  <td className="small">
                    {Object.entries(c).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}
                    {ph === "layouts" && p.running && p.phase === "layouts" && (
                      <span> {p.counted ?? 0} counted · {p.placed ?? 0} placed · {p.rejected ?? 0} rejected</span>
                    )}
                    {(phases[ph]?.issues ?? []).map((i, k) => <div key={k} className="muted">{i}</div>)}
                    {phases[ph]?.error && <div className="bad">{phases[ph].error}</div>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="small muted">
          Counts: {Object.entries(prog?.counts ?? job.counts).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}
        </div>
      </div>
      <div className="panel col" style={{ minWidth: 300, maxWidth: 420 }}>
        <h4>Inputs</h4>
        <table className="t small"><tbody>
          <tr><td>Part</td><td>{job.inputs.part.source}</td></tr>
          <tr><td>Alloy / process</td><td>{job.inputs.alloy} · {job.inputs.process}</td></tr>
          <tr><td>Recipe</td><td>{job.recipe.id} ({job.recipe_set})</td></tr>
          <tr><td>Flask</td><td>{["length", "width", "cope_height", "drag_height"].map((k) => job.inputs.flask[k]).join(" × ")} mm</td></tr>
          <tr><td>Sprue</td><td>{job.inputs.sprue.mode}{job.inputs.sprue.position ? ` at ${job.inputs.sprue.position.join(", ")}` : ""}</td></tr>
          <tr><td>Cavities</td><td>{lim.n_min ?? 1} to {lim.n_max ?? "as many as fit"}</td></tr>
          <tr><td>Families</td><td>{(lim.families ?? ["all"]).join(", ")}</td></tr>
          <tr><td>Part turned</td><td>{(lim.rotations_deg ?? [0, 90, 180, 270]).join(", ")}°</td></tr>
          <tr><td>Ingates off</td><td>{lim.disabled_ingates.join(", ") || "none"}</td></tr>
          <tr><td>Overrides</td><td>{Object.entries(job.inputs.overrides).map(([k, v]) => `${k} ${v}`).join(", ") || "none"}</td></tr>
        </tbody></table>
      </div>
    </div>
  );
}

function AnalysisTab({ job, onChanged }: { job: JobSummary; onChanged: () => void }) {
  const [a, setA] = useState<any>(null);
  const [mesh, setMesh] = useState<any>(null);
  const [err, setErr] = useState("");
  const [tagMode, setTagMode] = useState(false);
  const [disabled, setDisabled] = useState<string[]>(job.inputs.limits.disabled_ingates);
  const [tags, setTags] = useState<{ face: number; tag: string }[]>(job.inputs.face_tags);
  useEffect(() => {
    if (job.status.modulus !== "done") return;
    api.analysis(job.id).then(setA).catch((e) => setErr(e.message));
    api.partMesh(job.id).then(setMesh).catch((e) => setErr(e.message));
  }, [job.id, job.status.modulus]);
  const tagged = useMemo(() => new Set(tags.map((t) => t.face)), [tags]);
  const range = useMemo<[number, number]>(() => {
    if (!mesh) return [0, 1];
    const v = [...mesh.modulus].filter((x: number) => x > 0).sort((p: number, q: number) => p - q);
    return [v[Math.floor(v.length * 0.02)] ?? 0, v[Math.floor(v.length * 0.98)] ?? 1];
  }, [mesh]);
  if (job.status.modulus !== "done") return <div className="note">Run Phase 1 (modulus analysis) to see the part analysis.</div>;
  if (err) return <div className="error">{err}</div>;
  if (!a || !mesh) return <div className="muted">Loading the analysis…</div>;
  const cands: any[] = a.ingates?.candidates ?? [];
  const markers: Marker[] = cands.map((c) => ({ id: c.id, at: c.location, normal: c.normal, disabled: disabled.includes(c.id),
    flagged: c.flags.impinges_core || c.flags.impinges_thin_wall }));
  const dirty = disabled.join() !== job.inputs.limits.disabled_ingates.join() || JSON.stringify(tags) !== JSON.stringify(job.inputs.face_tags);
  const save = async () => {
    try {
      await api.setInputs(job.id, { limits: { disabled_ingates: disabled }, face_tags: tags });
      onChanged();
    } catch (e: any) { setErr(e.message); }
  };
  const zones = [...a.modulus.zones].sort((p: any, q: any) => q.modulus - p.modulus);
  return (
    <div className="row">
      <div className="col grow" style={{ minWidth: 480 }}>
        <div className="viewer" style={{ height: 560 }}>
          <Heatmap mesh={mesh} hotSpots={a.modulus.hot_spots} markers={markers} tagged={tagged} range={range}
            onMarker={(id) => setDisabled(disabled.includes(id) ? disabled.filter((x) => x !== id) : [...disabled, id])}
            onFace={tagMode ? (f) => setTags(tagged.has(f) ? tags.filter((t) => t.face !== f) : [...tags, { face: f, tag: "machined" }]) : null} />
          <div className="legend">
            <div className="row" style={{ alignItems: "center", gap: 6 }}><span>{fmt(range[0], 1)}</span><span className="bar" /><span>{fmt(range[1], 1)} mm local modulus</span></div>
            Red spheres: hot spots · cones: ingate candidates (green; yellow = impinges a core or thin wall; grey = off; click to switch)
            {tagMode && " · purple: tagged faces (click to tag / untag)"}
          </div>
        </div>
        <div className="row" style={{ alignItems: "center" }}>
          <button className={tagMode ? "on" : ""} onClick={() => setTagMode(!tagMode)}>{tagMode ? "Tagging faces: click the part" : "Tag faces (machined, no ingates)"}</button>
          <button className="primary" disabled={!dirty} onClick={save}>Save; later phases go out of date</button>
          <span className="small muted">{disabled.length} candidate(s) off · {tags.length} face(s) tagged</span>
        </div>
        {job.status.modulus === "done" && job.phases.modulus.issues?.length ? (
          <div className="small muted">{job.phases.modulus.issues.join(" · ")}</div>) : null}
      </div>
      <div className="col" style={{ width: 400 }}>
        <div className="panel">
          <h4>Feeding zones ({zones.length}), heaviest first</h4>
          <table className="t"><thead><tr><th>Zone</th><th className="num">Modulus mm</th><th className="num">Volume cm³</th><th className="num">Cooling cm²</th></tr></thead>
            <tbody>{zones.slice(0, 14).map((z: any) => (
              <tr key={z.id}><td className="mono">{z.id}</td><td className="num">{fmt(z.modulus, 2)}</td><td className="num">{fmt(z.volume / 1000, 1)}</td><td className="num">{fmt(z.cooling_area / 100, 1)}</td></tr>
            ))}</tbody></table>
          {zones.length > 14 && <div className="small muted">…and {zones.length - 14} more</div>}
        </div>
        <div className="panel">
          <h4>Ingate candidates ({cands.length}; {a.ingates?.discarded?.length ?? 0} stretches discarded)</h4>
          <table className="t"><thead><tr><th></th><th>Id</th><th>Zone</th><th className="num">M zone</th><th className="num">Contact</th><th>Flags</th></tr></thead>
            <tbody>{cands.map((c: any) => (
              <tr key={c.id} className={disabled.includes(c.id) ? "muted" : ""}>
                <td><input type="checkbox" checked={!disabled.includes(c.id)}
                  onChange={() => setDisabled(disabled.includes(c.id) ? disabled.filter((x) => x !== c.id) : [...disabled, c.id])} /></td>
                <td className="mono">{c.id}</td><td className="mono">{c.target_zone}</td><td className="num">{fmt(c.zone_modulus, 2)}</td>
                <td className="num">{fmt(c.contact_length, 0)}</td>
                <td className="small">{[c.flags.impinges_core && "core", c.flags.impinges_thin_wall && "thin wall", ...c.rule_tags].filter(Boolean).join(", ")}</td>
              </tr>
            ))}</tbody></table>
        </div>
      </div>
    </div>
  );
}

function RejectedTab({ job, go }: { job: JobSummary; go: (h: string) => void }) {
  const [r, setR] = useState<any>(null);
  const [code, setCode] = useState<string | null>(null);
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => { api.rejected(job.id).then(setR); }, [job.id, job.counts.rejected]);
  useEffect(() => { if (code) api.rejected(job.id, code).then((x) => setItems(x.items)); }, [job.id, code]);
  if (!r) return <div className="muted">Loading…</div>;
  const c = r.counts;
  return (
    <div className="col">
      <div className="small muted">Enumerated {c.enumerated} = accepted {c.accepted ?? "–"} + rejected {c.rejected}
        {c.accepted !== undefined && c.enumerated !== c.accepted + c.rejected ? <span className="bad"> (does not add up)</span> : null}</div>
      <div className="row">
        <div className="panel" style={{ minWidth: 340 }}>
          <h4>By reason (a layout counts once under each of its reasons)</h4>
          <table className="t"><tbody>{r.groups.map((g: any) => (
            <tr key={g.code} style={{ cursor: "pointer" }} className={code === g.code ? "good" : ""} onClick={() => setCode(g.code)}>
              <td className="mono">{g.code}</td><td className="small muted">Phase {g.phase === "layouts" ? 3 : 4}</td><td className="num">{g.count}</td>
            </tr>))}</tbody></table>
          {r.groups.length === 0 && <div className="muted">Nothing rejected.</div>}
        </div>
        {code && (
          <div className="panel grow">
            <h4>{code}: {items.length} layout(s)</h4>
            <div className="row">{items.map((it) => (
              <div key={it.id} className="panel" style={{ width: 250, padding: 8 }}>
                <div className="mono">{it.id} <span className="muted">· {it.n} × {it.template} · {it.rotation_deg}° · {it.ingate}</span></div>
                {it.drawable && <img src={api.thumb(job.id, it.id)} style={{ width: "100%", background: "#ece6d8", cursor: "pointer" }}
                  onClick={() => go(`#/job/${job.id}/layout/${it.id}`)} />}
                {it.reasons.map((x: any, k: number) => <div key={k} className="small">{x.message}{x.value !== undefined ? ` (${JSON.stringify(x.value)}${x.limit !== undefined ? ` / ${JSON.stringify(x.limit)}` : ""})` : ""}</div>)}
              </div>))}</div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function JobPage({ id, go }: { id: string; go: (h: string) => void }) {
  const [job, setJob] = useState<JobSummary | null>(null);
  const [prog, setProg] = useState<Progress | null>(null);
  const [tab, setTab] = useState<Tab>("run");
  const [err, setErr] = useState("");
  const load = useCallback(() => api.job(id).then((j) => { setJob(j); setProg(j.progress.running ? null : null); }).catch((e) => setErr(e.message)), [id]);
  useEffect(() => { load(); }, [load]);
  // while running, poll the progress; reload the job when it stops
  useEffect(() => {
    if (!job) return;
    let alive = true;
    const tick = async () => {
      try {
        const p = await api.progress(id);
        if (!alive) return;
        setProg(p);
        if (p.running) setTimeout(tick, 1000);
        else load();
      } catch (e: any) { setErr(e.message); }
    };
    if (job.progress.running) tick();
    return () => { alive = false; };
  }, [job?.progress.running]); // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (start?: string) => {
    try { await api.run(id, start); await load(); } catch (e: any) { setErr(e.message); }
  };
  const narrow = async (limits: any) => {
    try { await api.setInputs(id, { limits }); await api.run(id); await load(); } catch (e: any) { setErr(e.message); }
  };
  if (!job) return err ? <div className="error">{err}</div> : <div className="muted">Loading…</div>;
  const running = prog?.running ?? job.progress.running;
  return (
    <div>
      <div className="row" style={{ alignItems: "baseline" }}>
        <h2 style={{ margin: 0 }}>{job.name}</h2>
        <span className="muted mono small">{job.id}</span>
        {running && <span className="pill running">running · {prog?.message}</span>}
      </div>
      {err && <div className="error">{err}</div>}
      <div className="tabs">
        {(["run", "analysis", "layouts", "rejected"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {{ run: "Run", analysis: "Part analysis", layouts: `Layouts${job.counts.accepted !== undefined ? ` (${job.counts.accepted})` : ""}`, rejected: `Rejected (${job.counts.rejected})` }[t]}
          </button>
        ))}
      </div>
      {tab === "run" && <RunTab job={job} prog={prog} onRun={run} onNarrow={narrow} />}
      {tab === "analysis" && <AnalysisTab job={job} onChanged={load} />}
      {tab === "layouts" && <Grid job={job} running={!!running} go={go} />}
      {tab === "rejected" && <RejectedTab job={job} go={go} />}
    </div>
  );
}
