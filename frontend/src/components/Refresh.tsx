import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { sgt } from "../format";

interface Step {
  id: string;
  label: string;
  state: "waiting" | "running" | "done" | "failed" | "skipped" | "empty";
  percent: number;
}
interface Progress {
  stage: string;
  finished: number;
  total: number;
  sources: Step[];
}
interface Report {
  state: "ok" | "partial" | "unchanged" | "offline" | "failed";
  message: string;
  swapped: boolean;
  finished_at: string;
  failed?: Record<string, string[]>;
  problems?: string[];
}
interface Status {
  running: boolean;
  progress: Progress | null;
  last: Report | null;
  data: { packed_at?: string; observations?: string };
}

const WORD = { waiting: "waiting", done: "DONE", failed: "FAILED", skipped: "SKIPPED", empty: "NO ROWS" };
const word = (s: Step) => (s.state === "running" ? `${s.percent}%…` : WORD[s.state]);

/** The data is a snapshot in a SQLite file until this fetches every source again. It shows nothing when the API isn't serving from a file. */
export default function Refresh() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const wasRunning = useRef(false);

  const load = useCallback(() => api<Status>("/api/refresh").then(setStatus, () => {}), []);
  useEffect(() => {
    load();
  }, [load]);

  const running = status?.running ?? false;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(load, 1500);
    return () => clearInterval(timer);
  }, [running, load]);

  // A refresh that replaced the file finished while this page was open: reload so every chart shows the new data.
  useEffect(() => {
    if (wasRunning.current && status && !status.running && status.last?.swapped) window.location.reload();
    wasRunning.current = running;
  }, [status, running]);

  if (!status) return null;
  const press = () => {
    setError("");
    api("/api/refresh", { method: "POST" }).then(load, (e: Error) => {
      setError(e.message);
      load();
    });
  };
  const { last, progress } = status;
  const details = [...Object.entries(last?.failed ?? {}).map(([id, e]) => `${id}: ${e[0]}`), ...(last?.problems ?? [])];
  const collecting = progress?.stage === "Collecting sources";

  return (
    <section className="refresh" aria-label="Refresh the data">
      <div className="refresh-top">
        <div>
          <h2 className="section-title" style={{ margin: 0 }}>
            Refresh the data
          </h2>
          <p className="refresh-when">
            Showing the data saved at {status.data.packed_at ? sgt(status.data.packed_at) : "an unknown time"}
            {status.data.observations ? ` · ${Number(status.data.observations).toLocaleString()} observations` : ""}. Nothing is fetched until you press the button.
          </p>
        </div>
        <button onClick={press} disabled={running}>
          {running ? "Refreshing…" : "Refresh data"}
        </button>
      </div>

      {running ? (
        <div className="refresh-progress" aria-live="polite">
          <p>
            {progress
              ? collecting
                ? `Data collected (${progress.finished}/${progress.total})`
                : progress.stage
              : "Starting…"}
            . The charts keep showing the current data until it is done.
          </p>
          {progress && <progress value={progress.finished} max={progress.total} />}
          {progress && (
            <ul>
              {progress.sources.map((s) => (
                <li key={s.id} className={s.state} title={s.label}>
                  <span>collect {s.id}</span>
                  <span>{word(s)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        last && (
          <div className={`refresh-note ${last.state}`}>
            <p>
              {sgt(last.finished_at)}: {last.message}
            </p>
            {details.length > 0 && (
              <details>
                <summary>
                  {details.length} detail{details.length === 1 ? "" : "s"}
                </summary>
                <ul>
                  {details.map((d) => (
                    <li key={d}>{d}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )
      )}
      {error && <p className="refresh-note failed">{error}</p>}
    </section>
  );
}
