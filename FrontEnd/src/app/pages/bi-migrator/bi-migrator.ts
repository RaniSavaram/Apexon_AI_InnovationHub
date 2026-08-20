import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-bi-migrator',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule
  ],
  templateUrl: './bi-migrator.html',
  styleUrl: './bi-migrator.css'
})
export class BiMigratorComponent {

  // ===========================
  // Dropdown Values
  // ===========================

  selectedSource = '';

  destination = 'Power BI';

  // Display value
  displaySource = 'Source';

  // ===========================
  // Migration Status
  // ===========================

  migrating = false;

  migrationCompleted = false;

  // ===========================
  // JSON Payload
  // Backend team can use this
  // ===========================

  migrationPayload: any = {};

  // ===========================
  // Migration Summary
  // ===========================

  migrationSummary = {

    reports: 5,

    dashboards: 2,

    visuals: 82,

    daxObjects: 116,

    datasets: 4,

    migrationTime: '01m 32s'

  };

  // ===========================
  // URLs
  // ===========================

  sharePointUrl =
    'https://isplahd.sharepoint.com/sites/DBScanner/Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2FDBScanner%2FShared%20Documents%2FDBScanner';

  reportUrl =
    'https://app.fabric.microsoft.com';

  // ===========================
  // Start Migration
  // ===========================

  startMigration() {

    if (this.selectedSource === '') {

      alert("Please select a Source Platform.");

      return;

    }

    this.displaySource = this.selectedSource;

    this.migrating = true;

    this.migrationCompleted = false;

    // JSON sent to Backend

    this.migrationPayload = {

      sourcePlatform: this.selectedSource,

      destinationPlatform: this.destination,

      migrationType: "BI Modernization",

      projectName: "Apexon AI Innovation Hub",

      status: "Running",

      requestedBy: "Current User",

      requestedTime: new Date(),

      summary: null

    };

    console.log("Migration Request");

    console.log(this.migrationPayload);

    setTimeout(() => {

      this.migrating = false;

      this.migrationCompleted = true;

      this.migrationPayload.status = "Completed";

      this.migrationPayload.completedTime = new Date();

      this.migrationPayload.summary = this.migrationSummary;

      console.log("Completed Payload");

      console.log(this.migrationPayload);

      alert("Migration Completed Successfully.");

    }, 4000);

  }

  // ===========================
  // Output
  // ===========================

  viewOutput() {

    window.open(this.sharePointUrl, '_blank');

  }

  // ===========================
  // Analysis
  // ===========================

  viewAnalysis() {

    window.open(this.reportUrl, '_blank');

  }

}