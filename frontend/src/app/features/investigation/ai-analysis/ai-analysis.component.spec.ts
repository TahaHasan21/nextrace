import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';

import { InvestigationService } from '../../../core/services/investigation.service';
import {
  AnalysisRunDetail,
  AnalysisRunSummary,
  InvestigationAnalysis,
} from '../../../models/investigation.models';
import { AiAnalysisComponent } from './ai-analysis.component';

class FakeInvestigationService {
  private nextCall: (() => Observable<InvestigationAnalysis>) | null = null;
  private nextHistoryCall: (() => Observable<AnalysisRunSummary[]>) | null = null;
  private nextDetailCall: (() => Observable<AnalysisRunDetail>) | null = null;
  public calls: number[] = [];
  public historyCalls: number[] = [];
  public detailCalls: Array<[number, number]> = [];

  analyzeInvestigation(eventId: number): Observable<InvestigationAnalysis> {
    this.calls.push(eventId);
    if (this.nextCall) {
      return this.nextCall();
    }
    return new Subject<InvestigationAnalysis>().asObservable();
  }

  queueResult(fn: () => Observable<InvestigationAnalysis>): void {
    this.nextCall = fn;
  }

  listAnalysisRuns(eventId: number): Observable<AnalysisRunSummary[]> {
    this.historyCalls.push(eventId);
    if (this.nextHistoryCall) {
      return this.nextHistoryCall();
    }
    return of([]);
  }

  queueHistory(fn: () => Observable<AnalysisRunSummary[]>): void {
    this.nextHistoryCall = fn;
  }

  getAnalysisRun(eventId: number, runId: number): Observable<AnalysisRunDetail> {
    this.detailCalls.push([eventId, runId]);
    if (this.nextDetailCall) {
      return this.nextDetailCall();
    }
    return new Subject<AnalysisRunDetail>().asObservable();
  }

  queueDetail(fn: () => Observable<AnalysisRunDetail>): void {
    this.nextDetailCall = fn;
  }
}

const sampleAnalysis: InvestigationAnalysis = {
  summary: 'A configuration change preceded a database latency anomaly and error spike.',
  primary_candidate: { event_id: 2, event_type: 'config_change', supporting_evidence_ids: [] },
  alternative_candidates: [
    { event_id: 1, event_type: 'deployment', supporting_evidence_ids: [] },
    { event_id: 3, event_type: 'db_latency', supporting_evidence_ids: [] },
  ],
  supporting_points: ['Occurred shortly before the degradation window.'],
  uncertainties: ['Causation is not established by the available evidence.'],
  recommended_checks: ['Inspect the configuration diff.'],
};

describe('AiAnalysisComponent', () => {
  let fixture: ComponentFixture<AiAnalysisComponent>;
  let fakeService: FakeInvestigationService;

  beforeEach(async () => {
    fakeService = new FakeInvestigationService();

    await TestBed.configureTestingModule({
      imports: [AiAnalysisComponent],
      providers: [{ provide: InvestigationService, useValue: fakeService }],
    }).compileComponents();

    fixture = TestBed.createComponent(AiAnalysisComponent);
    fixture.componentRef.setInput('eventId', 5);
  });

  function clickAnalyze(): void {
    const button: HTMLButtonElement = fixture.nativeElement.querySelector('.analyze-button');
    button.click();
    fixture.detectChanges();
  }

  it('does not call the AI service automatically on load', () => {
    fixture.detectChanges();

    expect(fakeService.calls.length).toBe(0);
    expect(fixture.nativeElement.querySelector('.analyze-button')).not.toBeNull();
  });

  it('shows a loading state while the analysis request is in flight', () => {
    fakeService.queueResult(() => new Subject<InvestigationAnalysis>().asObservable());
    fixture.detectChanges();

    clickAnalyze();

    expect(fixture.nativeElement.textContent).toContain('Analyzing investigation');
  });

  it('renders the full structured analysis once loaded', () => {
    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();

    clickAnalyze();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain(sampleAnalysis.summary);
    expect(fakeService.calls).toEqual([5]);
  });

  it('renders the primary candidate', () => {
    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();
    clickAnalyze();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Config Change');
    expect(text).toContain('#2');
  });

  it('renders alternative candidates', () => {
    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();
    clickAnalyze();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Deployment');
    expect(text).toContain('Db Latency');
  });

  it('renders supporting evidence citations for the primary candidate, linked to the evidence anchor', () => {
    const analysisWithEvidence = {
      ...sampleAnalysis,
      primary_candidate: {
        event_id: 2,
        event_type: 'config_change',
        supporting_evidence_ids: ['ev1_abc123', 'ev1_def456'],
      },
    };
    fakeService.queueResult(() => of(analysisWithEvidence));
    fixture.detectChanges();
    clickAnalyze();

    const links: NodeListOf<HTMLAnchorElement> =
      fixture.nativeElement.querySelectorAll('.ai-evidence-refs .evidence-link');
    expect(links.length).toBe(2);
    expect(links[0].textContent).toContain('ev1_abc123');
    expect(links[0].getAttribute('href')).toBe('#evidence-ev1_abc123');
    expect(links[1].getAttribute('href')).toBe('#evidence-ev1_def456');
  });

  it('renders supporting evidence citations for alternative candidates', () => {
    const analysisWithEvidence = {
      ...sampleAnalysis,
      alternative_candidates: [
        { event_id: 1, event_type: 'deployment', supporting_evidence_ids: ['ev1_f47291'] },
        { event_id: 3, event_type: 'db_latency', supporting_evidence_ids: [] },
      ],
    };
    fakeService.queueResult(() => of(analysisWithEvidence));
    fixture.detectChanges();
    clickAnalyze();

    const link: HTMLAnchorElement | null = fixture.nativeElement.querySelector(
      '.ai-alternatives .evidence-link',
    );
    expect(link).not.toBeNull();
    expect(link!.getAttribute('href')).toBe('#evidence-ev1_f47291');
  });

  it('omits the evidence-citations block when a candidate has no supporting evidence', () => {
    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();
    clickAnalyze();

    expect(fixture.nativeElement.querySelector('.ai-evidence-refs')).toBeNull();
  });

  it('renders supporting points under their own heading, distinct from evidence/uncertainties', () => {
    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();
    clickAnalyze();

    const headings = Array.from(
      fixture.nativeElement.querySelectorAll('.ai-block h3'),
    ) as HTMLElement[];
    const supportingBlock = headings.find((h) => h.textContent?.includes('Supporting points'));
    expect(supportingBlock).toBeDefined();
    expect(supportingBlock!.parentElement!.textContent).toContain(
      'Occurred shortly before the degradation window.',
    );
  });

  it('emits its state through analysisStateChange on every transition', () => {
    const emitted: string[] = [];
    fixture.componentInstance.analysisStateChange.subscribe((state: string) => emitted.push(state));

    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();
    clickAnalyze();

    expect(emitted).toEqual(['loading', 'success']);
  });

  it('emits idle through analysisStateChange when the event id changes', () => {
    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();
    clickAnalyze();

    const emitted: string[] = [];
    fixture.componentInstance.analysisStateChange.subscribe((state: string) => emitted.push(state));
    fixture.componentRef.setInput('eventId', 9);
    fixture.detectChanges();

    expect(emitted).toEqual(['idle']);
  });

  it('renders uncertainties', () => {
    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();
    clickAnalyze();

    expect(fixture.nativeElement.textContent).toContain(
      'Causation is not established by the available evidence.',
    );
  });

  it('renders recommended checks', () => {
    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();
    clickAnalyze();

    expect(fixture.nativeElement.textContent).toContain('Inspect the configuration diff.');
  });

  it('shows an empty state when no primary candidate could be identified', () => {
    fakeService.queueResult(() =>
      of({ ...sampleAnalysis, primary_candidate: null }),
    );
    fixture.detectChanges();
    clickAnalyze();

    expect(fixture.nativeElement.textContent).toContain(
      'Insufficient evidence to identify a primary candidate confidently.',
    );
  });

  it('shows a provider-unavailable message on a 503 response', () => {
    fakeService.queueResult(() =>
      throwError(
        () =>
          new HttpErrorResponse({
            status: 503,
            statusText: 'Service Unavailable',
            error: { detail: 'AI analysis is not configured.' },
          }),
      ),
    );
    fixture.detectChanges();

    clickAnalyze();

    expect(fixture.nativeElement.textContent).toContain('AI analysis is not configured.');
  });

  it('shows a generic error message and a retry button on other failures', () => {
    fakeService.queueResult(() =>
      throwError(() => new HttpErrorResponse({ status: 500, statusText: 'Server Error' })),
    );
    fixture.detectChanges();

    clickAnalyze();

    expect(fixture.nativeElement.textContent).toContain('Could not generate an AI analysis');
    expect(fixture.nativeElement.querySelector('.analyze-button')).not.toBeNull();
  });

  it('resets to idle when the event id changes', () => {
    fakeService.queueResult(() => of(sampleAnalysis));
    fixture.detectChanges();
    clickAnalyze();
    expect(fixture.nativeElement.textContent).toContain(sampleAnalysis.summary);

    fixture.componentRef.setInput('eventId', 9);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).not.toContain(sampleAnalysis.summary);
    expect(fixture.nativeElement.querySelector('.analyze-button')).not.toBeNull();
  });

  it('always shows the grounding disclaimer', () => {
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('investigative aid, not proof of causation');
  });

  describe('previous analyses (history)', () => {
    const sampleRun: AnalysisRunSummary = {
      run_id: 7,
      target_event_id: 5,
      status: 'complete',
      provider: 'GeminiProvider',
      model: 'gemini-3.5-flash',
      requested_at: '2026-09-12T10:00:00Z',
      completed_at: '2026-09-12T10:00:05Z',
      retry_count: 0,
      summary: 'A grounded summary.',
    };

    const failedRun: AnalysisRunSummary = {
      run_id: 8,
      target_event_id: 5,
      status: 'failed',
      provider: 'GeminiProvider',
      model: 'gemini-3.5-flash',
      requested_at: '2026-09-11T09:00:00Z',
      completed_at: '2026-09-11T09:00:02Z',
      retry_count: 1,
      summary: null,
    };

    it('loads history automatically on init, without triggering a real analysis call', () => {
      fakeService.queueHistory(() => of([sampleRun]));

      fixture.detectChanges();

      expect(fakeService.historyCalls).toEqual([5]);
      expect(fakeService.calls.length).toBe(0);
    });

    it('shows an empty state when there is no previous history', () => {
      fakeService.queueHistory(() => of([]));

      fixture.detectChanges();

      const details = fixture.nativeElement.querySelector('.history-details');
      expect(details.textContent).toContain('No previous analyses for this event yet.');
    });

    it('lists successful runs with their metadata', () => {
      fakeService.queueHistory(() => of([sampleRun]));

      fixture.detectChanges();

      const item: HTMLElement = fixture.nativeElement.querySelector('.history-item');
      expect(item.textContent).toContain('GeminiProvider / gemini-3.5-flash');
      const status = item.querySelector('.history-status') as HTMLElement;
      expect(status.textContent?.trim()).toBe('complete');
    });

    it('lists failed runs, including their retry count', () => {
      fakeService.queueHistory(() => of([failedRun]));

      fixture.detectChanges();

      const item: HTMLElement = fixture.nativeElement.querySelector('.history-item');
      const status = item.querySelector('.history-status') as HTMLElement;
      expect(status.textContent?.trim()).toBe('failed');
      expect(item.textContent).toContain('1 retry');
    });

    it('shows an error state when the history request fails', () => {
      fakeService.queueHistory(() => throwError(() => new HttpErrorResponse({ status: 500 })));

      fixture.detectChanges();

      expect(fixture.nativeElement.querySelector('.history-details').textContent).toContain(
        'Could not load previous analyses.',
      );
    });

    it('fetches and displays the full result when a completed run is selected', () => {
      fakeService.queueHistory(() => of([sampleRun]));
      fixture.detectChanges();

      fakeService.queueDetail(() =>
        of({
          ...sampleRun,
          context_snapshot: {},
          result: sampleAnalysis,
          error_message: null,
        }),
      );

      const toggle: HTMLButtonElement = fixture.nativeElement.querySelector('.history-item-toggle');
      toggle.click();
      fixture.detectChanges();

      expect(fakeService.detailCalls).toEqual([[5, 7]]);
      const detail = fixture.nativeElement.querySelector('.history-run-detail');
      expect(detail.textContent).toContain(sampleAnalysis.summary);
    });

    it('shows the safe error message when a failed run is selected', () => {
      fakeService.queueHistory(() => of([failedRun]));
      fixture.detectChanges();

      fakeService.queueDetail(() =>
        of({
          ...failedRun,
          context_snapshot: {},
          result: null,
          error_message: 'AI analysis is not available right now.',
        }),
      );

      const toggle: HTMLButtonElement = fixture.nativeElement.querySelector('.history-item-toggle');
      toggle.click();
      fixture.detectChanges();

      const detail = fixture.nativeElement.querySelector('.history-run-detail');
      expect(detail.textContent).toContain('AI analysis is not available right now.');
    });

    it('collapses the run detail when toggled again', () => {
      fakeService.queueHistory(() => of([sampleRun]));
      fixture.detectChanges();
      fakeService.queueDetail(() => of({ ...sampleRun, context_snapshot: {}, result: sampleAnalysis, error_message: null }));

      const toggle: HTMLButtonElement = fixture.nativeElement.querySelector('.history-item-toggle');
      toggle.click();
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.history-run-detail')).not.toBeNull();

      toggle.click();
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.history-run-detail')).toBeNull();
    });

    it('reloads history after a new analysis completes', () => {
      fakeService.queueHistory(() => of([]));
      fixture.detectChanges();

      fakeService.queueResult(() => of(sampleAnalysis));
      fakeService.queueHistory(() => of([sampleRun]));
      clickAnalyze();

      expect(fakeService.historyCalls).toEqual([5, 5]);
      expect(fixture.nativeElement.querySelector('.history-item')).not.toBeNull();
    });

    it('reloads history when the event id changes', () => {
      fakeService.queueHistory(() => of([]));
      fixture.detectChanges();

      fakeService.queueHistory(() => of([sampleRun]));
      fixture.componentRef.setInput('eventId', 9);
      fixture.detectChanges();

      expect(fakeService.historyCalls).toEqual([5, 9]);
    });
  });
});
