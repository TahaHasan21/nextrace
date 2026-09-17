import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { AnalysisRunDetail, AnalysisRunSummary, Investigation } from '../../models/investigation.models';
import { InvestigationService } from './investigation.service';

describe('InvestigationService', () => {
  let service: InvestigationService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(InvestigationService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('requests the investigation for the given event id', () => {
    const mockInvestigation: Investigation = {
      target_event: {
        id: 5,
        service: 'payment-service',
        environment: 'production',
        event_type: 'incident',
        timestamp: '2026-09-06T10:04:00Z',
        severity: 'critical',
        source: 'application',
        message: 'Payment failures reported in production',
        metadata: null,
        created_at: '2026-09-06T10:04:01Z',
      },
      timeline: [],
      evidence: [],
      candidates: [],
    };

    let result: Investigation | undefined;
    service.getInvestigation(5).subscribe((response) => (result = response));

    const req = httpMock.expectOne('/investigations/5');
    expect(req.request.method).toBe('GET');
    req.flush(mockInvestigation);

    expect(result).toEqual(mockInvestigation);
  });

  it('propagates a 404 error to the caller', () => {
    let capturedStatus: number | undefined;
    let nextCalled = false;
    service.getInvestigation(999999).subscribe({
      next: () => {
        nextCalled = true;
      },
      error: (err) => (capturedStatus = err.status),
    });

    const req = httpMock.expectOne('/investigations/999999');
    req.flush({ detail: 'Event 999999 not found' }, { status: 404, statusText: 'Not Found' });

    expect(nextCalled).toBe(false);
    expect(capturedStatus).toBe(404);
  });

  it('requests the analysis run history for the given event id', () => {
    const mockHistory: AnalysisRunSummary[] = [
      {
        run_id: 7,
        target_event_id: 5,
        status: 'complete',
        provider: 'GeminiProvider',
        model: 'gemini-3.5-flash',
        requested_at: '2026-09-12T10:00:00Z',
        completed_at: '2026-09-12T10:00:05Z',
        retry_count: 0,
        summary: 'A grounded summary.',
      },
    ];

    let result: AnalysisRunSummary[] | undefined;
    service.listAnalysisRuns(5).subscribe((response) => (result = response));

    const req = httpMock.expectOne('/investigations/5/analyses');
    expect(req.request.method).toBe('GET');
    req.flush(mockHistory);

    expect(result).toEqual(mockHistory);
  });

  it('requests the full detail for one analysis run', () => {
    const mockDetail: AnalysisRunDetail = {
      run_id: 7,
      target_event_id: 5,
      status: 'complete',
      provider: 'GeminiProvider',
      model: 'gemini-3.5-flash',
      requested_at: '2026-09-12T10:00:00Z',
      completed_at: '2026-09-12T10:00:05Z',
      retry_count: 0,
      summary: 'A grounded summary.',
      context_snapshot: {},
      result: {
        summary: 'A grounded summary.',
        primary_candidate: null,
        alternative_candidates: [],
        supporting_points: [],
        uncertainties: [],
        recommended_checks: [],
      },
      error_message: null,
    };

    let result: AnalysisRunDetail | undefined;
    service.getAnalysisRun(5, 7).subscribe((response) => (result = response));

    const req = httpMock.expectOne('/investigations/5/analyses/7');
    expect(req.request.method).toBe('GET');
    req.flush(mockDetail);

    expect(result).toEqual(mockDetail);
  });
});
