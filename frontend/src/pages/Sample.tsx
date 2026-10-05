import { ArithmeticBadge, ExtrapolationBadge, FreshnessBadge, ManualBadge } from "../components/Badges";
import ChartCard from "../components/ChartCard";

export const meta = { title: "Sample", path: "/sample", order: 999 };

// Every card state the fixtures cover, in the order a reviewer should read them.
const CHARTS = [
  "sample.open_roles",
  "sample.headline_open_roles",
  "sample.sdk_share",
  "sample.status_incidents",
  "sample.consumer_spend",
  "sample.arr_ledger",
  "sample.mscience_panel",
];

export default function Sample() {
  return (
    <>
      <header className="page-head">
        <h1>Sample</h1>
        <p>
          Fixture data for layout review. None of these figures are real. Each card shows a different mix of source type, freshness
          and chart kind.
        </p>
      </header>
      <div className="grid">
        {CHARTS.map((id) => (
          <ChartCard key={id} id={id} />
        ))}
      </div>
      <h2 className="section-title">Badge and state reference</h2>
      <div className="gallery">
        <FreshnessBadge state="fresh" />
        <FreshnessBadge state="aging" />
        <FreshnessBadge state="stale" />
        <FreshnessBadge state="never" />
        <ManualBadge />
        <ArithmeticBadge />
        <ExtrapolationBadge />
      </div>
    </>
  );
}
