import { Component, Input } from '@angular/core';

import { Candidate, Evidence } from '../../../models/investigation.models';
import { humanizeEventType } from '../../../shared/format-metadata';

const EVIDENCE_TYPE_LABELS: Record<string, string> = {
  temporal_proximity: 'Temporal relationship',
  sequence_relationship: 'Sequence relationship',
  recovery_relationship: 'Recovery relationship',
};

export interface SupportingEvidenceSummary {
  id: string;
  label: string;
  description: string;
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
