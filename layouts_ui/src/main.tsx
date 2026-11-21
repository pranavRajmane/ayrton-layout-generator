import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { api, type JobSummary } from "./api";
import NewJob from "./NewJob";
import JobPage from "./JobPage";
import { Compare, Detail } from "./Detail";
import "./style.css";

// #/                      jobs, and a new one
// #/new                   the input form
// #/job/<id>              a job: run, part analysis, layouts, rejected
// #/job/<id>/layout/<lid> one layout
// #/job/<id>/compare/<a,b,...>
function useHash(): [string, (h: string) => void] {
  const [h, setH] = useState(location.hash || "#/");
  useEffect(() => {
    const on = () => { setH(location.hash || "#/"); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [h, (x) => { location.hash = x; }];
}

function Jobs({ go }: { go: (h: string) => void }) {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.jobs().then(setJobs).catch((e) => setErr(e.message)); }, []);
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row" style={{ alignItems: "center" }}>
        <h2 style={{ margin: 0 }}>Layout jobs</h2>
        <button className="primary" onClick={() => go("#/new")}>New job</button>
      </div>
      {err && <div className="error">{err}</div>}
      {jobs && jobs.length === 0 && <div className="note">No jobs yet: start one with a STEP part.</div>}
      {jobs && jobs.length > 0 && (
        <div className="panel">
          <table className="t">
            <thead><tr><th>Job</th><th>Part</th><th>Alloy</th><th>Flask</th><th>Phases</th><th className="num">Layouts</th><th className="num">Shortlisted</th></tr></thead>
            <tbody>{jobs.map((j) => (
              <tr key={j.id} style={{ cursor: "pointer" }} onClick={() => go(`#/job/${j.id}`)}>
                <td><a href={`#/job/${j.id}`}>{j.name}</a></td>
                <td className="small">{j.inputs.part.source}</td>
                <td className="small">{j.inputs.alloy}</td>
                <td className="small">{j.inputs.flask.length} × {j.inputs.flask.width}</td>
                <td className="small">{Object.values(j.status).filter((s) => s === "done").length} / 5 done{j.progress.running ? " · running" : ""}</td>
                <td className="num">{j.counts.accepted ?? "–"}</td>
                <td className="num">{j.starred.length}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function App() {
  const [hash, go] = useHash();
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  let body;
  if (parts[0] === "new") body = <NewJob onCreated={(id) => go(`#/job/${id}`)} />;
  else if (parts[0] === "job" && parts[2] === "layout") body = <Detail job={parts[1]} lid={parts[3]} go={go} />;
  else if (parts[0] === "job" && parts[2] === "compare") body = <Compare job={parts[1]} ids={(parts[3] ?? "").split(",")} />;
  else if (parts[0] === "job") body = <JobPage key={parts[1]} id={parts[1]} go={go} />;
  else body = <Jobs go={go} />;
  return (
    <>
      <header className="top">
        <h1><a href="#/">Ayrton · gating layouts</a></h1>
        <span className="crumbs small">grey and SG iron · every valid multi-cavity layout, for the engineer to pick from</span>
      </header>
      <main>{body}</main>
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
