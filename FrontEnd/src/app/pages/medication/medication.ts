import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-medication',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './medication.html',
  styleUrl: './medication.css'
})
export class MedicationComponent {

  pptUrl =
    'https://isplahd-my.sharepoint.com/:p:/g/personal/rani_savaram_apexon_com/IQBF4F-DkmKdRLz4wlGDis5VASUOYpXkCo1nRQhRsYJDCNg?wdExp=TEAMS-TREATMENT&web=1&TeamsCID=9e0c9148-e3d0-4a51-b143-0a6239edd33b';

  demoUrl =
    'https://app.fabric.microsoft.com/groups/bae3b540-d044-45e0-8c52-3cf4ee3dcb31/reports/cd6748c7-3d7f-4fe4-913a-faf36e3817de/fc7776b3d979b2e37379?experience=fabric-developer';

  viewPPT() {

    window.open(this.pptUrl, '_blank');

  }

  viewDemo() {

    window.open(this.demoUrl, '_blank');

  }

}