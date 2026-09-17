import { Component, Input } from '@angular/core';

import { Candidate, Evidence } from '../../../models/investigation.models';
import { humanizeEventType } from '../../../shared/format-metadata';

const EVIDENCE_TYPE_LABELS: Record<string, string> = {
  temporal_proximity: 'Temporal relationship',
  sequence_relationship: 'Sequence relationship',
  recovery_relationship: 'Recovery relationship',
};

/** Short, human-readable labels for the backend's stable reason codes
 * (CandidateRead.reason_codes) - purely presentational; the underlying
 * code is still shown so the mapping is never opaque. */
const REASON_CODE_LABELS: Record<string, string> = {
  temporal_proximity: 'Temporal proximity',
  relevant_event_type: 'Relevant event type',
  evidence_sequence: 'Evidence sequence',
  temporal_evidence: 'Temporal evidence',
  recovery_context: 'Recovery context',
};

export interface SupportingEvidenceSummary {
  id: string;
  label: string;
  description: string;
}

export interface CandidateReasonSummary {
  code: string;
  label: string;
  text: string;
}

@Component({
  selector: 'app-candidates',
  templateUrl: './candidates.component.html',
  styleUrl: './candidates.component.css',
})
export class CandidatesComponent {
  @Input({ required: true }) candidates: Candidate[] = [];
  /** Resolves supporting_evidence_ids against the already-loaded evidence
   * list - no extra API request is made per candidate. */
  @Input({ required: true }) evidence: Evidence[] = [];

  protected labelFor(eventType: string): string {
    return humanizeEventType(eventType);
  }

  /** Zips each reason with its stable structured code - index-aligned,
   * same length, per the backend's CandidateRead contract. Falls back to
   * showing the raw code (or nothing) if it's ever missing/unrecognized,
   * rather than hiding the reason text. */
  protected candidateReasons(candidate: Candidate): CandidateReasonSummary[] {
    return candidate.reasons.map((text, index) => {
      const code = candidate.reason_codes?.[index] ?? '';
      return { code, label: REASON_CODE_LABELS[code] ?? code, text };
    });
  }

  /** supporting_evidence_ids are references to Evidence.id - resolved here
   * against the evidence already displayed in the Evidence panel, so a
   * reference can link/scroll straight to the real evidence item. */
  protected supportingEvidence(candidate: Candidate): SupportingEvidenceSummary[] {
    const byId = new Map(this.evidence.map((item) => [item.id, item]));
    return candidate.supporting_evidence_ids
      .map((id) => byId.get(id))
      .filter((item): item is Evidence => item !== undefined)
      .map((item) => ({
        id: item.id,
        label: EVIDENCE_TYPE_LABELS[item.type] ?? item.type,
        description: item.description,
      }));
  }
}
