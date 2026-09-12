import { HttpErrorResponse } from '@angular/common/http';
import {
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  inject,
  signal,
} from '@angular/core';

import { InvestigationService } from '../../../core/services/investigation.service';
import { InvestigationAnalysis } from '../../../models/investigation.models';
import { humanizeEventType } from '../../../shared/format-metadata';

export type AnalysisState = 'idle' | 'loading' | 'success' | 'unavailable' | 'error';

@Component({
  selector: 'app-ai-analysis',
  templateUrl: './ai-analysis.component.html',
  styleUrl: './ai-analysis.component.css',
})
export class AiAnalysisComponent implements OnChanges {
  @Input({ required: true }) eventId!: number;

  /** Lets a parent (e.g. the investigation header) surface AI analysis
   * status without duplicating this component's own request/state logic -
   * emitted on every state transition, never polled or inferred. */
  @Output() readonly analysisStateChange = new EventEmitter<AnalysisState>();

  private readonly investigationService = inject(InvestigationService);

  protected readonly state = signal<AnalysisState>('idle');
  protected readonly analysis = signal<InvestigationAnalysis | null>(null);
  protected readonly errorMessage = signal('');

  ngOnChanges(changes: SimpleChanges): void {
    // A new investigation target means any previous analysis is stale.
    if (changes['eventId'] && !changes['eventId'].firstChange) {
      this.analysis.set(null);
      this.errorMessage.set('');
      this.setState('idle');
    }
  }

  protected labelFor(eventType: string): string {
    return humanizeEventType(eventType);
  }

  analyze(): void {
    this.setState('loading');
    this.errorMessage.set('');

    this.investigationService.analyzeInvestigation(this.eventId).subscribe({
      next: (result) => {
        this.analysis.set(result);
        this.setState('success');
      },
      error: (err: HttpErrorResponse) => {
        if (err.status === 503) {
          this.errorMessage.set(
            (err.error && err.error.detail) || 'AI analysis is not available right now.',
          );
          this.setState('unavailable');
        } else {
          this.errorMessage.set('Could not generate an AI analysis. Please try again.');
          this.setState('error');
        }
      },
    });
  }

  private setState(next: AnalysisState): void {
    this.state.set(next);
    this.analysisStateChange.emit(next);
  }
}
