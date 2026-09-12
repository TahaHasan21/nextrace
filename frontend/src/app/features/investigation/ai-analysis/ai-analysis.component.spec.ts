import { HttpErrorResponse } from '@angular/common/http';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';

import { InvestigationService } from '../../../core/services/investigation.service';
import { InvestigationAnalysis } from '../../../models/investigation.models';
import { AiAnalysisComponent } from './ai-analysis.component';

class FakeInvestigationService {
  private nextCall: (() => Observable<InvestigationAnalysis>) | null = null;
  public calls: number[] = [];

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
});
