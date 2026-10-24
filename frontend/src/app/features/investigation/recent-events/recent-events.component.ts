import { DatePipe } from '@angular/common';
import { Component, EventEmitter, Input, OnInit, Output, inject, signal } from '@angular/core';

import { InvestigationService } from '../../../core/services/investigation.service';
import { NextraceEvent } from '../../../models/investigation.models';
import { humanizeEventType, isDemoEvent } from '../../../shared/format-metadata';

type ListState = 'loading' | 'success' | 'error';

/** Recent events from GET /events, newest first. Selecting one opens its
 * investigation - so finding an incident never requires knowing its ID. */
@Component({
  selector: 'app-recent-events',
  imports: [DatePipe],
  templateUrl: './recent-events.component.html',
  styleUrl: './recent-events.component.css',
})
export class RecentEventsComponent implements OnInit {
  /** Open by default; the parent collapses it while an investigation is
   * shown so the results stay at the top of the page. */
  @Input() expanded = true;
  /** Highlights the event whose investigation is currently open. */
  @Input() selectedEventId: number | null = null;
  @Output() readonly eventSelected = new EventEmitter<number>();

  private readonly investigationService = inject(InvestigationService);

  protected readonly state = signal<ListState>('loading');
  protected readonly events = signal<NextraceEvent[]>([]);
  protected readonly hideDemo = signal(false);

  ngOnInit(): void {
    this.load();
  }

  protected labelFor(eventType: string): string {
    return humanizeEventType(eventType);
  }

  protected isDemo(event: NextraceEvent): boolean {
    return isDemoEvent(event.metadata);
  }

  protected toggleHideDemo(hide: boolean): void {
    this.hideDemo.set(hide);
    this.load();
  }

  protected select(event: NextraceEvent): void {
    this.eventSelected.emit(event.id);
  }

  load(): void {
    this.state.set('loading');
    this.investigationService.listRecentEvents({ excludeDemo: this.hideDemo() }).subscribe({
      next: (events) => {
        this.events.set(events);
        this.state.set('success');
      },
      error: () => this.state.set('error'),
    });
  }
}
