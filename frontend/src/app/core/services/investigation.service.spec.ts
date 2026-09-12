import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { Investigation } from '../../models/investigation.models';
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
});
