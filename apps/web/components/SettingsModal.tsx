"use client";

import { FormEvent, useState } from "react";
import { DEFAULT_SETTINGS, SETTING_FIELDS } from "@/lib/api";
import type { AnalysisSettings } from "@/lib/types";

type Props = {
  settings: AnalysisSettings;
  datasetName: string | null;
  onApply: (settings: AnalysisSettings) => void;
  onClose: () => void;
};

export default function SettingsModal({ settings, datasetName, onApply, onClose }: Props) {
  const [draft, setDraft] = useState<Record<keyof AnalysisSettings, string>>(
    () => Object.fromEntries(Object.entries(settings).map(([key, value]) => [key, String(value)])) as Record<keyof AnalysisSettings, string>,
  );

  const problems = SETTING_FIELDS.flatMap((field) => {
    const value = Number(draft[field.key]);
    if (draft[field.key].trim() === "" || !Number.isFinite(value)) return [`${field.label} must be a number.`];
    if (value < field.min || value > field.max) return [`${field.label} must be between ${field.min} and ${field.max} ${field.unit}.`];
    if ((field.key === "trip_gap_minutes" || field.key === "landing_min_visits") && !Number.isInteger(value)) return [`${field.label} must be a whole number.`];
    return [];
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (problems.length) return;
    onApply(Object.fromEntries(Object.entries(draft).map(([key, value]) => [key, Number(value)])) as AnalysisSettings);
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <form className="modal settings-modal" onMouseDown={(event) => event.stopPropagation()} onSubmit={submit}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">×</button>
        <span className="eyebrow">ANALYSIS SETTINGS</span>
        <h2>Method parameters</h2>
        <p>These thresholds are transparent baseline rules, not trained models. Applying them re-analyses {datasetName ?? "the current dataset"}.</p>
        <div className="settings-grid">
          {SETTING_FIELDS.map((field) => (
            <label key={field.key}>
              <span>{field.label} <small>({field.unit})</small></span>
              <input
                type="number"
                inputMode="decimal"
                min={field.min}
                max={field.max}
                step={field.step}
                value={draft[field.key]}
                onChange={(event) => setDraft((current) => ({ ...current, [field.key]: event.target.value }))}
              />
              <small>{field.help}</small>
            </label>
          ))}
        </div>
        {problems.length > 0 && <div className="form-problems">{problems.map((problem) => <p key={problem}>{problem}</p>)}</div>}
        <div className="modal-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={() => setDraft(Object.fromEntries(Object.entries(DEFAULT_SETTINGS).map(([key, value]) => [key, String(value)])) as Record<keyof AnalysisSettings, string>)}
          >
            Reset to defaults
          </button>
          <button type="submit" className="upload-button" disabled={problems.length > 0}>Apply and re-analyse</button>
        </div>
      </form>
    </div>
  );
}
