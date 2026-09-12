import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';

import { InvestigationService } from '../../core/services/investigation.service';
import { Investigation, InvestigationAnalysis } from '../../models/investigation.models';
import { InvestigationComponent } from './investigation.component';

class FakeInvestigationService {
  private response$ = new Subject<Investigation>();
  private nextCall: (() => Observable<Investigation>) | null = null;
  private nextAnalysisCall: (() => Observable<InvestigationAnalysis>) | null = null;

  getInvestigation(_eventId: number): Observable<Investigation> {
    if (this.nextCall) {
      return this.nextCall();
    }
    return this.response$.asObservable();
  }

  queueResult(fn: () => Observable<Investigation>): void {
    this.nextCall = fn;
  }

  // Most tests never click "Analyze with AI"; a small subset (AI analysis
  // status wiring) does, via queueAnalysisResult().
  analyzeInvestigation(_eventId: number): Observable<InvestigationAnalysis> {
    if (this.nextAnalysisCall) {
      return this.nextAnalysisCall();
    }
    return new Subject<InvestigationAnalysis>().asObservable();
  }

  queueAnalysisResult(fn: () => Observable<InvestigationAnalysis>): void {
    this.nextAnalysisCall = fn;
  }
}

const sampleAnalysis: InvestigationAnalysis = {
  summary: 'A deployment preceded the incident window.',
  primary_candidate: null,
  alternative_candidates: [],
  supporting_points: [],
  uncertainties: [],
  recommended_checks: [],
};

const sampleInvestigation: Investigation = {
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
  timeline: [
    {
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
  ],
  evidence: [],
  candidates: [],
};

describe('InvestigationComponent', () => {
  let fixture: ComponentFixture<InvestigationComponent>;
  let fakeService: FakeInvestigationService;

  beforeEach(async () => {
    fakeService = new FakeInvestigationService();

    await TestBed.configureTestingModule({
      imports: [InvestigationComponent],
      providers: [{ provide: InvestigationService, useValue: fakeService }],
    }).compileComponents();

    fixture = TestBed.createComponent(InvestigationComponent);
  });

  function setEventIdAndInvestigate(eventId: string): void {
    const component = fixture.componentInstance as unknown as { eventIdInput: string };
    component.eventIdInput = eventId;
    fixture.detectChanges();
    const button: HTMLButtonElement = fixture.nativeElement.querySelector('.lookup-row button');
    button.click();
    fixture.detectChanges();
  }

  /** Drives the REAL <input type="number"> DOM element rather than setting
   * the component property directly. This matters: Angular's NumberValueAccessor
   * (used automatically for type="number" + ngModel) writes back an actual
   * `number` (or `null`) at runtime, never a string - setEventIdAndInvestigate()
   * above bypasses that entirely and would not have caught the real bug this
   * regression test targets ("this.eventIdInput.trim is not a function"). */
  function typeIntoInputAndInvestigate(value: string): void {
    fixture.detectChanges();
    const input: HTMLInputElement = fixture.nativeElement.querySelector('#event-id');
    input.value = value;
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    const button: HTMLButtonElement = fixture.nativeElement.querySelector('.lookup-row button');
    button.click();
    fixture.detectChanges();
  }

  it('handles a value typed into the real number input (NumberValueAccessor writes a number, not a string)', () => {
    fakeService.queueResult(() => of(sampleInvestigation));

    typeIntoInputAndInvestigate('5');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Payment failures reported in production');
  });

  it('shows the default idle state before any investigation is run', () => {
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Enter an event ID above to begin an investigation.');
  });

  it('labels the input explicitly as Event ID, not Investigation ID', () => {
    fixture.detectChanges();

    const label = fixture.nativeElement.querySelector('label.lookup-label');
    expect(label.textContent.trim()).toBe('Event ID');
  });

  it('rejects an empty event ID without calling the service', () => {
    setEventIdAndInvestigate('');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Enter an event ID.');
  });

  it('rejects a non-numeric/non-positive event ID without calling the service', () => {
    setEventIdAndInvestigate('-3');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Event ID must be a positive whole number.');
  });

  it('disables the input and button while a request is in flight', () => {
    fakeService.queueResult(() => new Subject<Investigation>().asObservable());

    setEventIdAndInvestigate('5');

    const button: HTMLButtonElement = fixture.nativeElement.querySelector('.lookup-row button');
    const input: HTMLInputElement = fixture.nativeElement.querySelector('#event-id');
    expect(button.disabled).toBe(true);
    expect(input.readOnly).toBe(true);
    expect(button.textContent).toContain('Investigating');
  });

  it('offers a retry action after a load failure, which re-runs the same lookup', () => {
    fakeService.queueResult(() =>
      throwError(() => new HttpErrorResponse({ status: 500, statusText: 'Server Error' })),
    );
    setEventIdAndInvestigate('5');

    fakeService.queueResult(() => of(sampleInvestigation));
    const retryButton: HTMLButtonElement = fixture.nativeElement.querySelector('.retry-button');
    expect(retryButton).not.toBeNull();
    retryButton.click();
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Payment failures reported in production');
  });

  it('offers a retry action after a not-found result', () => {
    fakeService.queueResult(() =>
      throwError(() => new HttpErrorResponse({ status: 404, statusText: 'Not Found' })),
    );
    setEventIdAndInvestigate('999999');

    expect(fixture.nativeElement.querySelector('.retry-button')).not.toBeNull();
  });

  it('shows the investigated event ID prominently in the header', () => {
    fakeService.queueResult(() => of(sampleInvestigation));

    setEventIdAndInvestigate('5');

    const summary = fixture.nativeElement.querySelector('.incident-summary');
    expect(summary.textContent).toContain('Event #5');
  });

  it('shows a loading state while the request is in flight', () => {
    fakeService.queueResult(() => new Subject<Investigation>().asObservable());

    setEventIdAndInvestigate('5');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Loading investigation');
  });

  it('renders the full investigation once loaded', () => {
    fakeService.queueResult(() => of(sampleInvestigation));

    setEventIdAndInvestigate('5');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Payment failures reported in production');
    expect(fixture.nativeElement.querySelector('app-timeline')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('app-evidence')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('app-candidates')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('app-ai-analysis')).not.toBeNull();
  });

  it('renders the incident header using the actual API data, not hardcoded values', () => {
    fakeService.queueResult(() => of(sampleInvestigation));

    setEventIdAndInvestigate('5');

    const summary = fixture.nativeElement.querySelector('.incident-summary');
    expect(summary.textContent).toContain('Production Investigation');
    expect(summary.textContent).toContain('Incident');
    expect(summary.textContent).toContain('payment-service');
    expect(summary.textContent).toContain('production');
    expect(summary.textContent).toContain('critical');
    expect(summary.textContent).toContain('application');
    expect(summary.textContent).toContain('2026');
    expect(summary.querySelector('.badge--target')?.textContent).toContain('Investigation Target');
  });

  it('shows the AI analysis status as not yet requested until the AI panel is used', () => {
    fakeService.queueResult(() => of(sampleInvestigation));

    setEventIdAndInvestigate('5');

    const summary = fixture.nativeElement.querySelector('.incident-summary');
    expect(summary.textContent).toContain('Not yet requested');
  });

  it("reflects the AI panel's own status once analysis completes, without duplicating its logic", () => {
    fakeService.queueResult(() => of(sampleInvestigation));
    setEventIdAndInvestigate('5');
    fakeService.queueAnalysisResult(() => of(sampleAnalysis));

    const analyzeButton: HTMLButtonElement =
      fixture.nativeElement.querySelector('app-ai-analysis .analyze-button');
    analyzeButton.click();
    fixture.detectChanges();

    const summary = fixture.nativeElement.querySelector('.incident-summary');
    expect(summary.textContent).toContain('Complete');
  });

  it('resets the AI analysis status when a new investigation is started', () => {
    fakeService.queueResult(() => of(sampleInvestigation));
    setEventIdAndInvestigate('5');
    fakeService.queueAnalysisResult(() => of(sampleAnalysis));
    (fixture.nativeElement.querySelector('app-ai-analysis .analyze-button') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.incident-summary').textContent).toContain('Complete');

    fakeService.queueResult(() => of({ ...sampleInvestigation, target_event: { ...sampleInvestigation.target_event, id: 6 } }));
    setEventIdAndInvestigate('6');

    expect(fixture.nativeElement.querySelector('.incident-summary').textContent).toContain(
      'Not yet requested',
    );
  });

  it('keeps the timeline, candidates, and evidence visible when AI analysis fails', () => {
    fakeService.queueResult(() => of(sampleInvestigation));
    setEventIdAndInvestigate('5');
    fakeService.queueAnalysisResult(() =>
      throwError(
        () =>
          new HttpErrorResponse({
            status: 503,
            statusText: 'Service Unavailable',
            error: { detail: 'AI analysis is not configured.' },
          }),
      ),
    );

    (fixture.nativeElement.querySelector('app-ai-analysis .analyze-button') as HTMLButtonElement).click();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.incident-summary').textContent).toContain(
      'Unavailable',
    );
    expect(fixture.nativeElement.querySelector('app-timeline')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('app-candidates')).not.toBeNull();
    expect(fixture.nativeElement.querySelector('app-evidence')).not.toBeNull();
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Payment failures reported in production');
  });

  it('shows a not-found message on a 404 response', () => {
    fakeService.queueResult(() =>
      throwError(() => new HttpErrorResponse({ status: 404, statusText: 'Not Found' })),
    );

    setEventIdAndInvestigate('999999');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('No event found with ID 999999');
  });

  it('shows an error message on a non-404 failure', () => {
    fakeService.queueResult(() =>
      throwError(() => new HttpErrorResponse({ status: 500, statusText: 'Server Error' })),
    );

    setEventIdAndInvestigate('5');

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Could not load the investigation');
  });
});
