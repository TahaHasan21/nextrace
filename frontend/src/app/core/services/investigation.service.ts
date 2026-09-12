import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { Investigation, InvestigationAnalysis } from '../../models/investigation.models';

/**
 * Requests are made against relative paths (e.g. "/investigations/1") so the
 * backend base URL is configured in one place: the dev proxy
 * (proxy.conf.json) for local development, or the production host serving
 * both the API and this app together.
 */
@Injectable({ providedIn: 'root' })
export class InvestigationService {
  private readonly http = inject(HttpClient);

  getInvestigation(eventId: number): Observable<Investigation> {
    return this.http.get<Investigation>(`/investigations/${eventId}`);
  }

  /** Triggered only on explicit user action ("Analyze with AI") - never
   * called automatically when an investigation loads. */
  analyzeInvestigation(eventId: number): Observable<InvestigationAnalysis> {
    return this.http.post<InvestigationAnalysis>(`/investigations/${eventId}/analysis`, {});
  }
}
