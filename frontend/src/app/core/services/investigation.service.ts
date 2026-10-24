import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AnalysisRunDetail,
  AnalysisRunSummary,
  Investigation,
  InvestigationAnalysis,
} from '../../models/investigation.models';

/**
 * Requests are made against relative paths (e.g. "/investigations/1") so the
 * backend base URL is configured in one place: the dev proxy
 * (proxy.conf.json) for local development, or the production host serving
 * both the API and this app together.
 */

/** Header the backend requires on POST /investigations/{id}/analysis, since
 * each call spends AI-provider quota. The key is never bundled into this
 * app: an operator enters it at runtime (see AiAnalysisComponent). */
export const INGEST_KEY_HEADER = 'X-NexTrace-Ingest-Key';

@Injectable({ providedIn: 'root' })
export class InvestigationService {
  private readonly http = inject(HttpClient);

  getInvestigation(eventId: number): Observable<Investigation> {
    return this.http.get<Investigation>(`/investigations/${eventId}`);
  }

  /** Triggered only on explicit user action ("Analyze with AI") - never
   * called automatically when an investigation loads. */
  analyzeInvestigation(eventId: number, ingestKey?: string): Observable<InvestigationAnalysis> {
    const headers = ingestKey ? new HttpHeaders({ [INGEST_KEY_HEADER]: ingestKey }) : undefined;
    return this.http.post<InvestigationAnalysis>(
      `/investigations/${eventId}/analysis`,
      {},
      { headers },
    );
  }

  /** Lightweight history list (no context snapshot) - safe to call
   * automatically when the investigation loads, unlike analyzeInvestigation()
   * which triggers a real AI provider call. */
  listAnalysisRuns(eventId: number): Observable<AnalysisRunSummary[]> {
    return this.http.get<AnalysisRunSummary[]>(`/investigations/${eventId}/analyses`);
  }

  getAnalysisRun(eventId: number, runId: number): Observable<AnalysisRunDetail> {
    return this.http.get<AnalysisRunDetail>(`/investigations/${eventId}/analyses/${runId}`);
  }
}
