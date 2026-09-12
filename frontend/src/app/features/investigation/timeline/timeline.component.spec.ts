import { TestBed } from '@angular/core/testing';

import { NextraceEvent } from '../../../models/investigation.models';
import { TimelineComponent } from './timeline.component';

function makeEvent(overrides: Partial<NextraceEvent>): NextraceEvent {
  return {
    id: 1,
    service: 'payment-service',
    environment: 'production',
    event_type: 'deployment',
    timestamp: '2026-09-06T10:00:00Z',
    severity: 'info',
    source: 'github',
    message: 'Version 1.4.2 deployed to production',
    metadata: null,
    created_at: '2026-09-06T10:00:01Z',
    ...overrides,
  };
}

describe('TimelineComponent', () => {
  it('renders one row per event with a human-readable event type', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('events', [
      makeEvent({ id: 1, event_type: 'deployment', message: 'Deployed' }),
      makeEvent({ id: 2, event_type: 'error_spike', message: 'Errors spiked' }),
    ]);
    fixture.componentRef.setInput('targetEventId', 2);
    fixture.detectChanges();

    const rows = fixture.nativeElement.querySelectorAll('.timeline-row');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Deployment');
    expect(rows[0].textContent).toContain('Deployed');
    expect(rows[1].textContent).toContain('Error Spike');
  });

  it('marks the target event distinctly as the investigation target', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('events', [
      makeEvent({ id: 1 }),
      makeEvent({ id: 2, event_type: 'incident' }),
    ]);
    fixture.componentRef.setInput('targetEventId', 2);
    fixture.detectChanges();

    const rows = fixture.nativeElement.querySelectorAll('.timeline-row');
    expect(rows[0].querySelector('.badge--target')).toBeNull();
    const targetBadge = rows[1].querySelector('.badge--target');
    expect(targetBadge).not.toBeNull();
    expect(targetBadge.textContent).toContain('Investigation Target');
  });

  it('exposes each row as a scroll anchor addressable by event id', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('events', [makeEvent({ id: 24, event_type: 'error_spike' })]);
    fixture.componentRef.setInput('targetEventId', 24);
    fixture.detectChanges();

    const row = fixture.nativeElement.querySelector('.timeline-row');
    expect(row.id).toBe('event-24');
  });

  it('shows severity and source for each event', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('events', [
      makeEvent({ id: 1, severity: 'critical', source: 'application' }),
    ]);
    fixture.componentRef.setInput('targetEventId', 1);
    fixture.detectChanges();

    const row = fixture.nativeElement.querySelector('.timeline-row');
    expect(row.textContent).toContain('critical');
    expect(row.textContent).toContain('Source: application');
  });

  it('renders metadata as readable key/value rows behind an expandable toggle', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('events', [
      makeEvent({
        id: 1,
        metadata: { previous_value: 20, new_value: 5 },
      }),
    ]);
    fixture.componentRef.setInput('targetEventId', 1);
    fixture.detectChanges();

    const toggle = fixture.nativeElement.querySelector('.metadata-toggle summary');
    expect(toggle).not.toBeNull();
    expect(toggle.textContent).toContain('View metadata');

    const rows = fixture.nativeElement.querySelectorAll('.metadata-row');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Previous Value');
    expect(rows[0].textContent).toContain('20');
    expect(rows[1].textContent).toContain('New Value');
    expect(rows[1].textContent).toContain('5');

    // Never dump the raw metadata blob as a single JSON string.
    expect(fixture.nativeElement.textContent).not.toContain('{"previous_value":20');
  });

  it('omits the metadata toggle when an event has no metadata', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('events', [makeEvent({ id: 1, metadata: null })]);
    fixture.componentRef.setInput('targetEventId', 1);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.metadata-toggle')).toBeNull();
  });

  it('shows an empty state when there are no events', () => {
    const fixture = TestBed.createComponent(TimelineComponent);
    fixture.componentRef.setInput('events', []);
    fixture.componentRef.setInput('targetEventId', 1);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.empty-state')).not.toBeNull();
    expect(fixture.nativeElement.querySelectorAll('.timeline-row').length).toBe(0);
  });
});
