import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  Component,
  EventEmitter,
  Input,
  OnChanges,
  OnInit,
  Output,
  SimpleChanges,
  inject,
  signal,
} from '@angular/core';

import { InvestigationService } from '../../../core/services/investigation.service';
import {
  AnalysisRunDetail,
  AnalysisRunSummary,
  InvestigationAnalysis,
} from '../../../models/investigation.models';
import { humanizeEventType } from '../../../shared/format-metadata';

export type AnalysisState = 'idle' | 'loading' | 'success' | 'unavailable' | 'error';
type HistoryState = 'idle' | 'loading' | 'success' | 'error';

@Component({
  selector: 'app-ai-analysis',
  imports: [DatePipe],
  templateUrl: './ai-analysis.component.html',
  styleUrl: './ai-analysis.component.css',
})
export class AiAnalysisComponent implements OnInit, OnChanges {
  @Input({ required: true }) eventId!: number;

  /** Lets a parent (e.g. the investigation header) surface AI analysis
   * status without duplicating this component's own request/state logic -
   * emitted on every state transition, never polled or inferred. */
  @Output() readonly analysisStateChange = new EventEmitter<AnalysisState>();

  private readonly investigationService = inject(InvestigationService);

  protected readonly state = signal<AnalysisState>('idle');
  protected readonly analysis = signal<InvestigationAnalysis | null>(null);
  protected readonly errorMessage = signal('');

  /** True after the backend rejected an analysis request for a missing or
   * invalid operator key (HTTP 401) - the error state then offers a key
   * field instead of a plain retry. */
  protected readonly needsKey = signal(false);

  /** Operator-entered key, held in memory only: never persisted to
   * storage, never rendered, gone on reload. */
  private ingestKey = '';

  // "Previous analyses" - a lightweight, read-only history list. Loading it
  // is safe to do automatically (it never triggers a real AI provider
  // call, unlike analyze() below), unlike the live "Analyze with AI" flow.
  protected readonly historyState = signal<HistoryState>('idle');
  protected readonly history = signal<AnalysisRunSummary[]>([]);
  protected readonly historyErrorMessage = signal('');

  protected readonly selectedRunId = signal<number | null>(null);
  protected readonly selectedRun = signal<AnalysisRunDetail | null>(null);
  protected readonly selectedRunError = signal('');

  ngOnInit(): void {
    this.loadHistory();
  }

  ngOnChanges(changes: SimpleChanges): void {
    // A new investigation target means any previous analysis - and any
    // previously loaded/selected history - is stale.
    if (changes['eventId'] && !changes['eventId'].firstChange) {
      this.analysis.set(null);
      this.errorMessage.set('');
      this.needsKey.set(false);
      this.setState('idle');

      this.selectedRunId.set(null);
      this.selectedRun.set(null);
      this.selectedRunError.set('');
      this.loadHistory();
    }
  }

  protected labelFor(eventType: string): string {
    return humanizeEventType(eventType);
  }

  analyze(): void {
    this.setState('loading');
    this.errorMessage.set('');
    this.needsKey.set(false);

    this.investigationService
      .analyzeInvestigation(this.eventId, this.ingestKey || undefined)
      .subscribe({
        next: (result) => {
          this.analysis.set(result);
          this.setState('success');
          this.loadHistory(); // the run that just completed is now part of history
        },
        error: (err: HttpErrorResponse) => {
          if (err.status === 401) {
            const rejectedKey = this.ingestKey !== '';
            this.ingestKey = '';
            this.needsKey.set(true);
            this.errorMessage.set(
              rejectedKey
                ? 'The operator key was not accepted.'
                : 'AI analysis requires an operator key.',
            );
            this.setState('error');
            return; // rejected before any analysis run was recorded
          }
          if (err.status === 503) {
            this.errorMessage.set(
              (err.error && err.error.detail) || 'AI analysis is not available right now.',
            );
            this.setState('unavailable');
          } else {
            this.errorMessage.set('Could not generate an AI analysis. Please try again.');
            this.setState('error');
          }
          this.loadHistory(); // a failed run is also recorded in history
        },
      });
  }

  protected analyzeWithKey(key: string): void {
    const trimmed = key.trim();
    if (!trimmed) {
      return;
    }
    this.ingestKey = trimmed;
    this.analyze();
  }

  /** Selecting an already-selected run collapses it again - a compact
   * toggle rather than a separate close control. Fetches the full detail
   * (including the persisted result) only once per selection. */
  protected toggleHistoryRun(run: AnalysisRunSummary): void {
    if (this.selectedRunId() === run.run_id) {
      this.selectedRunId.set(null);
      this.selectedRun.set(null);
      return;
    }

    this.selectedRunId.set(run.run_id);
    this.selectedRun.set(null);
    this.selectedRunError.set('');

    this.investigationService.getAnalysisRun(this.eventId, run.run_id).subscribe({
      next: (detail) => this.selectedRun.set(detail),
      error: () => this.selectedRunError.set('Could not load this analysis.'),
    });
  }

  private loadHistory(): void {
    this.historyState.set('loading');
    this.historyErrorMessage.set('');

    this.investigationService.listAnalysisRuns(this.eventId).subscribe({
      next: (runs) => {
        this.history.set(runs);
        this.historyState.set('success');
      },
      error: () => {
        this.historyErrorMessage.set('Could not load previous analyses.');
        this.historyState.set('error');
      },
    });
  }

  private setState(next: AnalysisState): void {
    this.state.set(next);
    this.analysisStateChange.emit(next);
  }
}
