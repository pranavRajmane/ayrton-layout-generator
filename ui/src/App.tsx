import { useCallback, useEffect, useRef, useState } from "react";
import { api, type DesignState, type Issue, type ElementType, type FaceCheck, type ParamSchema, type Vec3 } from "./api";
import { BuildPanel, CalcPanel, PartPanel, ProcessPanel, type PickMode } from "./Panels";
import Viewer from "./Viewer";
import { useSetup } from "./Setup";

type Tab = "setup" | "element" | "part" | "process" | "calc" | "build";

const DEFAULT_PATH = "";
const isPartFile = (p: string) => /\.(step|stp|stl)$/i.test(p.trim());
const openAny = (p: string) => (isPartFile(p) ? api.newFromPart(p.trim()) : api.open(p.trim()));

function fmt(v: unknown): string {
  return typeof v === "number" ? (Math.abs(v) >= 1e4 ? v.toExponential(3) : +v.toFixed(3) + "") : String(v);
}

function ParamInput({ p, value, onCommit }: { p: ParamSchema; value: unknown; onCommit: (v: unknown) => void }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  if (p.choices) {
    return (
      <select value={String(value)} onChange={(e) => onCommit(e.target.value)}>
        {p.choices.map((c) => (
          <option key={String(c)}>{String(c)}</option>
        ))}
      </select>
    );
  }
  const commit = () => {
    const v = p.type === "float" || p.type === "int" ? Number(text) : text;
    if (v !== value && !(typeof v === "number" && Number.isNaN(v))) onCommit(v);
    else setText(String(value));
  };
  return (
    <input
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
      title={`${p.description}${p.min !== null ? ` · min ${p.min}` : ""}${p.max !== null ? ` · max ${p.max}` : ""}`}
    />
  );
}

// errors that only say the gating is not finished yet (the runner leads nowhere so far)
const UNFINISHED = new Set(["system.dangling_outlet", "system.path_end"]);

function unfinished(todo: Issue[]): string {
  const open = [...new Set(todo.filter((i) => i.rule === "system.dangling_outlet").flatMap((i) => i.instances))];
  if (!open.length) return todo.map((i) => i.message).join("; ");
  const names = open.map((x) => (x === "sprue" ? "the sprue" : x.replace(/^runner_?(\d*)$/, (_, n) => `runner ${n ? Number(n) + 1 : 1}`)));
  return `${names.join(", ")} ${open.length === 1 ? "ends" : "end"} in open air; the gating does not reach a part yet (ingates come next).`;
}

// "... has no density_kg_m3": the preset field a notice is about
const MISSING_FIELD = /has no (\S+)$/;

function IssueLine({ i, onSelect }: { i: Issue; onSelect: (inst: string) => void }) {
  return (
    <div className={`issue ${i.severity}`} onClick={() => i.instances[0] && onSelect(i.instances[0])}>
      <span className="sev">{i.severity}</span> {i.message}
      {i.instances.length > 0 && <span className="muted"> [{i.instances.join(", ")}]</span>}
      {i.limit !== null && i.limit !== undefined && <span className="muted"> (value {fmt(i.value)}, limit {fmt(i.limit)})</span>}
      <code className="rule">{i.rule}</code>
    </div>
  );
}

function Issues({ issues, showNotes, onToggleNotes, onSelect }: {
  issues: Issue[]; showNotes: boolean; onToggleNotes: () => void; onSelect: (inst: string) => void;
}) {
  const todo = issues.filter((i) => UNFINISHED.has(i.rule));
  const problems = issues.filter((i) => i.severity !== "info" && !UNFINISHED.has(i.rule));
  const notes = issues.filter((i) => i.severity === "info" && i.rule !== "preset.missing");
  const missing = [...new Set(issues.filter((i) => i.rule === "preset.missing").map((i) => i.message.match(MISSING_FIELD)?.[1] ?? i.message))];
  const errors = problems.filter((i) => i.severity === "error").length;
  const nNotes = notes.length + (missing.length ? 1 : 0);
  return (
    <>
      <div className="issue-head">
        <b>{errors ? `${errors} error(s)` : "No errors"}, {problems.length - errors} warning(s)</b>
        {nNotes > 0 && (
          <button className="link" onClick={onToggleNotes}>
            {showNotes ? "hide" : "show"} {nNotes} note(s)
          </button>
        )}
      </div>
      {problems.map((i, k) => (
        <IssueLine key={k} i={i} onSelect={onSelect} />
      ))}
      {todo.length > 0 && (
        <div className="issue todo">
          <span className="sev">to do</span> Not finished yet: {unfinished(todo)}
        </div>
      )}
      {showNotes && missing.length > 0 && (
        <div className="issue info">
          <span className="sev">info</span> The preset has no values yet, so weights, pour time and the choke are not computed. Missing: {missing.join(", ")}
          <code className="rule">preset.missing</code>
        </div>
      )}
      {showNotes && notes.map((i, k) => <IssueLine key={k} i={i} onSelect={onSelect} />)}
    </>
  );
}

export default function App() {
  const [path, setPath] = useState(() => new URLSearchParams(location.search).get("design") ?? DEFAULT_PATH);
  const [state, setState] = useState<DesignState | null>(null);
  const [types, setTypes] = useState<Record<string, ElementType>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("setup");
  const [mode, setMode] = useState<PickMode>("none");
  const [checks, setChecks] = useState<FaceCheck[] | null>(null);
  const [picked, setPicked] = useState<number[] | null>(null);
  const [presets, setPresets] = useState<string[]>([]);
  const [showNotes, setShowNotes] = useState(false);
  const opened = useRef<string | null>(null);

  const run = useCallback(async (f: () => Promise<DesignState | void>) => {
    setBusy(true);
    setError(null);
    try {
      const s = await f();
      if (s) {
        setState(s);
        if (s.path) opened.current = s.path;
      }
    } catch (e) {
      const msg = String((e as Error).message);
      if (msg.startsWith("no design session") && opened.current) {
        // the API restarted and forgot the session: start again from the file
        try {
          setState(await api.open(opened.current));
          setNote("The API restarted, so the design was reopened from its file; unsaved edits are lost.");
        } catch (e2) {
          setError(String((e2 as Error).message));
        }
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    api.elementTypes().then((ts) => setTypes(Object.fromEntries(ts.map((t) => [t.id, t])))).catch((e) => setError(String(e)));
    api.presets().then(setPresets).catch(() => undefined);
    if (path) run(() => openAny(path));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [plan, setPlan] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  // a part file from the computer: upload it, then start a new design around it
  const openFile = (f: File) =>
    run(async () => {
      const p = await api.upload(f);
      setPath(p);
      return api.newFromPart(p);
    });
  const setup = useSetup(state, run, setPlan);
  const el = state?.elements.find((e) => e.instance === selected) ?? null;
  const elIndex = state?.elements.findIndex((e) => e.instance === selected) ?? -1;
  const setParam = (name: string, v: unknown) => {
    if (!state || elIndex < 0) return;
    const saved = state.document.elements[elIndex].params;
    const op = name in saved ? "replace" : "add";
    run(() => api.patch(state.id, [{ op, path: `/elements/${elIndex}/params/${name}`, value: v }]));
  };
  const onPartClick = (pt: Vec3) => {
    if (!state) return;
    if (mode === "gate") {
      run(() => api.gatePort(state.id, pt));
      setMode("none");
    } else if (mode === "face_down") {
      run(async () => {
        const pk = await api.pick(state.id, pt);
        setMode("none");
        return api.orient(state.id, { op: "face_down", normal: pk.normal });
      });
    } else {
      run(async () => {
        const pk = await api.pick(state.id, pt);
        setPicked(pk.mesh);
        setNote(`face ${pk.face}: ${pk.kind}, ${pk.area_mm2.toFixed(0)} mm², normal ${pk.normal.map((v) => v.toFixed(2)).join(", ")}`);
      });
    }
  };
  const overlays = [
    ...(checks ? [{ meshes: checks.filter((f) => f.mesh).map((f) => f.mesh!), color: "#d93a3a" }] : []),
    ...(picked ? [{ meshes: [picked], color: "#2f7de1" }] : []),
  ];

  return (
    <div
      className={`app ${dragging ? "dropping" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const f = e.dataTransfer.files?.[0];
        if (f) openFile(f);
      }}
    >
      {dragging && <div className="dropzone">Drop a STEP or STL file to open it</div>}
      <header>
        <strong>Ayrton gating</strong>
        <input
          className="path"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="Path to a part (.step, .stl) or a saved design (.json)"
          onKeyDown={(e) => e.key === "Enter" && run(() => openAny(path))}
        />
        <button onClick={() => run(() => openAny(path))}>Open</button>
        <button className="primary" onClick={() => fileInput.current?.click()}>Upload part…</button>
        <input ref={fileInput} type="file" accept=".step,.stp,.stl" hidden
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) openFile(f); }} />
        <span className="sep" />
        <button disabled={!state?.can_undo || busy} onClick={() => state && run(() => api.undo(state.id))}>Undo</button>
        <button disabled={!state?.can_redo || busy} onClick={() => state && run(() => api.redo(state.id))}>Redo</button>
        <button disabled={!state || busy} onClick={() => state && run(async () => setNote("saved " + (await api.save(state.id)).saved))}>Save</button>
        <button
          disabled={!state || busy}
          onClick={() => {
            const out = prompt("Export folder (Ayrton project + regions)", "out/export");
            if (state && out) run(async () => setNote(`exported: ${(await api.exportTo(state.id, out)).metal.volume_mm3.toFixed(0)} mm³ of metal`));
          }}
        >
          Export to Ayrton
        </button>
        <span className="grow" />
        {state && <span className="muted">regenerated in {state.timings_ms.regen_and_mesh?.toFixed(0)} ms</span>}
        {busy && <span className="busy">working…</span>}
      </header>
      {error && <div className="error">{error}</div>}
      {note && <div className="note" onClick={() => setNote(null)}>{note}</div>}
      <aside className="tree">
        <h3>{state?.document.name ?? "No design"}</h3>
        {state?.part && (
          <div className={`item ${tab === "part" ? "sel" : ""}`} onClick={() => setTab("part")}>
            part <span className="muted">{(state.part.volume_mm3 / 1000).toFixed(1)} cm³</span>
          </div>
        )}
        {state?.document.part?.cores?.map((c: any) => (
          <div key={c.id} className="item static">core.{c.id}</div>
        ))}
        <h4>Elements</h4>
        {state?.elements.map((e) => {
          const bad = state.issues.some((i) => i.severity === "error" && i.instances.includes(e.instance));
          return (
            <div key={e.instance} className={`item ${e.instance === selected ? "sel" : ""}`} onClick={() => { setSelected(e.instance); setTab("element"); }}>
              <span className={`dot ${bad ? "red" : e.built ? "green" : "grey"}`} />
              {e.name ?? e.instance} <span className="muted">{e.type}</span>
            </div>
          );
        })}
        <h4>Connections</h4>
        {state?.document.connections.map((c: any) => (
          <div key={c.id} className="item static small">
            {c.from.instance}.{c.from.port} → {c.to.instance}.{c.to.port} <span className="muted">{c.joint}</span>
          </div>
        ))}
      </aside>
      <main>
        {mode !== "none" && <div className="mode">{mode === "gate" ? "Click the part where an ingate should land" : "Click the face that should face down"}</div>}
        {state && (
          <Viewer state={state} selected={selected} onSelect={(i) => { setSelected(i); setPicked(null); if (i) setTab("element"); }}
            onPartClick={onPartClick} overlays={overlays} setup={tab === "setup" ? setup.view : { mode: "none" }} plan={plan} onPlan={setPlan} />
        )}
      </main>
      <aside className="params">
        <nav className="tabs">
          {(["setup", "element", "part", "process", "calc", "build"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t === "setup" ? "set-up" : t}</button>
          ))}
        </nav>
        {tab === "setup" && (setup.panel ?? <div className="muted">Open a part file (.step or .stl) to start.</div>)}
        {state && tab === "part" && <PartPanel state={state} run={run} mode={mode} setMode={setMode} checks={checks} setChecks={setChecks} />}
        {state && tab === "process" && <ProcessPanel state={state} run={run} presets={presets} />}
        {state && tab === "calc" && <CalcPanel state={state} run={run} />}
        {state && tab === "build" && <BuildPanel state={state} run={run} types={types} selected={selected} />}
        {tab !== "element" ? null : el ? (
          <>
            <h3>{el.instance}</h3>
            <div className="muted">
              {el.type} {types[el.type]?.version}
            </div>
            <table>
              <tbody>
                {(types[el.type]?.params ?? []).map((p) => (
                  <tr key={p.name}>
                    <td>{p.label}</td>
                    <td>
                      <ParamInput key={state?.id} p={p} value={el.params[p.name] ?? p.default} onCommit={(v) => setParam(p.name, v)} />
                    </td>
                    <td className="muted">{p.unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {Object.keys(el.derived).length > 0 && (
              <>
                <h4>Derived</h4>
                <table>
                  <tbody>
                    {Object.entries(el.derived).map(([k, v]) => (
                      <tr key={k}>
                        <td>{k}</td>
                        <td>{fmt(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            <h4>Ports</h4>
            {el.ports.map((p) => (
              <div key={p.id} className="small">
                <b>{p.id}</b> {p.kind} · {p.section ? `${p.section.shape} ${p.section.area.toFixed(1)} mm²` : "–"} · {p.section_policy}
              </div>
            ))}
          </>
        ) : (
          <div className="muted">Select an element</div>
        )}
      </aside>
      <footer>
        <Issues issues={state?.issues ?? []} showNotes={showNotes} onToggleNotes={() => setShowNotes((v) => !v)} onSelect={setSelected} />
      </footer>
    </div>
  );
}
