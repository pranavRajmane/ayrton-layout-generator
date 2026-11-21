// Phase 7's grid: one card per accepted layout (or, while Phase 3 runs, each layout as it is placed),
// filters, a sort on any metric, a shortlist and a compare selection. Only the rows in view are drawn.
import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmt, METRIC_LABEL, type Card, type Grid as GridT, type JobSummary } from "./api";

const CARD_W = 250, CARD_H = 346, GAP = 12;

function Facet({ label, values, sel, onChange, show = String }: {
  label: string; values: any[]; sel: string[]; onChange: (v: string[]) => void; show?: (v: any) => string;
}) {
  return (
    <label className="f">{label}
      <select multiple size={Math.min(Math.max(values.length, 2), 4)} value={sel}
        onChange={(e) => onChange([...e.target.selectedOptions].map((o) => o.value))} style={{ minWidth: 90 }}>
        {values.map((v) => <option key={String(v)} value={String(v)}>{show(v)}</option>)}
      </select>
    </label>
  );
}

function CardView({ c, job, sel, onSel, onStar, go }: {
  c: Card; job: string; sel: boolean; onSel: () => void; onStar: () => void; go: (h: string) => void;
}) {
  const m = c.metrics;
  const shape = c.arrangement?.per_side !== undefined ? `${c.arrangement.per_side} a side` :
    c.arrangement?.levels !== undefined ? `${c.arrangement.levels} levels, ${c.arrangement.feed}-fed` : "";
  return (
    <>
      <div className="head">
        <span className="id">{c.id}</span>
        <span>
          {c.state === "routed" && <input type="checkbox" checked={sel} onChange={onSel} title="Compare" />}
          <button className={`star ${c.starred ? "on" : ""}`} onClick={onStar} title="Shortlist" disabled={c.state === "pending"}>★</button>
        </span>
      </div>
      {c.state === "pending"
        ? <img className="thumb" loading="lazy" src={api.thumb(job, c.id)} alt={`Cavities of ${c.id}, runners next`} title="Placed; the runners are routed next" />
        : <img className="thumb" loading="lazy" src={api.thumb(job, c.id)} onClick={() => go(`#/job/${job}/layout/${c.id}`)} alt={`Top view of ${c.id}`} />}
      <div className="meta">{c.n} cavities · {c.family} / {c.template}{shape ? ` · ${shape}` : ""}</div>
      <div className="meta">{c.symmetry} · part turned {c.rotation_deg}° · ingate {c.ingate}</div>
      {m && (
        <dl>
          <dt>Poured</dt><dd>{fmt(m.poured_weight, 2)} kg</dd>
          <dt>Yield</dt><dd>{fmt(m.yield_pct, 1)} %</dd>
          <dt>Runner length</dt><dd>{fmt(m.runner_length, 0)} mm</dd>
          <dt>Min clearance</dt><dd>{fmt(m.min_clearance, 1)} mm</dd>
          <dt>Path-length error</dt><dd>{fmt(m.path_length_error_pct, 3)} %</dd>
        </dl>
      )}
    </>
  );
}

export default function Grid({ job, running, go }: { job: JobSummary; running: boolean; go: (h: string) => void }) {
  const [g, setG] = useState<GridT | null>(null);
  const [err, setErr] = useState("");
  const [f, setF] = useState<Record<string, string[]>>({ n: [], family: [], template: [], ingate: [], rotation: [] });
  const [starred, setStarred] = useState(false);
  const [sort, setSort] = useState("id");
  const [desc, setDesc] = useState(false);
  const [cmp, setCmp] = useState<string[]>([]);
  const [width, setWidth] = useState(1000);
  const [scroll, setScroll] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  const query = useMemo(() => ({
    n: f.n.join(","), family: f.family.join(","), template: f.template.join(","), ingate: f.ingate.join(","),
    rotation: f.rotation.join(","), starred, sort, desc, limit: 100000, state: running ? "all" : "routed",
  }), [f, starred, sort, desc, running]);
  useEffect(() => {
    let alive = true;
    const get = () => api.layouts(job.id, query).then((x) => alive && setG(x)).catch((e) => setErr(e.message));
    get();
    const t = running ? setInterval(get, 2000) : undefined;          // stream while Phase 3 places them
    return () => { alive = false; if (t) clearInterval(t); };
  }, [job.id, query, running]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const star = async (c: Card) => {
    await api.star(job.id, c.id, !c.starred);
    setG((x) => x && { ...x, items: x.items.map((y) => (y.id === c.id ? { ...y, starred: !c.starred } : y)) });
  };
  const items = g?.items ?? [];
  const cols = Math.max(1, Math.floor((width + GAP) / (CARD_W + GAP)));
  const rows = Math.ceil(items.length / cols);
  const viewH = box.current?.clientHeight ?? 800;
  const first = Math.max(0, Math.floor(scroll / (CARD_H + GAP)) - 1);
  const last = Math.min(rows, Math.ceil((scroll + viewH) / (CARD_H + GAP)) + 1);
  const shown = items.slice(first * cols, last * cols);
  const facets = g?.facets ?? {};

  return (
    <div>
      {err && <div className="error">{err}</div>}
      <div className="filters">
        <Facet label="Cavities" values={facets.n ?? []} sel={f.n} onChange={(v) => setF({ ...f, n: v })} />
        <Facet label="Family" values={facets.family ?? []} sel={f.family} onChange={(v) => setF({ ...f, family: v })} />
        <Facet label="Runner" values={facets.template ?? []} sel={f.template} onChange={(v) => setF({ ...f, template: v })} />
        <Facet label="Ingate" values={facets.ingate ?? []} sel={f.ingate} onChange={(v) => setF({ ...f, ingate: v })} />
        <Facet label="Part turned" values={facets.rotation_deg ?? []} sel={f.rotation} onChange={(v) => setF({ ...f, rotation: v })} show={(v) => `${v}°`} />
        <label className="f">Sort by
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="id">Layout id</option><option value="n">Cavities</option>
            {Object.entries(METRIC_LABEL).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={desc} onChange={(e) => setDesc(e.target.checked)} />High first</label>
        <label className="check"><input type="checkbox" checked={starred} onChange={(e) => setStarred(e.target.checked)} />Shortlist only</label>
        <button onClick={() => setF({ n: [], family: [], template: [], ingate: [], rotation: [] })}>Clear filters</button>
        <button className="primary" disabled={cmp.length < 2} onClick={() => go(`#/job/${job.id}/compare/${cmp.join(",")}`)}>
          Compare {cmp.length || ""}
        </button>
        <span className="muted small">{g ? `${g.total} shown of ${g.all}` : ""}{running ? " · streaming as Phase 3 places them" : ""}</span>
      </div>
      {g && g.all === 0 && !running && <div className="note">No layouts yet. Run the job (Run tab); rejected ones are on the Rejected tab.</div>}
      <div className="scroller" ref={box} onScroll={(e) => setScroll((e.target as HTMLDivElement).scrollTop)}>
        <div style={{ height: rows * (CARD_H + GAP), position: "relative" }}>
          {shown.map((c, k) => {
            const i = first * cols + k;
            const r = Math.floor(i / cols), col = i % cols;
            return (
              <div key={c.id} className={`card ${cmp.includes(c.id) ? "sel" : ""}`}
                style={{ left: col * (CARD_W + GAP), top: r * (CARD_H + GAP), width: CARD_W, height: CARD_H }}>
                <CardView c={c} job={job.id} sel={cmp.includes(c.id)} go={go} onStar={() => star(c)}
                  onSel={() => setCmp(cmp.includes(c.id) ? cmp.filter((x) => x !== c.id) : cmp.length < 4 ? [...cmp, c.id] : cmp)} />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
