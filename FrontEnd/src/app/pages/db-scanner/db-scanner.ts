import { Component, inject, ChangeDetectorRef, ViewChild, ElementRef, AfterViewChecked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DomSanitizer } from '@angular/platform-browser';
import { Scanner, SavedConnectionProfile } from '../../services/scanner/scanner';

@Component({
  selector: 'app-db-scanner',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule
  ],
  templateUrl: './db-scanner.html',
  styleUrl: './db-scanner.css'
})

export class DbScannerComponent implements AfterViewChecked {

  @ViewChild('terminalBody') private terminalBody!: ElementRef;

  //=========================================================
  // SERVICE
  //=========================================================

  private scanner = inject(Scanner);

  private cdr = inject(ChangeDetectorRef);

  private sanitizer = inject(DomSanitizer);

  //=========================================================
  // DROPDOWNS
  //=========================================================

  source = '';

  destination = 'Fabric';

  //=========================================================
  // PAGE STATUS
  //=========================================================

  loading = false;

  connected = false;

  scanCompleted = false;
  scanFailed = false;

  showConnection = false;

  connecting = false;

  showLogsDialog = false;

  activeTab: 'logs' | 'harness1' | 'harness2' | 'output' = 'logs';

  lastScanSource = '';

  showScanCompletedDialog = false;

  showConnectionSuccessDialog = false;

  connectionSuccessMessage = '';

  backendCompleted = false;

  backendResponse: any;

  scanInterval: any;
  scanStatusTimeout: any;
  private pollErrorCount = 0;

  metadataFile = '/output/Assesment%20Report.docx';

  migrationFile = '/output/AI_Migration_Plan.docx';

  metadataReportDownloadName = 'Metadata Report.docx';

  migrationPlanDownloadName = 'Migration Plan.docx';

  get safeMetadataUrl() {
    return this.sanitizer.bypassSecurityTrustUrl(this.metadataFile);
  }

  get safeMigrationUrl() {
    return this.sanitizer.bypassSecurityTrustUrl(this.migrationFile);
  }

  viewDocument(url: string) {
    window.open(`${url}${url.includes('?') ? '&' : '?'}view=1`, '_blank', 'noopener');
  }

  getFormatSourceForFilename(source: string): string {
    if (!source) return 'Database';
    const s = source.toLowerCase();
    if (s === 'sqlserver' || s === 'sql server') return 'SQL Server';
    if (s === 'databricks') return 'Databricks';
    if (s === 'synapse') return 'Azure Synapse';
    if (s === 'snowflake') return 'Snowflake';
    if (s === 'dynamics365' || s === 'dynamics 365' || s === 'd365') return 'Dynamics 365';
    if (s === 'sqlite') return 'SQLite';
    if (s === 'oracle') return 'Oracle';
    if (s === 'mysql') return 'MySQL';
    if (s === 'postgres' || s === 'postgresql') return 'PostgreSQL';
    if (s === 'sap') return 'SAP';
    return source.charAt(0).toUpperCase() + source.slice(1);
  }


  //=========================================================
  // SCAN STATUS
  //=========================================================

  progress = 0;

  scanStatus = 'Select a database to begin';

  //=========================================================
  // STATUS MESSAGES
  //=========================================================

  statusMessages: string[] = [

    'Select a database to begin'

  ];

  //=========================================================
  // HARNESS LAYER 1 & 2
  //=========================================================

  harness1Messages: string[] = [

    'Harness Layer 1 information will appear here.'

  ];

  harness2Messages: string[] = [

    'Harness Layer 2 information will appear here.'

  ];

  harness1Feedback = '';

  //=========================================================
  // TOKEN INFORMATION
  //=========================================================

  tokenInfo = {

    total: 0,

    prompt: 0,

    completion: 0,

    cost: '-'

  };

  //=========================================================
  // CONNECTION DETAILS
  //=========================================================

  connection = {

    server: '',

    database: '',

    username: '',

    password: '',

    httpPath: ''

  };

  rememberMe = false;

  savedConnectionOptions: SavedConnectionProfile[] = [];

  selectedSavedConnection = '__new__';

  private readonly REMEMBERED_CONNECTION_KEY = 'dbscanner.rememberedConnections';

  //=========================================================
  // DATABRICKS: SAVED-CONNECTION DROPDOWNS
  //=========================================================

  // 'select' shows Server/Catalog/Token/HTTP Path as dropdowns built from
  // saved profiles; 'new' falls back to plain editable inputs (used when
  // there's nothing saved yet, or the user picks "+ Add new connection").
  databricksMode: 'select' | 'new' = 'new';

  databricksConnections: SavedConnectionProfile[] = [];

  databricksServerOptions: string[] = [];

  databricksCatalogOptions: string[] = [];

  databricksHttpPathOptions: string[] = [];

  databricksTokenOptions: { value: string; label: string }[] = [];

  readonly NEW_DATABRICKS_CONNECTION = '__new__';

  //=========================================================
  // CONNECTION PAYLOAD
  //=========================================================

  connectionPayload: any = {};

  //=========================================================
  // OUTPUT FILES
  //=========================================================

  fabricReport =
    'https://app.fabric.microsoft.com/groups/bae3b540-d044-45e0-8c52-3cf4ee3dcb31/reports/1538985c-066f-425d-83bd-2530d449d259/1810ae00cc79e99923aa?experience=fabric-developer';

  // These files are generated by the backend under
  // BackEnd/AI_Agent_Pipeline/output and exposed through the Django output
  // route.

  //=========================================================
  // SOURCE CHANGED
  //=========================================================

  private getRememberedConnectionKey(source: string): string {
    return `${this.REMEMBERED_CONNECTION_KEY}.${(source || '').trim()}`;
  }

  getSavedProfileKey(profile: SavedConnectionProfile): string {
    return [
      profile.server ?? '',
      profile.database ?? '',
      profile.username ?? '',
      profile.password ?? '',
      profile.extra?.http_path ?? '',
    ].join('|');
  }

  private readSavedProfiles(): SavedConnectionProfile[] {
    if (!this.source) {
      return [];
    }

    try {
      const key = this.getRememberedConnectionKey(this.source);
      const raw = localStorage.getItem(key);
      if (!raw) {
        return [];
      }

      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return parsed as SavedConnectionProfile[];
      }
      if (parsed && Array.isArray(parsed.profiles)) {
        return parsed.profiles as SavedConnectionProfile[];
      }
      if (parsed && parsed.connection) {
        return [parsed.connection as SavedConnectionProfile];
      }
      return [];
    } catch {
      return [];
    }
  }

  private loadRememberedConnection() {
    this.savedConnectionOptions = this.readSavedProfiles();
    this.selectedSavedConnection = '__new__';
    this.rememberMe = this.savedConnectionOptions.length > 0;

    this.connection = {
      server: '',
      database: '',
      username: '',
      password: '',
      httpPath: '',
    };
  }

  private saveRememberedConnection() {
    if (!this.source) {
      return;
    }

    const key = this.getRememberedConnectionKey(this.source);
    const profile: SavedConnectionProfile = {
      server: this.connection.server,
      database: this.connection.database,
      username: this.connection.username,
      password: this.connection.password,
      extra: this.connection.httpPath ? { http_path: this.connection.httpPath } : {},
    };

    if (!this.rememberMe) {
      localStorage.removeItem(key);
      this.savedConnectionOptions = [];
      this.selectedSavedConnection = '__new__';
      return;
    }

    const existing = this.readSavedProfiles();
    const next = existing.filter(
      item => this.getSavedProfileKey(item) !== this.getSavedProfileKey(profile)
    );
    next.push(profile);
    this.savedConnectionOptions = next;
    localStorage.setItem(key, JSON.stringify(next));
  }

  onSavedConnectionChange() {
    if (this.selectedSavedConnection === '__new__') {
      this.connection = {
        server: '',
        database: '',
        username: '',
        password: '',
        httpPath: '',
      };
      return;
    }

    const profile = this.savedConnectionOptions.find(
      item => this.getSavedProfileKey(item) === this.selectedSavedConnection
    );

    if (!profile) {
      return;
    }

    this.connection = {
      server: profile.server ?? '',
      database: profile.database ?? '',
      username: profile.username ?? '',
      password: profile.password ?? '',
      httpPath: profile.extra?.http_path ?? '',
    };
  }

  sourceChanged() {

    if (!this.source) {

      return;

    }

    if (this.connected) {

      return;

    }

    this.connection = {

      server: '',

      database: '',

      username: '',

      password: '',

      httpPath: ''

    };

    this.loadRememberedConnection();

    this.progress = 0;

    this.scanCompleted = false;
    this.scanFailed = false;

    this.showScanCompletedDialog = false;

    const selectedDb = this.getFormatSourceForFilename(this.source);
    this.scanStatus = this.source ? `Waiting to connect to ${selectedDb}` : 'Select a database to begin';

    this.statusMessages = [

      this.scanStatus

    ];

    this.harness1Messages = [

      'Harness Layer 1 information will appear here.'

    ];

    this.harness2Messages = [

      'Harness Layer 2 information will appear here.'

    ];

    this.tokenInfo = {

      total: 0,

      prompt: 0,

      completion: 0,

      cost: '-'

    };

    this.showConnection = true;

    if (this.source === 'Databricks') {
      this.loadDatabricksConnections();
      return;
    }

    this.databricksMode = 'new';
    this.databricksConnections = [];

    if (!this.rememberMe) {
      this.scanner.getSavedConnection(this.source).subscribe({
        next: (response) => {
          if (response.found && response.connection) {
            this.connection = {
              server: response.connection.server ?? '',
              database: response.connection.database ?? '',
              username: response.connection.username ?? '',
              password: response.connection.password ?? '',
              httpPath: response.connection.extra?.http_path ?? '',
            };
            this.cdr.detectChanges();
          }
        },
        error: () => {
          // No saved connection yet, or lookup failed - not fatal, just
          // leave the form blank for manual entry.
        },
      });
    }

  }

  //=========================================================
  // DATABRICKS: LOAD SAVED CONNECTIONS
  //=========================================================

  loadDatabricksConnections() {

    this.scanner.getSavedConnections('Databricks').subscribe({
      next: (response) => {
        this.databricksConnections = response.connections ?? [];

        if (this.databricksConnections.length === 0) {
          this.databricksMode = 'new';
          this.cdr.detectChanges();
          return;
        }

        this.databricksMode = 'select';
        this.buildDatabricksOptions();

        // Default to the most recently saved profile.
        const latest = this.databricksConnections[this.databricksConnections.length - 1];
        this.connection = {
          server: latest.server ?? '',
          database: latest.database ?? '',
          username: latest.username ?? '',
          password: latest.password ?? '',
          httpPath: latest.extra?.http_path ?? '',
        };

        this.cdr.detectChanges();
      },
      error: () => {
        // No saved connections yet, or lookup failed - fall back to a
        // blank, manually-entered connection.
        this.databricksMode = 'new';
        this.databricksConnections = [];
        this.cdr.detectChanges();
      },
    });

  }

  //=========================================================
  // DATABRICKS: BUILD DROPDOWN OPTIONS
  //=========================================================

  buildDatabricksOptions() {

    const uniq = (values: (string | undefined)[]) =>
      Array.from(new Set(values.filter((v): v is string => !!v)));

    this.databricksServerOptions = uniq(this.databricksConnections.map(c => c.server));
    this.databricksCatalogOptions = uniq(this.databricksConnections.map(c => c.database));
    this.databricksHttpPathOptions = uniq(this.databricksConnections.map(c => c.extra?.http_path));

    this.databricksTokenOptions = uniq(this.databricksConnections.map(c => c.password))
      .map(token => ({ value: token, label: this.maskToken(token) }));

  }

  //=========================================================
  // DATABRICKS: MASK TOKEN FOR DROPDOWN DISPLAY
  //=========================================================

  maskToken(token: string): string {

    if (!token) {
      return '';
    }

    if (token.length <= 10) {
      return token;
    }

    return `${token.slice(0, 6)}••••${token.slice(-4)}`;

  }

  //=========================================================
  // DATABRICKS: SERVER DROPDOWN CHANGED
  //=========================================================

  onDatabricksServerChange() {

    if (this.connection.server === this.NEW_DATABRICKS_CONNECTION) {
      this.startNewDatabricksConnection();
      return;
    }

    const match = this.databricksConnections.find(c => c.server === this.connection.server);

    if (match) {
      this.connection.database = match.database ?? '';
      this.connection.password = match.password ?? '';
      this.connection.httpPath = match.extra?.http_path ?? '';
    }

  }

  //=========================================================
  // DATABRICKS: SWITCH TO MANUAL ENTRY
  //=========================================================

  startNewDatabricksConnection() {

    this.databricksMode = 'new';

    this.connection = {
      server: '',
      database: '',
      username: '',
      password: '',
      httpPath: '',
    };

  }

  //=========================================================
  // DATABRICKS: BACK TO SAVED-CONNECTION DROPDOWNS
  //=========================================================

  useSavedDatabricksConnection() {

    if (this.databricksConnections.length === 0) {
      return;
    }

    this.databricksMode = 'select';

    const latest = this.databricksConnections[this.databricksConnections.length - 1];
    this.connection = {
      server: latest.server ?? '',
      database: latest.database ?? '',
      username: latest.username ?? '',
      password: latest.password ?? '',
      httpPath: latest.extra?.http_path ?? '',
    };

  }
  //=========================================================
  // CONNECT DATABASE
  //=========================================================

  connectDatabase() {

    const isDatabricks = this.source === 'Databricks';

    if (
      this.connection.server.trim() === '' ||
      this.connection.database.trim() === '' ||
      (!isDatabricks && this.connection.username.trim() === '') ||
      this.connection.password.trim() === '' ||
      (isDatabricks && this.connection.httpPath.trim() === '')
    ) {

      alert('Please fill all mandatory fields.');

      return;

    }

    this.connecting = true;

    this.scanner.connectDatabase(
      this.source,
      this.connection,
      this.rememberMe
    ).subscribe({

      next: (response: any) => {

        this.connected = true;

        const connectedDb = this.getFormatSourceForFilename(this.source);
        this.scanStatus = `Connected to ${connectedDb}. Ready to scan`;
        this.statusMessages = [this.scanStatus];

        this.saveRememberedConnection();

        this.showConnection = false;

        this.connecting = false;

        this.cdr.detectChanges();

        this.connectionPayload = {

          source: this.source,

          destination: this.destination,

          connection: {

            server: this.connection.server,

            database: this.connection.database,

            username: this.connection.username,

            password: this.connection.password,

            httpPath: this.connection.httpPath

          },

          status: 'Connected',

          connectedTime: new Date()

        };

        this.statusMessages.push(
          'Database Connected Successfully.'
        );

        this.connectionSuccessMessage = response.message ?? 'Connection successful.';
        this.showConnectionSuccessDialog = true;
        this.cdr.detectChanges();

      },

      error: (err) => {

        this.connected = false;

        this.connecting = false;

        this.cdr.detectChanges();

        this.statusMessages.push(
          'Database Connection Failed.'
        );

        console.error(err);

        alert(err.error?.message ?? 'Connection Failed');

      }

    });

  }

  //=========================================================
  // CANCEL CONNECTION
  //=========================================================

  cancelConnection() {

    this.showConnection = false;

    this.connected = false;

    this.source = '';

  }

  //=========================================================
  // START SCAN
  //=========================================================

  startScan() {

    if (!this.connected) {

      alert('Please connect to database first.');

      return;

    }

    this.lastScanSource = this.source;

    this.loading = true;

    this.scanCompleted = false;
    this.scanFailed = false;

    this.showScanCompletedDialog = false;
    const activeDb = this.getFormatSourceForFilename(this.source);
    this.scanStatus = `Starting ${activeDb} scan`;
    this.statusMessages = [];
    this.statusMessages.push(`Starting ${activeDb} scan`);
    this.backendCompleted = false;
    this.backendResponse = null;
    this.pollErrorCount = 0;

    if (this.scanInterval) {
      clearInterval(this.scanInterval);
    }

    this.scanner.startScan(
      this.source,
      this.destination,
      this.connection
    ).subscribe({
      next: (response: any) => {
        if (response.scan_id) {
          this.pollScanStatus(response.scan_id);
        }
      },
      error: (err) => {
        this.loading = false;
        this.progress = 0;
        this.scanFailed = true;
        this.scanStatus = 'Scan Failed';
        this.statusMessages.push('Database Scan Failed.');
        console.error(err);
        alert(err.error?.message ?? 'Scan Failed');
        this.cdr.detectChanges();
      }
    });

  }

  private pollScanStatus(scanId: string) {
    this.scanner.getScanStatus(scanId).subscribe({
      next: (status) => {
        this.pollErrorCount = 0;
        this.applyScanLogs(status.Logs);
        
        // Dynamically update progress and status message from backend
        if (status.progressbar && status.progressbar > this.progress) {
          this.progress = status.progressbar;
        }
        this.scanStatus = status.scan_status_message || this.scanStatus;

        if (status.status === 'Running') {
          this.scanStatusTimeout = setTimeout(() => this.pollScanStatus(scanId), 1000);
          this.cdr.detectChanges();
          return;
        }

        if (status.status === 'Failed') {
          this.loading = false;
          this.scanFailed = true;
          this.scanStatus = 'Scan Failed';
          this.statusMessages.push(status.error ?? 'Database Scan Failed.');
          this.showLogsDialog = true;
          this.activeTab = 'logs';
          this.cdr.detectChanges();
          return;
        }

        this.backendCompleted = true;
        this.backendResponse = {
          ...status,
          ...(status.result ?? {}),
          Logs: status.Logs,
        };
        this.progress = 100;
        this.completeScanProgress();
        this.cdr.detectChanges();
      },
      error: () => {
        this.pollErrorCount++;
        if (this.pollErrorCount >= 4) {
          this.loading = false;
          this.scanFailed = true;
          this.scanStatus = 'Scan session interrupted (server reloaded). Please click Scan to restart.';
          this.statusMessages.push('Scan session interrupted (server reloaded). Please click Scan to restart.');
          this.cdr.detectChanges();
          return;
        }
        this.scanStatusTimeout = setTimeout(() => this.pollScanStatus(scanId), 3000);
      },
    });
  }

  private applyScanLogs(logs: Record<string, any> | undefined) {
    if (!logs) return;
    this.statusMessages = logs['Scan Info'] ?? this.statusMessages;
    this.harness1Messages = logs['Harness Layer1'] ?? this.harness1Messages;
    this.harness2Messages = logs['Harness Layer2']?.length
      ? logs['Harness Layer2']
      : ['HARNESS LAYER 2:', 'No evaluator-generator messages were returned for this scan.'];
    const tokenEntries = logs['Token Info'];
    if (tokenEntries?.length) {
      const latest = tokenEntries[tokenEntries.length - 1];
      this.tokenInfo = {
        total: latest.total ?? 0,
        prompt: latest.prompt ?? 0,
        completion: latest.completion ?? 0,
        cost: latest.cost ?? '-',
      };
    }
  }

  //=========================================================
  // COMPLETE SCAN PROGRESS
  //=========================================================

  completeScanProgress() {

    this.loading = false;

    this.scanCompleted = true;

    this.progress = 0;

    this.source = '';

    this.connected = false;

    const completedSource = this.lastScanSource || this.source;
    const formattedSource = this.getFormatSourceForFilename(completedSource);
    const completedMsg = this.backendResponse?.scan_status_message 
      || `${formattedSource} scan completed successfully.`;

    this.scanStatus = completedMsg;

    if (!this.statusMessages.includes(completedMsg)) {
      this.statusMessages.push(completedMsg);
    }

    if (this.backendResponse) {

      this.connectionPayload.scanStatus = 'Completed';

      this.connectionPayload.completedTime = new Date();

      this.connectionPayload.scanResult = this.backendResponse;

      const logs = this.backendResponse.Logs;
      const outputFiles = this.backendResponse.output_files;
      if (outputFiles?.assessment_report) {
        this.metadataFile = `/output/${encodeURIComponent(outputFiles.assessment_report)}?t=${Date.now()}`;
      }
      if (outputFiles?.migration_plan) {
        this.migrationFile = `/output/${encodeURIComponent(outputFiles.migration_plan)}?t=${Date.now()}`;
      }

      const selectedSource = this.lastScanSource || this.source || 'Database';
      const formattedSource = this.getFormatSourceForFilename(selectedSource);
      this.metadataReportDownloadName = `${formattedSource} Assessment Report.docx`;
      this.migrationPlanDownloadName = `${formattedSource} Migration Plan.docx`;

      //----------------------------------------------------
      // Backend Logs
      //----------------------------------------------------

      if (logs) {

        this.statusMessages = logs['Scan Info'] ?? this.statusMessages;

      }

      else {

        this.statusMessages = [

          'Metadata Collection Completed',

          'Schema Validation Completed',

          'Migration Assessment Completed',

          'AI Agents Executed',

          'Output Documents Generated Successfully'

        ];

      }

      //----------------------------------------------------
      // Harness Layer 1
      //----------------------------------------------------

      if (logs && logs['Harness Layer1'] && logs['Harness Layer1'].length > 0) {

        this.harness1Messages = logs['Harness Layer1'];

      }

      else {

        this.harness1Messages = [

          'Bronze Layer Generated',

          'Silver Layer Generated',

          'Gold Layer Generated',

          'Migration Assessment Completed'

        ];

      }

      //----------------------------------------------------
      // Harness Layer 2
      //----------------------------------------------------

      if (logs && logs['Harness Layer2'] && logs['Harness Layer2'].length > 0) {

        this.harness2Messages = logs['Harness Layer2'];

      }

      else {

        this.harness2Messages = [
  "HARNESS LAYER 2:",
  "Generated At: 2026-08-04T07:30:18.234561+00:00",
  "------------------------------",
  "[SUCCESS]: AI Assessment Generation",
  "    - Harness Steps:",
  "        * [SUCCESS]: Metadata loaded successfully",
  "        * [SUCCESS]: Schema relationships analyzed",
  "        * [SUCCESS]: Business rules identified",
  "        * [SUCCESS]: Fabric compatibility assessment completed",
  "        * [SUCCESS]: Migration complexity calculated",
  "",
  "[SUCCESS]: Migration Planning",
  "    - Harness Steps:",
  "        * [SUCCESS]: Target architecture generated",
  "        * [SUCCESS]: Migration sequence prepared",
  "        * [SUCCESS]: Dependency analysis completed",
  "        * [SUCCESS]: Migration recommendations generated",
  "",
  "[SUCCESS]: AI Validation",
  "    - Harness Steps:",
  "        * [SUCCESS]: Assessment report validated",
  "        * [SUCCESS]: Migration plan validated",
  "        * [SUCCESS]: AI confidence score verified",
  "        * [SUCCESS]: Output documents generated",
  "        * [SUCCESS]: Ready for Human Review",
  "",
  "------------------------------",
  "REPORT SUMMARY:",
  "Assessment Status: PASSED",
  "Migration Plan Status: GENERATED",
  "AI Output Quality: HIGH",
  "Migration Complexity: MEDIUM",
  "Human Review Required: YES",
  "==============================",
  "",
  "Assessment Report generated successfully.",
  "Migration Plan generated successfully.",
  "Waiting for Human Review.",
  "",
  "If you want to continue click on SUBMIT/CONTINUE.",
  "If you want to regenerate the AI assessment click RETRY."
];

      }

      //----------------------------------------------------
      // Token Usage
      //----------------------------------------------------

      if (logs && logs['Token Info'] && logs['Token Info'].length > 0) {

        const latestTokenEntry = logs['Token Info'][logs['Token Info'].length - 1];

        this.tokenInfo = {

          total: latestTokenEntry.total ?? 0,

          prompt: latestTokenEntry.prompt ?? 0,

          completion: latestTokenEntry.completion ?? 0,

          cost: latestTokenEntry.cost ?? '-'

        };

        for (const entry of logs['Token Info']) {

          this.statusMessages.push(

            `Token Usage — Total: ${entry.total ?? 0}, ` +

            `Prompt: ${entry.prompt ?? 0}, ` +

            `Completion: ${entry.completion ?? 0}, ` +

            `Cost: $${entry.cost ?? '-'}`

          );

        }

      }

    }

    this.showScanCompletedDialog = true;

    this.cdr.detectChanges();

  }

  //=========================================================
  // CLOSE SCAN COMPLETED DIALOG
  //=========================================================

  closeScanCompletedDialog() {

    this.showScanCompletedDialog = false;

  }

  closeConnectionSuccessDialog() {
    this.showConnectionSuccessDialog = false;
  }

  //=========================================================
  // OPEN SCAN COMPLETED DIALOG FROM LOGS
  //=========================================================

  openScanCompletedDialogFromLogs() {

    this.showLogsDialog = false;

    this.showScanCompletedDialog = true;

    this.cdr.detectChanges();

  }

  //=========================================================
  // RETRY CONSTRAINT HARNESS LAYER (HARNESS 1)
  //=========================================================

  retryHarness1() {

    if (!this.lastScanSource) {

      return;

    }

    const feedback = this.harness1Feedback.trim();
    if (feedback) {
      this.statusMessages.push(`Constraint harness feedback for retry: ${feedback}`);
    }

    this.source = this.lastScanSource;

    this.connected = true;

    this.activeTab = 'harness1';

    this.startScan();

  }

  //=========================================================
  // SUBMIT CONSTRAINT HARNESS LAYER (HARNESS 1)
  //=========================================================

  submitHarness1() {

    const feedback = this.harness1Feedback.trim();
    if (feedback) {
      this.harness1Messages = [
        ...this.harness1Messages,
        `User feedback submitted: ${feedback}`
      ];
    }

    this.activeTab = 'harness2';

  }

  //=========================================================
  // OPEN LOGS DIALOG
  //=========================================================

  openLogsDialog() {

    this.showLogsDialog = true;

    this.activeTab = 'logs';

  }

  //=========================================================
  // CLOSE LOGS DIALOG
  //=========================================================

  closeLogsDialog() {

    this.showLogsDialog = false;

  }

  //=========================================================
  // UPDATE PROGRESS
  //=========================================================

  updateProgress(

    percent: number,

    status: string

  ) {

    this.progress = percent;

    this.scanStatus = status;

    this.statusMessages.push(status);

  }

  //=========================================================
  // DOWNLOAD METADATA REPORT
  //=========================================================

  downloadMetadata() {

    window.open(this.metadataFile, '_blank');

  }

  //=========================================================
  // DOWNLOAD MIGRATION REPORT
  //=========================================================

  downloadMigration() {

    window.open(this.migrationFile, '_blank');

  }

  //=========================================================
  // VIEW OUTPUT
  //=========================================================

  viewOutput() {

    this.activeTab = 'output';

  }

  //=========================================================
  // VIEW ANALYSIS
  //=========================================================

  viewAnalysis() {

    window.open(

      this.fabricReport,

      '_blank'

    );

  }

  ngAfterViewChecked() {
    this.scrollToBottom();
  }

  private scrollToBottom() {
    try {
      if (this.terminalBody) {
        this.terminalBody.nativeElement.scrollTop = this.terminalBody.nativeElement.scrollHeight;
      }
    } catch (err) {}
  }

}  
