// Phase 6 inputs: the part, alloy and recipe, flask, parting plane, sprue, optional limits, and the
// recipe's layout values (pre-filled; what the engineer changes becomes a per-job override).
import { useEffect, useMemo, useState } from "react";
import { api, fmt, type Flask, type Upload } from "./api";
import { PartingPreview } from "./Model";

// the recipe values a job may change here, by dotted path
const EDITABLE: [string, string][] = [
  ["layout.clearance.flask_wall_mm", "Cavity to flask wall (mm)"],
  ["layout.clearance.cavity_cavity_mm", "Cavity to cavity (mm)"],
  ["layout.clearance.cavity_sprue_mm", "Cavity to sprue (mm)"],
  ["layout.clearance.runner_cavity_mm", "Runner to cavity (mm)"],
  ["layout.clearance.cope_above_mm", "Sand above the cavity (mm)"],
  ["layout.clearance.drag_below_mm", "Sand below the cavity (mm)"],
  ["layout.ingate.thickness_mm", "Ingate thickness (mm)"],
  ["layout.ingate.aspect", "Ingate width / thickness"],
  ["layout.ingate.min_contact_length_mm", "Shortest ingate contact (mm)"],
  ["layout.ingate.merge_distance_mm", "Merge ingate candidates within (mm)"],
  ["layout.runner.extension_mm", "Runner run-on past the last ingate (mm)"],
  ["layout.draft_deg", "Draft (degrees)"],
  ["shrinkage_pct", "Pattern allowance (%)"],
];

function get(o: any, path: string) {
  return path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
}

function FlaskPick({ flask, at, onPick }: { flask: Flask; at: [number, number] | null; onPick: (p: [number, number]) => void }) {
  const W = 300, s = W / flask.length, H = flask.width * s;
  return (
    <svg width={W} height={H} className="flaskpick"
      onClick={(e) => {
        const r = (e.target as SVGElement).closest("svg")!.getBoundingClientRect();
        const x = (e.clientX - r.left) / s - flask.length / 2, y = flask.width / 2 - (e.clientY - r.top) / s;
        onPick([Math.round(x), Math.round(y)]);
      }}>
      <line x1={W / 2} x2={W / 2} y1={0} y2={H} stroke="#b7ae9a" strokeDasharray="4 4" />
      <line x1={0} x2={W} y1={H / 2} y2={H / 2} stroke="#b7ae9a" strokeDasharray="4 4" />
      {at && <circle cx={(at[0] + flask.length / 2) * s} cy={(flask.width / 2 - at[1]) * s} r={6} fill="#d9641e" stroke="#2a3337" />}
    </svg>
  );
}

export default function NewJob({ onCreated }: { onCreated: (id: string) => void }) {
  const [settings, setSettings] = useState<any>(null);
  const [up, setUp] = useState<Upload | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [name, setName] = useState("");
  const [process, setProcess] = useState("green_sand");
  const [alloy, setAlloy] = useState("grey_iron");
  const [recipeSet, setRecipeSet] = useState("presets");
  const [recipe, setRecipe] = useState<any>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [flask, setFlask] = useState<Flask>({ length: 800, width: 700, cope_height: 200, drag_height: 200 });
  const [parting, setParting] = useState("");
  const [sprueMode, setSprueMode] = useState<"free" | "fixed">("free");
  const [sprueAt, setSprueAt] = useState<[number, number] | null>(null);
  const [nMin, setNMin] = useState(""); const [nMax, setNMax] = useState("");
  const [families, setFamilies] = useState<string[]>(["row", "grid", "radial"]);
  const [rotations, setRotations] = useState<number[]>([0, 90, 180, 270]);
  const [keepouts, setKeepouts] = useState(false);

  useEffect(() => { api.settings().then(setSettings).catch((e) => setErr(e.message)); }, []);
  useEffect(() => {
    setRecipe(null);
    api.recipe(process, alloy, recipeSet).then((r) => { setRecipe(r); setEdits({}); }).catch((e) => setErr(e.message));
  }, [process, alloy, recipeSet]);

  const upload = async (f: File | undefined) => {
    if (!f) return;
    setErr(""); setBusy(`Reading ${f.name}…`);
    try {
      const u = await api.upload(f);
      setUp(u); setName((n) => n || f.name.replace(/\.(step|stp)$/i, ""));
      setParting(u.suggestions[0]?.id ?? "");
    } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  };
  const sug = up?.suggestions.find((s) => s.id === parting) ?? null;
  const density = recipe ? get(recipe.values, "density_kg_m3") : null;
  const missing: string[] = recipe ? Object.values(recipe.missing as Record<string, { path: string }[]>).flat().map((k) => k.path) : [];
  const overrides = useMemo(() => {
    const o: Record<string, number> = {};
    for (const [path] of EDITABLE) {
      if (edits[path] === undefined || edits[path] === "") continue;
      const v = Number(edits[path]);
      if (!Number.isNaN(v) && v !== get(recipe?.values, path)) o[path] = v;
    }
    return o;
  }, [edits, recipe]);

  const create = async () => {
    if (!up || !sug) return;
    setErr(""); setBusy("Checking the inputs…");
    try {
      const spec = {
        name, upload: up.upload, process, alloy, recipe_set: recipeSet, flask,
        parting: { move: sug.move, source: sug.id === "as_is" ? "user" : "auto" },
        sprue: { mode: sprueMode, position: sprueMode === "fixed" ? sprueAt : null },
        limits: { n_min: nMin ? Number(nMin) : null, n_max: nMax ? Number(nMax) : null,
                  families: families.length === 3 ? null : families, rotations_deg: rotations.length === 4 ? null : rotations,
                  riser_keepouts: keepouts },
        overrides,
      };
      const j = await api.create(spec);
      onCreated(j.id);
    } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  };
  const num = (k: keyof Flask) => (
    <label className="f">{({ length: "Length (x, mm)", width: "Width (y, mm)", cope_height: "Cope height (mm)", drag_height: "Drag depth (mm)" } as const)[k]}
      <input type="number" value={flask[k]} onChange={(e) => setFlask({ ...flask, [k]: Number(e.target.value) })} />
    </label>
  );

  return (
    <div className="col" style={{ gap: 12 }}>
      <h2>New layout job</h2>
      {err && <div className="error">{err}</div>}
      {busy && <div className="note">{busy}</div>}
      <div className="row">
        <div className="panel col grow" style={{ minWidth: 340 }}>
          <h4>Part</h4>
          <input type="file" accept=".step,.stp" onChange={(e) => upload(e.target.files?.[0])} />
          {up && (
            <div className="small">
              <div className={up.closed ? "good" : "bad"}>{up.closed ? "Closed solid" : "Not a closed solid"}: {up.closed_detail}</div>
              <div>Volume {fmt(up.volume_mm3 / 1000, 1)} cm³ · size {up.bbox[1].map((h, i) => fmt(h - up.bbox[0][i], 0)).join(" × ")} mm</div>
              <div>Weight {density ? `${fmt((up.volume_mm3 * 1e-9) * density, 2)} kg (at ${density} kg/m³)` : "– (the recipe has no density yet)"}</div>
            </div>
          )}
          <label className="f">Job name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <div className="row">
            <label className="f">Alloy
              <select value={alloy} onChange={(e) => setAlloy(e.target.value)}>
                {settings && Object.entries(settings.alloys).map(([k, v]: any) => <option key={k} value={k}>{v.label}</option>)}
              </select>
            </label>
            <label className="f">Process
              <select value={process} onChange={(e) => setProcess(e.target.value)}>
                {settings && Object.entries(settings.processes).map(([k, v]: any) => <option key={k} value={k}>{v.label}</option>)}
              </select>
            </label>
            <label className="f">Recipe
              <select value={recipeSet} onChange={(e) => setRecipeSet(e.target.value)}>
                {settings && Object.entries(settings.recipe_sets).map(([k, v]: any) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
          </div>
          {missing.length > 0 && (
            <div className="note small">This recipe ({recipe?.id}) has no value yet for {missing.length} item(s) the phases need:
              {" "}{missing.slice(0, 8).join(", ")}{missing.length > 8 ? " …" : ""}. Fill them in the preset, or pick another recipe.</div>
          )}
          <h4>Flask</h4>
          <div className="row">{num("length")}{num("width")}{num("cope_height")}{num("drag_height")}</div>
          <h4>Sprue</h4>
          <div className="row">
            <label className="check"><input type="radio" checked={sprueMode === "free"} onChange={() => setSprueMode("free")} />Free (flask centre)</label>
            <label className="check"><input type="radio" checked={sprueMode === "fixed"} onChange={() => setSprueMode("fixed")} />Fixed: click its place</label>
          </div>
          {sprueMode === "fixed" && (
            <div className="row">
              <FlaskPick flask={flask} at={sprueAt} onPick={setSprueAt} />
              <span className="small muted">{sprueAt ? `x ${sprueAt[0]}, y ${sprueAt[1]} mm` : "Click on the flask"}</span>
            </div>
          )}
          <h4>Limits (optional)</h4>
          <div className="row">
            <label className="f">Fewest cavities<input type="number" value={nMin} onChange={(e) => setNMin(e.target.value)} /></label>
            <label className="f">Most cavities<input type="number" value={nMax} onChange={(e) => setNMax(e.target.value)} /></label>
          </div>
          <div>
            {["row", "grid", "radial"].map((f) => (
              <label key={f} className="check"><input type="checkbox" checked={families.includes(f)}
                onChange={(e) => setFamilies(e.target.checked ? [...families, f] : families.filter((x) => x !== f))} />{f}</label>
            ))}
            <span className="muted small">· part turned</span>{" "}
            {[0, 90, 180, 270].map((r) => (
              <label key={r} className="check"><input type="checkbox" checked={rotations.includes(r)}
                onChange={(e) => setRotations(e.target.checked ? [...rotations, r].sort((a, b) => a - b) : rotations.filter((x) => x !== r))} />{r}°</label>
            ))}
          </div>
          <label className="check"><input type="checkbox" checked={keepouts} onChange={(e) => setKeepouts(e.target.checked)} />Riser keep-outs at hot spots (placeholder)</label>
        </div>
        <div className="panel col grow" style={{ minWidth: 380 }}>
          <h4>Parting plane</h4>
          {!up && <div className="muted small">Upload the part to see where it can part.</div>}
          {up?.suggestions.map((s) => (
            <label key={s.id} className="check" style={{ display: "flex" }}>
              <input type="radio" checked={parting === s.id} onChange={() => setParting(s.id)} />
              <span>{s.label} <span className="muted small">· {fmt(s.below, 0)} mm below, {fmt(s.above, 0)} mm above
                {s.undercut_mm2 !== undefined ? ` · undercut ${fmt(s.undercut_mm2, 0)} mm²` : ""}</span>
                {s.below <= 0.01 && <span className="bad small"> · nothing below the plane: no room for drag ingates</span>}</span>
            </label>
          ))}
          {up && sug && (
            <div className="viewer" style={{ height: 300 }}>
              <PartingPreview url={api.uploadGlb(up.upload)} move={sug.move} lo={sug.bbox[0]} hi={sug.bbox[1]} />
              <div className="legend">Blue: the parting plane (z = 0); cope above, drag below</div>
            </div>
          )}
          <h4>Recipe values for this job</h4>
          <table className="t">
            <tbody>
              {EDITABLE.map(([path, label]) => {
                const v = get(recipe?.values, path);
                return (
                  <tr key={path}>
                    <td>{label}</td>
                    <td className="num">
                      <input type="number" style={{ width: 90 }} placeholder={v == null ? "blank" : String(v)}
                        value={edits[path] ?? (v ?? "")} onChange={(e) => setEdits({ ...edits, [path]: e.target.value })} />
                    </td>
                    <td className="small muted">{overrides[path] !== undefined ? `recipe ${v ?? "blank"}` : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <div><button className="primary" disabled={!up || !sug || !!busy} onClick={create}>Create the job</button></div>
    </div>
  );
}
