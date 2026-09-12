/**
 * Types mirroring the backend's investigation API contract exactly
 * (see backend/app/schemas/investigation.py and backend/app/schemas/event.py).
 */

export interface NextraceEvent {
  id: number;
  service: string;
  environment: string;
  event_type: string;
  timestamp: string;
  severity: string | null;
  source: string;
  message: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

/** One of: 'temporal_proximity' | 'sequence_relationship' | 'recovery_relationship'. */
export interface Evidence {
  /** Stable, deterministic evidence identifier (e.g. "ev1_abc123..."),
   * derived only from the evidence type and ordered event IDs - not
   * persisted, but stable across requests and process restarts. */
  id: string;
  type: string;
  description: string;
  event_ids: number[];
}

/** A ranked investigation candidate. The score is a heuristic, not a confirmed root cause. */
export interface Candidate {
  event_id: number;
  event_type: string;
  score: number;
  reasons: string[];
  /** IDs of Evidence items (Evidence.id) supporting this candidate - never event IDs. */
  supporting_evidence_ids: string[];
}

export interface Investigation {
  target_event: NextraceEvent;
  timeline: NextraceEvent[];
  evidence: Evidence[];
  candidates: Candidate[];
}

/** A reference to an existing candidate/event - the backend guarantees
 * this always resolves to a real entry in the investigation's candidate
 * list (or is null), never an invented one. */
export interface CandidateReference {
  event_id: number;
  event_type: string;
  /** Evidence IDs (Evidence.id) the AI cited for this reference - the
   * backend guarantees every entry genuinely exists in this investigation's
   * evidence list, never an invented or foreign one. */
  supporting_evidence_ids: string[];
}

/** Grounded AI explanation of an investigation. An investigative aid, not
 * proof of causation - see InvestigationAnalysis in the backend. */
export interface InvestigationAnalysis {
  summary: string;
  primary_candidate: CandidateReference | null;
  alternative_candidates: CandidateReference[];
  supporting_points: string[];
  uncertainties: string[];
  recommended_checks: string[];
}
