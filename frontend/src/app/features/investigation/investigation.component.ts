import { DatePipe, Location } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { InvestigationService } from '../../core/services/investigation.service';
import { Investigation } from '../../models/investigation.models';
import { humanizeEventType } from '../../shared/format-metadata';
import { AiAnalysisComponent, AnalysisState } from './ai-analysis/ai-analysis.component';
import { CandidatesComponent } from './candidates/candidates.component';
import { EvidenceComponent } from './evidence/evidence.component';
import { RecentEventsComponent } from './recent-events/recent-events.component';
import { TimelineComponent } from './timeline/timeline.component';

const EVENT_QUERY_PARAM = 'event';

type LoadState = 'idle' | 'loading' | 'success' | 'not-found' | 'error';

const AI_ANALYSIS_STATUS_LABELS: Record<AnalysisState, string> = {
  idle: 'Not yet requested',
  loading: 'Analyzing…',
  success: 'Complete',
  unavailable: 'Unavailable',
  error: 'Failed',
};

@Component({
  selector: 'app-investigation',
  imports: [
    FormsModule,
    DatePipe,
    TimelineComponent,
    EvidenceComponent,
    CandidatesComponent,
    AiAnalysisComponent,
    RecentEventsComponent,
  ],
  templateUrl: './investigation.component.html',
  styleUrl: './investigation.component.css',
})
export class InvestigationComponent implements OnInit {
  private readonly investigationService = inject(InvestigationService);
  private readonly location = inject(Location);

  // Bound via [(ngModel)] on an <input type="number">, so Angular's
  // NumberValueAccessor writes back an actual `number` (or `null` when
  // empty) at runtime - never trust this as a plain string.
  protected eventIdInput: string | number | null = '';
  protected readonly state = signal<LoadState>('idle');
  protected readonly investigation = signal<Investigation | null>(null);
  protected readonly errorMessage = signal('');
  protected readonly requestedEventId = signal<number | null>(null);
  protected readonly aiAnalysisState = signal<AnalysisState>('idle');

  /** `?event=<id>` opens that investigation directly, so an investigation
   * can be linked to and survives a reload. Event IDs are not secrets; the
   * operator key never goes anywhere near the URL. */
  ngOnInit(): void {
    const query = this.location.path().split('?')[1] ?? '';
    const linked = new URLSearchParams(query).get(EVENT_QUERY_PARAM);
    if (linked !== null) {
      this.openEvent(Number(linked));
    }
  }

  /** Opens an investigation chosen from the recent-events list or the
   * timeline - the same path as typing the ID and pressing Investigate. */
  protected openEvent(eventId: number): void {
    this.eventIdInput = eventId;
    this.investigate();
  }

  protected eventTypeLabel(eventType: string): string {
    return humanizeEventType(eventType);
  }

  protected aiAnalysisStatusLabel(): string {
    return AI_ANALYSIS_STATUS_LABELS[this.aiAnalysisState()];
  }

  protected onAiAnalysisStateChange(next: AnalysisState): void {
    this.aiAnalysisState.set(next);
  }

  protected investigate(): void {
    const raw = String(this.eventIdInput ?? '').trim();
    if (raw.length === 0) {
      this.state.set('error');
      this.errorMessage.set('Enter an event ID.');
      return;
    }

    const eventId = Number(raw);
    if (!Number.isInteger(eventId) || eventId <= 0) {
      this.state.set('error');
      this.errorMessage.set('Event ID must be a positive whole number.');
      return;
    }

    this.requestedEventId.set(eventId);
    this.aiAnalysisState.set('idle');
    this.state.set('loading');
    this.investigation.set(null);

    this.investigationService.getInvestigation(eventId).subscribe({
      next: (result) => {
        this.investigation.set(result);
        this.state.set('success');
        this.location.replaceState(
          this.location.path().split('?')[0] || '/',
          `${EVENT_QUERY_PARAM}=${eventId}`,
        );
      },
      error: (err: HttpErrorResponse) => {
        if (err.status === 404) {
          this.state.set('not-found');
        } else {
          this.errorMessage.set('Could not load the investigation. Please try again.');
          this.state.set('error');
        }
      },
    });
  }

  /** Re-runs the same lookup after a failure - the event ID input already
   * holds the value that was submitted, so retrying is just investigating
   * again rather than a distinct code path. */
  protected retry(): void {
    this.investigate();
  }
}
