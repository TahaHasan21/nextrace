import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';

import { InvestigationService } from '../../../core/services/investigation.service';
import { NextraceEvent } from '../../../models/investigation.models';
import { RecentEventsComponent } from './recent-events.component';

function makeEvent(overrides: Partial<NextraceEvent>): NextraceEvent {
  return {
    id: 1,
    service: 'checkout-api',
    environment: 'staging',
    event_type: 'error_spike',
    timestamp: '2026-10-01T12:01:40Z',
    severity: 'critical',
    source: 'application',
    message: 'HTTP 5xx rate above threshold',
    metadata: null,
    created_at: '2026-10-01T12:01:41Z',
    ...overrides,
  };
}

class FakeService {
  calls: { excludeDemo?: boolean }[] = [];
  next: () => Observable<NextraceEvent[]> = () => of([]);

  listRecentEvents(options: { excludeDemo?: boolean }): Observable<NextraceEvent[]> {
    this.calls.push(options);
    return this.next();
  }
}

describe('RecentEventsComponent', () => {
  let service: FakeService;

  beforeEach(() => {
    service = new FakeService();
    TestBed.configureTestingModule({
      imports: [RecentEventsComponent],
      providers: [{ provide: InvestigationService, useValue: service }],
    });
  });

  function render() {
    const fixture = TestBed.createComponent(RecentEventsComponent);
    fixture.detectChanges();
    return fixture;
  }

  it('lists real events newest first, as returned by the API, and emits the selected id', () => {
    service.next = () =>
      of([makeEvent({ id: 9 }), makeEvent({ id: 8, event_type: 'deployment', severity: 'info' })]);
    const fixture = render();
    const selected: number[] = [];
    fixture.componentInstance.eventSelected.subscribe((id) => selected.push(id));

    const items = fixture.nativeElement.querySelectorAll('.recent-item');
    expect(items.length).toBe(2);
    expect(items[0].textContent).toContain('Error Spike');
    expect(items[0].textContent).toContain('checkout-api');
    expect(items[0].textContent).toContain('Oct 1, 12:01:40');
    expect(items[1].textContent).toContain('#8');

    items[1].click();
    expect(selected).toEqual([8]);
  });

  it('marks seeded demo events and can hide them via the API filter', () => {
    service.next = () => of([makeEvent({ metadata: { demo_scenario: 'payment-incident-v1' } })]);
    const fixture = render();
    expect(fixture.nativeElement.querySelector('.badge--demo')).not.toBeNull();

    service.next = () => of([]);
    const toggle: HTMLInputElement = fixture.nativeElement.querySelector('.demo-toggle input');
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change'));
    fixture.detectChanges();

    expect(service.calls).toEqual([{ excludeDemo: false }, { excludeDemo: true }]);
  });

  it('explains how to get events when there are none', () => {
    const fixture = render();

    expect(fixture.nativeElement.textContent).toContain('No events yet');
  });

  it('shows an error with a working retry when the API fails', () => {
    service.next = () => throwError(() => new Error('offline'));
    const fixture = render();
    expect(fixture.nativeElement.textContent).toContain('Could not load recent events');

    service.next = () => of([makeEvent({})]);
    fixture.nativeElement.querySelector('.retry-button').click();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelectorAll('.recent-item').length).toBe(1);
  });

  it('highlights the event whose investigation is open', () => {
    service.next = () => of([makeEvent({ id: 1 }), makeEvent({ id: 2 })]);
    const fixture = TestBed.createComponent(RecentEventsComponent);
    fixture.componentRef.setInput('selectedEventId', 2);
    fixture.detectChanges();

    const items = fixture.nativeElement.querySelectorAll('.recent-item');
    expect(items[0].getAttribute('aria-current')).toBeNull();
    expect(items[1].getAttribute('aria-current')).toBe('true');
  });
});
