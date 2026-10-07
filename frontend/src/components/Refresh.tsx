import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { utc } from "../format";

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
  started_at: string | null;
  last: Report | null;
  data: { packed_at?: string };
}

/** The data is a snapshot in a SQLite file until this button fetches every source again. It shows nothing when the API isn't serving from a file. */
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
    const timer = setInterval(load, 4000);
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
  const last = status.last;
  const details = [...Object.entries(last?.failed ?? {}).map(([id, e]) => `${id}: ${e[0]}`), ...(last?.problems ?? [])];

  return (
    <div className="refresh">
      <div className="refresh-when">Data from {status.data.packed_at ? utc(status.data.packed_at) : "an unknown time"}</div>
      <button onClick={press} disabled={running}>
        {running ? "Refreshing…" : "Refresh data"}
      </button>
      {running ? (
        <p className="refresh-note">
          Fetching every source again. This can take several minutes; the charts keep showing the current data until it is done.
        </p>
      ) : (
        last && (
          <div className={`refresh-note ${last.state}`}>
            <p>
              {utc(last.finished_at)}: {last.message}
            </p>
            {details.length > 0 && (
              <details>
                <summary>{details.length} detail{details.length === 1 ? "" : "s"}</summary>
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
    </div>
  );
}
