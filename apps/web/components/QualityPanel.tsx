"use client";

import { useState } from "react";
import type { MobilityAnalysis } from "@/lib/types";

const ISSUE_PREVIEW = 25;

function label(code: string) {
  return code.replaceAll("_", " ");
}

function Reasons({ title, counts, tone }: { title: string; counts: Record<string, number>; tone: "rejected" | "flagged" }) {
  const entries = Object.entries(counts);
  return (
    <div className="reason-list">
      <span className="label">{title}</span>
      {entries.length ? (
        entries.map(([code, count]) => (
          <div key={code}>
            <span className={`disposition ${tone}`}>{label(code)}</span>
            <strong>{count}</strong>
          </div>
        ))
      ) : (
        <p>None</p>
      )}
    </div>
  );
}

export default function QualityPanel({ analysis }: { analysis: MobilityAnalysis | null }) {
  const [showAll, setShowAll] = useState(false);
  if (!analysis) return null;
  const { provenance, quality, warnings, summary } = analysis;
  const issues = showAll ? quality.issues : quality.issues.slice(0, ISSUE_PREVIEW);
  const totalIssues = provenance.rejected_rows + provenance.flagged_rows;

  return (
    <section className="quality-panel">
      <div className="panel-heading">
        <div>
          <span>DATA QUALITY &amp; PROVENANCE</span>
          <h2>What the analysis accepted, flagged and changed</h2>
        </div>
        <p>
          {provenance.source_filename} · {provenance.data_status}
          {provenance.processing_seconds != null && ` · processed in ${provenance.processing_seconds.toFixed(2)} s`}
        </p>
      </div>

      <div className="report-strip">
        <div><span className="label">Input rows</span><strong>{provenance.input_rows}</strong></div>
        <div><span className="label">Accepted</span><strong>{provenance.accepted_rows}</strong></div>
        <div><span className="label">Rejected</span><strong>{provenance.rejected_rows}</strong></div>
        <div><span className="label">Flagged (kept)</span><strong>{provenance.flagged_rows}</strong></div>
        <div><span className="label">Excluded from geometry</span><strong>{quality.excluded_from_geometry_rows}</strong></div>
        <div><span className="label">Used for movement</span><strong>{summary.observations}</strong></div>
        <div><span className="label">Speeds derived</span><strong>{quality.speeds_derived}</strong></div>
        <div><span className="label">Speeds replaced</span><strong>{quality.speeds_replaced}</strong></div>
      </div>

      <div className="quality-grid">
        <div className="quality-block">
          <span className="label">Warnings ({warnings.length})</span>
          {warnings.length ? <ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : <p>No warnings for this dataset.</p>}
        </div>
        <div className="quality-block">
          <span className="label">Transformations applied ({provenance.transformations.length})</span>
          {provenance.transformations.length ? (
            <ul>{provenance.transformations.map((item) => <li key={item}>{item}</li>)}</ul>
          ) : (
            <p>No transformations were applied. Raw uploaded files are never modified.</p>
          )}
        </div>
        <div className="quality-block">
          <Reasons title="Rejected (not used)" counts={quality.rejected_by_reason} tone="rejected" />
          <Reasons title="Flagged (kept, inspect)" counts={quality.flagged_by_reason} tone="flagged" />
          <p className="threshold-note">
            Flags: speed above {quality.thresholds.max_plausible_speed_knots} kn, or a position only reachable faster than that over
            more than {quality.thresholds.min_position_jump_km} km. A flag means &ldquo;inspect&rdquo;, not &ldquo;proven wrong&rdquo;.
          </p>
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Row</th><th>Vessel</th><th>Time</th><th>Outcome</th><th>Reason</th><th>Original value</th><th>Explanation</th></tr>
          </thead>
          <tbody>
            {issues.length ? (
              issues.map((issue, index) => (
                <tr key={`${issue.row}-${issue.code}-${index}`} className="static-row">
                  <td><strong>{issue.row ?? "—"}</strong></td>
                  <td>{issue.vessel_id ?? "—"}</td>
                  <td>{issue.timestamp ? new Date(issue.timestamp).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}</td>
                  <td><span className={`disposition ${issue.disposition}`}>{issue.disposition}</span></td>
                  <td>{label(issue.code)}</td>
                  <td><code>{issue.value ?? "(empty)"}</code></td>
                  <td className="explanation">{issue.message}</td>
                </tr>
              ))
            ) : (
              <tr className="static-row"><td colSpan={7} className="empty">Every row passed validation and plausibility screening.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {(quality.issues.length > ISSUE_PREVIEW || quality.issues_truncated) && (
        <div className="table-footer">
          <span>
            Showing {issues.length} of {totalIssues} rejected or flagged rows
            {quality.issues_truncated && ` (the API lists the first ${quality.issues.length}; counts above are complete)`}.
          </span>
          {quality.issues.length > ISSUE_PREVIEW && (
            <button onClick={() => setShowAll((value) => !value)}>{showAll ? "Show fewer" : "Show all listed"}</button>
          )}
        </div>
      )}
    </section>
  );
}
