import { Component } from '@angular/core';

import { InvestigationComponent } from './features/investigation/investigation.component';

@Component({
  selector: 'app-root',
  imports: [InvestigationComponent],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {}
