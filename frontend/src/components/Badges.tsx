import type { Freshness } from "../types";

const FRESH: Record<Freshness, { icon: string; label: string; hint: string }> = {
  fresh: { icon: "●", label: "fresh", hint: "Collected within its SLA" },
  aging: { icon: "▲", label: "aging", hint: "Past its SLA, within twice the SLA" },
  stale: { icon: "■", label: "stale", hint: "More than twice the SLA since the last collection" },
  never: { icon: "○", label: "awaiting data", hint: "Nothing has been collected or entered yet" },
};

/** Freshness measures when we last collected, not the date the data describes. Icon and label, never colour alone. */
export function FreshnessBadge({ state }: { state: Freshness }) {
  const f = FRESH[state];
  return (
    <span className={`fresh-badge ${state}`} title={f.hint}>
      <span className="dot" aria-hidden="true">
        {f.icon}
      </span>
      {f.label}
    </span>
  );
}

export const ManualBadge = () => (
  <span className="badge manual" title="Entered by a person, not collected automatically">
    MANUAL
  </span>
);

export const HardcodedBadge = () => (
  <span className="badge hardcoded" title="Hand-entered in a cited ledger; it is not an automatically refreshed feed">
    HARDCODED
  </span>
);

export const ArithmeticBadge = () => (
  <span className="badge" title="Computed by us from the rows shown">
    Arithmetic, not a model
  </span>
);

export const ExtrapolationBadge = () => (
  <span className="badge" title="Projects beyond the last observed data point">
    Extrapolation
  </span>
);
