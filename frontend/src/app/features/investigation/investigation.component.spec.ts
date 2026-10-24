import { Location, PlatformLocation } from '@angular/common';
import { MockPlatformLocation } from '@angular/common/testing';
import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';

import { InvestigationService } from '../../core/services/investigation.service';
import {
  AnalysisRunDetail,
  AnalysisRunSummary,
  Investigation,
  InvestigationAnalysis,
  NextraceEvent,
} from '../../models/investigation.models';
import { InvestigationComponent } from './investigation.component';

class FakeInvestigationService {
  private response$ = new Subject<Investigation>();
  private nextCall: (() => Observable<Investigation>) | null = null;
  private nextAnalysisCall: (() => Observable<InvestigationAnalysis>) | null = null;
  readonly requestedEventIds: number[] = [];
  recentEvents: NextraceEvent[] = [];

  listRecentEvents(): Observable<NextraceEvent[]> {
    return of(this.recentEvents);
  }

  getInvestigation(eventId: number): Observable<Investigation> {
    this.requestedEventIds.push(eventId);
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

  // The AI panel loads its own history automatically on init; these tests
  // don't exercise history behavior, so an empty list is a safe default.
  listAnalysisRuns(_eventId: number): Observable<AnalysisRunSummary[]> {
    return of([]);
  }

  getAnalysisRun(_eventId: number, _runId: number): Observable<AnalysisRunDetail> {
    return new Subject<AnalysisRunDetail>().asObservable();
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
      providers: [
        { provide: InvestigationService, useValue: fakeService },
        // Investigations are reflected in the URL (?event=<id>). The real
        // Location over a mock platform location keeps browser semantics
        // (path() includes the query) without leaking between tests.
        { provide: PlatformLocation, useClass: MockPlatformLocation },
      ],
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

  it('labels the investigation with the target event environment, never a fixed one', () => {
    fakeService.queueResult(() =>
      of({
        ...sampleInvestigation,
        target_event: { ...sampleInvestigation.target_event, environment: 'local-demo' },
      }),
    );

    setEventIdAndInvestigate('5');

    const eyebrow = fixture.nativeElement.querySelector('.incident-eyebrow');
    expect(eyebrow.textContent).toContain('local-demo');
    expect(eyebrow.textContent.toLowerCase()).not.toContain('production');
  });

  it('renders the incident header using the actual API data, not hardcoded values', () => {
    fakeService.queueResult(() => of(sampleInvestigation));

    setEventIdAndInvestigate('5');

    const summary = fixture.nativeElement.querySelector('.incident-summary');
    expect(summary.querySelector('.incident-eyebrow').textContent).toContain('production');
    expect(summary.textContent).toContain('10:04:00 UTC');
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
  describe('finding and navigating investigations', () => {
    function eventInUrl(): string | null {
      const query = TestBed.inject(Location).path().split('?')[1] ?? '';
      return new URLSearchParams(query).get('event');
    }

    /** The fixture from beforeEach must not also initialize from the URL. */
    function recreateFixture(): void {
      fixture.destroy();
      fixture = TestBed.createComponent(InvestigationComponent);
      fixture.detectChanges();
    }

    const recentDeployment: NextraceEvent = {
      ...sampleInvestigation.timeline[0],
      id: 7,
      event_type: 'deployment',
      message: 'Version 2.3.0 deployed',
    };

    it('opens an investigation chosen from the recent events list', () => {
      fakeService.recentEvents = [recentDeployment];
      fakeService.queueResult(() => of(sampleInvestigation));
      fixture.detectChanges();

      const item: HTMLButtonElement = fixture.nativeElement.querySelector('.recent-item');
      expect(item.textContent).toContain('Version 2.3.0 deployed');
      item.click();
      fixture.detectChanges();

      expect(fakeService.requestedEventIds).toEqual([7]);
      expect(fixture.nativeElement.textContent).toContain('Payment failures reported in production');
    });

    it('reflects the open investigation in the URL so it can be linked and reloaded', () => {
      fakeService.queueResult(() => of(sampleInvestigation));

      setEventIdAndInvestigate('5');

      expect(eventInUrl()).toBe('5');
    });

    it('does not change the URL when the lookup fails', () => {
      fakeService.queueResult(() =>
        throwError(() => new HttpErrorResponse({ status: 404, statusText: 'Not Found' })),
      );

      setEventIdAndInvestigate('999');

      expect(eventInUrl()).toBeNull();
    });

    it('opens the investigation named in ?event= on load', () => {
      TestBed.inject(Location).replaceState('/', 'event=5');
      fakeService.queueResult(() => of(sampleInvestigation));
      recreateFixture();

      expect(fakeService.requestedEventIds).toEqual([5]);
      expect(fixture.nativeElement.textContent).toContain('Payment failures reported in production');
    });

    it('rejects an invalid ?event= value with the normal validation message', () => {
      TestBed.inject(Location).replaceState('/', 'event=abc');
      recreateFixture();

      expect(fakeService.requestedEventIds).toEqual([]);
      expect(fixture.nativeElement.textContent).toContain('Event ID must be a positive whole number.');
    });

    it('keeps every in-page link on the current page so following it never reloads the app', () => {
      // Regression: bare "#id" hrefs resolve against <base href="/"> to
      // "/#id", dropping ?event=<id> - the browser then loads a new
      // document and the open investigation (and in-memory key) is lost.
      const evidence = {
        id: 'ev1_0123456789abcdef',
        type: 'sequence_relationship',
        description: 'A deployment was followed by an incident.',
        event_ids: [7, 5],
      };
      const linked: Investigation = {
        ...sampleInvestigation,
        timeline: [recentDeployment, ...sampleInvestigation.timeline],
        evidence: [evidence],
        candidates: [
          {
            event_id: 7,
            event_type: 'deployment',
            score: 85,
            reasons: ['Occurred 1 minute before the incident.'],
            reason_codes: ['temporal_proximity'],
            supporting_evidence_ids: [evidence.id],
          },
        ],
      };
      fakeService.queueResult(() => of(linked));
      fakeService.queueAnalysisResult(() =>
        of({
          ...sampleAnalysis,
          primary_candidate: {
            event_id: 7,
            event_type: 'deployment',
            supporting_evidence_ids: [evidence.id],
          },
        }),
      );
      setEventIdAndInvestigate('5');
      fixture.nativeElement.querySelector('.analyze-button').click();
      fixture.detectChanges();

      const base = 'http://localhost/'; // index.html: <base href="/">
      const page = new URL('?event=5', base).href;
      const links: HTMLAnchorElement[] = Array.from(
        fixture.nativeElement.querySelectorAll('a[href*="#"]'),
      );
      const targets = links.map((a) => new URL(a.getAttribute('href')!, base));
      expect(targets.map((url) => url.hash).sort()).toEqual([
        '#event-5', // evidence -> supporting events
        '#event-7', // candidate -> its event; evidence -> supporting events
        '#event-7',
        '#evidence-ev1_0123456789abcdef', // candidate -> supporting evidence
        '#evidence-ev1_0123456789abcdef', // AI citation
      ]);
      for (const url of targets) {
        url.hash = '';
        expect(url.href).toBe(page); // same document: only the fragment differs
      }
    });

    it('never lets an earlier, slower lookup replace the latest selection', () => {
      const first = new Subject<Investigation>();
      const second = new Subject<Investigation>();
      fakeService.recentEvents = [recentDeployment, { ...recentDeployment, id: 8 }];
      fixture.detectChanges();
      const items = fixture.nativeElement.querySelectorAll('.recent-item');

      fakeService.queueResult(() => first);
      items[0].click(); // event 7 - its response is slow
      fakeService.queueResult(() => second);
      items[1].click(); // event 8 - selected last
      fixture.detectChanges();

      second.next({
        ...sampleInvestigation,
        target_event: { ...sampleInvestigation.target_event, id: 8, message: 'Selected last' },
      });
      first.next({
        ...sampleInvestigation,
        target_event: { ...sampleInvestigation.target_event, id: 7, message: 'Stale response' },
      });
      fixture.detectChanges();

      expect(fakeService.requestedEventIds).toEqual([7, 8]);
      const text = fixture.nativeElement.textContent as string;
      expect(text).toContain('Selected last');
      expect(text).not.toContain('Stale response');
      expect(eventInUrl()).toBe('8');
    });

    it('does not let an in-flight lookup overwrite a later validation error', () => {
      const pending = new Subject<Investigation>();
      fakeService.queueResult(() => pending);
      setEventIdAndInvestigate('5');

      (fixture.componentInstance as unknown as { openEvent(id: number): void }).openEvent(-1);
      pending.next(sampleInvestigation);
      fixture.detectChanges();

      expect(fixture.nativeElement.textContent).toContain('Event ID must be a positive whole number.');
      expect(eventInUrl()).toBeNull();
    });

    function investigationFor(id: number): Investigation {
      const target = { ...sampleInvestigation.target_event, id, message: `Investigation of ${id}` };
      return { ...sampleInvestigation, target_event: target, timeline: [target] };
    }

    function shownEvent(): string | null {
      return fixture.nativeElement.querySelector('.incident-event-id')?.textContent.trim() ?? null;
    }

    it('follows browser Back and Forward to the investigation the URL names', () => {
      const location = TestBed.inject(Location);
      fakeService.queueResult(() => of(investigationFor(5)));
      setEventIdAndInvestigate('5');
      // An in-page link pushes a same-document history entry.
      location.go('/', 'event=5#event-5');
      // Pivot to event 7 (replaces that entry's URL).
      fakeService.queueResult(() => of(investigationFor(7)));
      (fixture.componentInstance as unknown as { openEvent(id: number): void }).openEvent(7);
      fixture.detectChanges();
      expect(eventInUrl()).toBe('7');
      expect(shownEvent()).toBe('Event #7');

      fakeService.queueResult(() => of(investigationFor(5)));
      location.back();
      fixture.detectChanges();
      expect(eventInUrl()).toBe('5');
      expect(shownEvent()).toBe('Event #5');

      fakeService.queueResult(() => of(investigationFor(7)));
      location.forward();
      fixture.detectChanges();
      expect(eventInUrl()).toBe('7');
      expect(shownEvent()).toBe('Event #7');
      expect(fakeService.requestedEventIds).toEqual([5, 7, 5, 7]);
    });

    it('does not reload the investigation when only the fragment changes', () => {
      const location = TestBed.inject(Location);
      fakeService.queueResult(() => of(investigationFor(5)));
      setEventIdAndInvestigate('5');
      location.go('/', 'event=5#event-5');

      location.back(); // back to ?event=5 - same investigation
      fixture.detectChanges();
      location.forward(); // and to its fragment again
      fixture.detectChanges();

      expect(fakeService.requestedEventIds).toEqual([5]);
      expect(shownEvent()).toBe('Event #5');
    });

    it('returns to the idle state when history reaches a URL without ?event=', () => {
      const location = TestBed.inject(Location);
      fakeService.queueResult(() => of(investigationFor(5)));
      setEventIdAndInvestigate('5');
      location.go('/');
      location.back();
      fixture.detectChanges();
      expect(shownEvent()).toBe('Event #5');

      location.forward();
      fixture.detectChanges();

      expect(shownEvent()).toBeNull();
      expect(fixture.nativeElement.textContent).toContain('Enter an event ID above to begin');
    });

    describe('fragment in the opening URL', () => {
      const original = Element.prototype.scrollIntoView;
      let scrolledTo: string[];

      beforeEach(() => {
        scrolledTo = [];
        // jsdom has no layout; record which element would be scrolled to.
        Element.prototype.scrollIntoView = function (this: Element) {
          scrolledTo.push(this.id);
        };
      });

      afterEach(() => {
        Element.prototype.scrollIntoView = original;
      });

      async function openUrl(query: string): Promise<void> {
        TestBed.inject(Location).replaceState('/', query);
        fakeService.queueResult(() => of(investigationFor(5)));
        recreateFixture();
        await fixture.whenStable();
      }

      it('scrolls to the target once the investigation has rendered and keeps the fragment', async () => {
        await openUrl('event=5#event-5');

        expect(scrolledTo).toEqual(['event-5']);
        expect(shownEvent()).toBe('Event #5');
        expect(TestBed.inject(Location).path(true)).toBe('?event=5#event-5');
      });

      it('ignores a fragment that matches nothing', async () => {
        await openUrl('event=5#evidence-ev1_missing');

        expect(scrolledTo).toEqual([]);
        expect(shownEvent()).toBe('Event #5');
      });

      it('ignores a malformed fragment', async () => {
        await openUrl('event=5#%E0%A4%A');

        expect(scrolledTo).toEqual([]);
        expect(shownEvent()).toBe('Event #5');
      });

      it('does not scroll for an ordinary lookup', async () => {
        fakeService.queueResult(() => of(investigationFor(5)));
        setEventIdAndInvestigate('5');
        await fixture.whenStable();

        expect(scrolledTo).toEqual([]);
        expect(eventInUrl()).toBe('5');
      });
    });

    it('pivots to another timeline event from inside an investigation', () => {
      const withDeployment: Investigation = {
        ...sampleInvestigation,
        timeline: [recentDeployment, ...sampleInvestigation.timeline],
      };
      fakeService.queueResult(() => of(withDeployment));
      setEventIdAndInvestigate('5');

      const pivots = fixture.nativeElement.querySelectorAll('.pivot-button');
      expect(pivots.length).toBe(1); // never offered for the current target
      pivots[0].click();
      fixture.detectChanges();

      expect(fakeService.requestedEventIds).toEqual([5, 7]);
      expect(eventInUrl()).toBe('7');
    });
  });
});
