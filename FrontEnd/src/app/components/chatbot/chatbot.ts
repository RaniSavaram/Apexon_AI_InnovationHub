import { Component, ElementRef, ViewChild, AfterViewChecked, OnInit, OnDestroy, ChangeDetectorRef, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Scanner, ConnectionDetails, ScanStatus } from '../../services/scanner/scanner';

export interface ChatAction {
  label: string;
  route?: string;
  prompt?: string;
  icon?: string;
  actionKey?: string;
  variant?: 'primary' | 'secondary' | 'success' | 'danger';
}

export interface ChatMessage {
  id: string;
  sender: 'user' | 'bot';
  text: string;
  formattedText: string;
  timestamp: Date;
  actions?: ChatAction[];
  chips?: string[];
  type?: 'text' | 'db-form' | 'db-progress' | 'artifact-prompt' | 'artifact-result';
  metadata?: any;
}

export interface QuickPrompt {
  label: string;
  query: string;
  icon: string;
}

const SESSION_HISTORY_KEY = 'apexon_hub_chat_history';
const SESSION_STATE_KEY = 'apexon_hub_chat_state';

@Component({
  selector: 'app-chatbot',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './chatbot.html',
  styleUrl: './chatbot.css'
})
export class ChatbotComponent implements OnInit, OnDestroy, AfterViewChecked {
  @ViewChild('chatBody') private chatBodyRef!: ElementRef;

  isOpen = false;
  isTyping = false;
  userInput = '';
  hasUnreadNotification = true;
  private shouldScroll = false;

  // DB Scan Interactive State (Zero credentials caching/remembering)
  showDbScanForm = false;
  isDbConnecting = false;
  isDbScanning = false;
  isGeneratingArtifacts = false;
  showPassword = false;

  activeScanId: string | null = null;
  activeScanSource = 'SQL Server';
  private scanPollInterval: any = null;

  // Dynamic Progress Bar
  dbScanProgress = 0;
  dbDisplayProgress = 0;
  dbTargetProgress = 0;
  private progressAnimationId: any = null;

  dbScanStatusMessage = 'Initiating scan...';
  dbScanLogs: string[] = [];
  dbTokenInfo = { total: 0, prompt: 0, completion: 0, cost: '-' };
  activeProgressTab: 'progress' | 'logs' | 'tokens' = 'progress';

  databaseSources: string[] = [
    'SQL Server',
    'Azure Synapse',
    'Snowflake',
    'Databricks',
    'Dynamics 365',
    'Oracle',
    'MySQL',
    'PostgreSQL'
  ];

  // Pure in-memory fresh connection (Never stored or remembered)
  dbConnection: ConnectionDetails = {
    server: '',
    database: '',
    username: '',
    password: '',
    httpPath: ''
  };

  quickPrompts: QuickPrompt[] = [
    {
      label: 'Start New DB Scan',
      query: 'Start New DB Scan',
      icon: 'bi bi-play-circle-fill'
    },
    {
      label: 'Medication Adherence',
      query: 'Tell me about Medication Adherence',
      icon: 'bi bi-capsule'
    },
    {
      label: 'Database Scanner Info',
      query: 'What is Database Scanner & Migration Readiness?',
      icon: 'bi bi-database-check'
    },
    {
      label: 'BI Migrator',
      query: 'How does BI Migrator modernize reports?',
      icon: 'bi bi-bar-chart-line-fill'
    },
    {
      label: 'Microsoft Fabric',
      query: 'What Azure and Microsoft Fabric capabilities are used?',
      icon: 'bi bi-cloud-arrow-up-fill'
    }
  ];

  messages: ChatMessage[] = [];

  constructor(
    private router: Router,
    private scanner: Scanner,
    private cdr: ChangeDetectorRef,
    private ngZone: NgZone
  ) {}

  ngOnInit(): void {
    this.restoreSession();
  }

  ngOnDestroy(): void {
    this.clearScanPoll();
    this.stopProgressAnimation();
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll) {
      this.scrollToBottom();
      this.shouldScroll = false;
    }
  }

  toggleChat(event?: Event): void {
    if (event) {
      event.stopPropagation();
      event.preventDefault();
    }
    this.isOpen = !this.isOpen;
    if (this.isOpen) {
      this.hasUnreadNotification = false;
      this.shouldScroll = true;
    }
    this.saveSessionState();
    this.cdr.detectChanges();
  }

  openChat(): void {
    this.isOpen = true;
    this.hasUnreadNotification = false;
    this.shouldScroll = true;
    this.saveSessionState();
    this.cdr.detectChanges();
  }

  closeChat(event?: Event): void {
    if (event) {
      event.stopPropagation();
      event.preventDefault();
    }
    this.isOpen = false;
    this.saveSessionState();
    this.cdr.detectChanges();
  }

  clearChat(event?: Event): void {
    if (event) {
      event.stopPropagation();
      event.preventDefault();
    }
    this.clearScanPoll();
    this.stopProgressAnimation();
    this.showDbScanForm = false;
    this.isDbConnecting = false;
    this.isDbScanning = false;
    this.isGeneratingArtifacts = false;
    this.resetDbConnection();
    this.messages = [];
    this.clearSessionStorage();
    this.initWelcomeMessage();
    this.shouldScroll = true;
    this.cdr.detectChanges();
  }

  private resetDbConnection(): void {
    this.dbConnection = {
      server: '',
      database: '',
      username: '',
      password: '',
      httpPath: ''
    };
  }

  private initWelcomeMessage(): void {
    const rawText = `👋 **Welcome to the Azure Powered AI Innovation Hub Assistant!**\n\nI can help you explore our enterprise Proof of Concepts (POCs), architecture, migration accelerators, and AI solutions.\n\n🚀 You can also **Start a New Database Assessment & Migration Scan** directly from here!`;
    this.messages = [{
      id: 'welcome-' + Date.now(),
      sender: 'bot',
      text: rawText,
      formattedText: this.formatMarkdown(rawText),
      timestamp: new Date(),
      actions: [
        { label: 'Start New DB Scan', actionKey: 'start_db_scan', icon: 'bi bi-play-circle-fill', variant: 'primary' }
      ],
      chips: [
        'Start New DB Scan',
        'Explore Medication Adherence',
        'Database Scanner Overview',
        'BI Migrator Details'
      ]
    }];
    this.saveSessionHistory();
  }

  onEnterPress(event: Event): void {
    event.preventDefault();
    this.sendMessage();
  }

  sendMessage(customText?: string): void {
    const textToSend = (customText ?? this.userInput).trim();
    if (!textToSend || this.isTyping) return;

    // Check if user requested to start DB scan
    const lower = textToSend.toLowerCase();
    if (lower.includes('start new db scan') || lower.includes('start db scan') || lower.includes('start scan') || lower.includes('new scan') || lower.includes('scan database')) {
      this.promptDbScanCredentials(textToSend);
      return;
    }

    // Add user message with immutable array update
    const userMsg: ChatMessage = {
      id: 'user-' + Date.now(),
      sender: 'user',
      text: textToSend,
      formattedText: this.formatMarkdown(textToSend),
      timestamp: new Date()
    };
    this.messages = [...this.messages, userMsg];
    this.userInput = '';
    this.isTyping = true;
    this.shouldScroll = true;
    this.saveSessionHistory();
    this.cdr.detectChanges();
    this.scrollToBottom();

    // Simulate AI response delay
    const responseDelay = Math.min(450 + textToSend.length * 6, 850);
    setTimeout(() => {
      this.ngZone.run(() => {
        const botResponse = this.generateBotResponse(textToSend);
        this.messages = [...this.messages, botResponse];
        this.isTyping = false;
        this.shouldScroll = true;
        this.saveSessionHistory();
        this.cdr.detectChanges();
        this.scrollToBottom();
      });
    }, responseDelay);
  }

  selectPrompt(query: string): void {
    this.sendMessage(query);
  }

  handleAction(action: ChatAction): void {
    if (action.actionKey === 'start_db_scan') {
      this.promptDbScanCredentials('Start New DB Scan');
    } else if (action.actionKey === 'generate_fabric_artifacts') {
      this.triggerGenerateFabricArtifacts();
    } else if (action.actionKey === 'skip_artifacts') {
      this.skipArtifacts();
    } else if (action.actionKey === 'view_metadata_report') {
      window.open('/output/Assesment%20Report.docx?view=1', '_blank');
    } else if (action.actionKey === 'view_migration_plan') {
      window.open('/output/AI_Migration_Plan.docx?view=1', '_blank');
    } else if (action.actionKey === 'open_fabric_report') {
      window.open('https://app.fabric.microsoft.com/groups/bae3b540-d044-45e0-8c52-3cf4ee3dcb31/reports/1538985c-066f-425d-83bd-2530d449d259/1810ae00cc79e99923aa?experience=fabric-developer', '_blank');
    } else if (action.route) {
      this.router.navigate([action.route]);
    } else if (action.prompt) {
      this.sendMessage(action.prompt);
    }
  }

  // =========================================================================
  // DB SCAN WORKFLOW: STEP 1 - PROMPT FRESH CREDENTIALS (NO REMEMBERING)
  // =========================================================================

  promptDbScanCredentials(userQuery?: string): void {
    if (userQuery) {
      const userMsg: ChatMessage = {
        id: 'user-' + Date.now(),
        sender: 'user',
        text: userQuery,
        formattedText: this.formatMarkdown(userQuery),
        timestamp: new Date()
      };
      this.messages = [...this.messages, userMsg];
    }

    this.userInput = '';
    this.showDbScanForm = true;
    this.isDbConnecting = false;
    this.isDbScanning = false;
    // Always start with fresh, empty connection fields
    this.resetDbConnection();

    const botMsg: ChatMessage = {
      id: 'db-prompt-' + Date.now(),
      sender: 'bot',
      type: 'db-form',
      text: `🔑 **Enter Database Credentials**\n\nPlease enter the credentials for your database source. Once verified, the assessment scan will begin immediately.`,
      formattedText: this.formatMarkdown(`🔑 **Enter Database Credentials**\n\nPlease enter the credentials for your database source. Once verified, the assessment scan will begin immediately.`),
      timestamp: new Date()
    };

    this.messages = [...this.messages, botMsg];
    this.shouldScroll = true;
    this.saveSessionHistory();
    this.cdr.detectChanges();
    this.scrollToBottom();
  }

  onSourceChanged(): void {
    // Reset fields on source switch
    this.resetDbConnection();
    this.cdr.detectChanges();
  }

  cancelDbScanForm(): void {
    this.showDbScanForm = false;
    this.resetDbConnection();
    const cancelMsg: ChatMessage = {
      id: 'bot-' + Date.now(),
      sender: 'bot',
      text: `Database scan configuration cancelled. Let me know if you'd like to explore anything else!`,
      formattedText: this.formatMarkdown(`Database scan configuration cancelled. Let me know if you'd like to explore anything else!`),
      timestamp: new Date(),
      chips: ['Start New DB Scan', 'Medication Adherence', 'BI Migrator Overview']
    };
    this.messages = [...this.messages, cancelMsg];
    this.shouldScroll = true;
    this.saveSessionHistory();
    this.cdr.detectChanges();
    this.scrollToBottom();
  }

  // =========================================================================
  // DB SCAN WORKFLOW: STEP 2 - VERIFY CREDENTIALS & START SCAN WITH PROGRESS
  // =========================================================================

  submitAndStartScan(): void {
    const isDatabricks = this.activeScanSource === 'Databricks';
    if (
      !this.dbConnection.server.trim() ||
      !this.dbConnection.database.trim() ||
      (!isDatabricks && !this.dbConnection.username.trim()) ||
      !this.dbConnection.password.trim() ||
      (isDatabricks && !this.dbConnection.httpPath?.trim())
    ) {
      alert('Please fill in all mandatory connection fields.');
      return;
    }

    this.isDbConnecting = true;
    this.showDbScanForm = false;
    this.cdr.detectChanges();

    // Inform user in chat
    const connectingMsg: ChatMessage = {
      id: 'bot-' + Date.now(),
      sender: 'bot',
      text: `🔌 **Verifying credentials for ${this.activeScanSource}...**\nConnecting to \`${this.dbConnection.server}\` (\`${this.dbConnection.database}\`)...`,
      formattedText: this.formatMarkdown(`🔌 **Verifying credentials for ${this.activeScanSource}...**\nConnecting to \`${this.dbConnection.server}\` (\`${this.dbConnection.database}\`)...`),
      timestamp: new Date()
    };
    this.messages = [...this.messages, connectingMsg];
    this.shouldScroll = true;
    this.saveSessionHistory();
    this.cdr.detectChanges();
    this.scrollToBottom();

    // 1. Connect to Database (remember_me = false)
    this.scanner.connectDatabase(this.activeScanSource, this.dbConnection, false).subscribe({
      next: (connRes) => {
        this.isDbConnecting = false;

        // 2. Start Scan Immediately
        this.initiateScanProcess();
      },
      error: (err) => {
        this.isDbConnecting = false;
        const errMsg = err.error?.message || err.message || 'Connection failed. Please check credentials and firewall settings.';
        const failMsg: ChatMessage = {
          id: 'bot-' + Date.now(),
          sender: 'bot',
          text: `❌ **Connection Failed to ${this.activeScanSource}**\n\n*${errMsg}*\n\nPlease verify your server host, credentials, and network access, then try again.`,
          formattedText: this.formatMarkdown(`❌ **Connection Failed to ${this.activeScanSource}**\n\n*${errMsg}*\n\nPlease verify your server host, credentials, and network access, then try again.`),
          timestamp: new Date(),
          actions: [
            { label: 'Try Again', actionKey: 'start_db_scan', icon: 'bi bi-arrow-repeat', variant: 'primary' }
          ],
          chips: ['Start New DB Scan', 'Database Scanner Overview']
        };
        this.messages = [...this.messages, failMsg];
        this.shouldScroll = true;
        this.saveSessionHistory();
        this.cdr.detectChanges();
        this.scrollToBottom();
      }
    });
  }

  private initiateScanProcess(): void {
    this.isDbScanning = true;
    this.dbScanProgress = 5;
    this.dbDisplayProgress = 5;
    this.dbTargetProgress = 10;
    this.startDynamicProgress();

    this.dbScanStatusMessage = `Connected to ${this.activeScanSource}! Starting metadata extraction...`;
    this.dbScanLogs = [
      `[INFO] Connected to ${this.activeScanSource} (${this.dbConnection.database})`,
      `[INFO] Target: Microsoft Fabric OneLake & Lakehouse`,
      `[SCAN] Extracting tables, views, stored procedures, constraints...`
    ];
    this.dbTokenInfo = { total: 0, prompt: 0, completion: 0, cost: '-' };
    this.activeProgressTab = 'progress';

    const scanStartMsg: ChatMessage = {
      id: 'scan-progress-' + Date.now(),
      sender: 'bot',
      type: 'db-progress',
      text: `🚀 **Assessment Scan in Progress**\n\nScanning **${this.activeScanSource}** for Microsoft Fabric modernization readiness...`,
      formattedText: this.formatMarkdown(`🚀 **Assessment Scan in Progress**\n\nScanning **${this.activeScanSource}** for Microsoft Fabric modernization readiness...`),
      timestamp: new Date(),
      metadata: {
        source: this.activeScanSource,
        progress: this.dbScanProgress,
        statusMessage: this.dbScanStatusMessage
      }
    };
    this.messages = [...this.messages, scanStartMsg];
    this.shouldScroll = true;
    this.saveSessionHistory();
    this.cdr.detectChanges();
    this.scrollToBottom();

    // Clear memory password after scan is dispatched
    this.dbConnection.password = '';

    // Call startScan endpoint
    this.scanner.startScan(this.activeScanSource, 'Microsoft Fabric', this.dbConnection).subscribe({
      next: (scanRes) => {
        if (scanRes.scan_id) {
          this.activeScanId = scanRes.scan_id;
          this.startScanPolling(scanRes.scan_id);
        } else {
          this.handleScanFailure('Scan ID not received from backend server.');
        }
      },
      error: (err) => {
        this.handleScanFailure(err.error?.message || 'Failed to start scan job.');
      }
    });
  }

  // Smooth Animated Progress Bar Loop
  private startDynamicProgress(): void {
    this.stopProgressAnimation();
    let lastTime = performance.now();

    const loop = (currentTime: number) => {
      const delta = Math.min((currentTime - lastTime) / 1000, 0.1);
      lastTime = currentTime;

      if (!this.isDbScanning) {
        return;
      }

      if (this.dbScanProgress < this.dbTargetProgress) {
        const diff = this.dbTargetProgress - this.dbScanProgress;
        const speed = Math.max(diff * 2.5, 3.0);
        this.dbScanProgress = Math.min(this.dbTargetProgress, this.dbScanProgress + speed * delta);
      } else if (this.dbScanProgress < Math.min(96, this.dbTargetProgress + 2)) {
        this.dbScanProgress = this.dbScanProgress + 0.2 * delta;
      }

      this.dbDisplayProgress = Math.round(this.dbScanProgress);
      this.cdr.detectChanges();

      if (this.isDbScanning) {
        this.progressAnimationId = requestAnimationFrame(loop);
      }
    };

    this.progressAnimationId = requestAnimationFrame(loop);
  }

  private stopProgressAnimation(): void {
    if (this.progressAnimationId) {
      cancelAnimationFrame(this.progressAnimationId);
      this.progressAnimationId = null;
    }
  }

  private startScanPolling(scanId: string): void {
    this.clearScanPoll();
    this.scanPollInterval = setInterval(() => {
      this.scanner.getScanStatus(scanId).subscribe({
        next: (statusData: ScanStatus) => {
          this.ngZone.run(() => {
            this.handleScanStatusUpdate(statusData);
          });
        },
        error: (err) => {
          console.warn('Poll status error:', err);
        }
      });
    }, 1600);
  }

  private clearScanPoll(): void {
    if (this.scanPollInterval) {
      clearInterval(this.scanPollInterval);
      this.scanPollInterval = null;
    }
  }

  private handleScanStatusUpdate(data: ScanStatus): void {
    if (data.progressbar !== undefined && data.progressbar !== null) {
      if (data.progressbar > this.dbTargetProgress) {
        this.dbTargetProgress = Math.min(98, data.progressbar);
      }
    }
    if (data.scan_status_message) {
      this.dbScanStatusMessage = data.scan_status_message;
    }
    if (data['scan info'] && data['scan info'].length > 0) {
      this.dbScanLogs = data['scan info'];
    }
    if (data['token info'] && data['token info'].length > 0) {
      const latestToken = data['token info'][data['token info'].length - 1];
      this.dbTokenInfo = {
        total: latestToken.total || 0,
        prompt: latestToken.prompt || 0,
        completion: latestToken.completion || 0,
        cost: latestToken.cost || '-'
      };
    }

    this.cdr.detectChanges();

    // Check Completion or Failure
    if (data.status === 'Completed') {
      this.dbTargetProgress = 100;
      this.dbScanProgress = 100;
      this.dbDisplayProgress = 100;
      this.clearScanPoll();
      this.stopProgressAnimation();
      this.isDbScanning = false;
      this.handleScanCompletedSuccess();
    } else if (data.status === 'Failed') {
      this.clearScanPoll();
      this.stopProgressAnimation();
      this.isDbScanning = false;
      this.handleScanFailure(data.error || 'Scan process failed during metadata analysis.');
    }
  }

  // =========================================================================
  // DB SCAN WORKFLOW: STEP 3 - SCAN SUCCESS & PROMPT FOR ARTIFACTS
  // =========================================================================

  private handleScanCompletedSuccess(): void {
    const successMsg: ChatMessage = {
      id: 'scan-success-' + Date.now(),
      sender: 'bot',
      type: 'artifact-prompt',
      text: `🎉 **Database Scan Completed Successfully!**\n\n• **Source Database:** ${this.activeScanSource}\n• **Target Architecture:** Microsoft Fabric\n• **Assessment Report:** \`Assesment Report.docx\`\n• **Migration Plan:** \`AI_Migration_Plan.docx\`\n\n**Would you like to generate Microsoft Fabric Artifacts (Delta tables, schemas, views, stored procedures) now?**`,
      formattedText: this.formatMarkdown(`🎉 **Database Scan Completed Successfully!**\n\n• **Source Database:** ${this.activeScanSource}\n• **Target Architecture:** Microsoft Fabric\n• **Assessment Report:** \`Assesment Report.docx\`\n• **Migration Plan:** \`AI_Migration_Plan.docx\`\n\n**Would you like to generate Microsoft Fabric Artifacts (Delta tables, schemas, views, stored procedures) now?**`),
      timestamp: new Date(),
      actions: [
        { label: '🚀 Yes, Generate Fabric Artifacts', actionKey: 'generate_fabric_artifacts', icon: 'bi bi-lightning-charge-fill', variant: 'primary' },
        { label: '📄 View Assessment Report', actionKey: 'view_metadata_report', icon: 'bi bi-file-earmark-word', variant: 'secondary' },
        { label: '📋 View Migration Plan', actionKey: 'view_migration_plan', icon: 'bi bi-file-earmark-text', variant: 'secondary' },
        { label: '❌ No, Skip for Now', actionKey: 'skip_artifacts', icon: 'bi bi-x-circle', variant: 'danger' }
      ],
      chips: [
        'Generate Fabric Artifacts',
        'Open DB Scanner Page',
        'Start Another Scan'
      ]
    };

    this.messages = [...this.messages, successMsg];
    this.shouldScroll = true;
    this.saveSessionHistory();
    this.cdr.detectChanges();
    this.scrollToBottom();
  }

  private handleScanFailure(errorMessage: string): void {
    this.isDbScanning = false;
    this.clearScanPoll();
    this.stopProgressAnimation();

    const failMsg: ChatMessage = {
      id: 'scan-failed-' + Date.now(),
      sender: 'bot',
      text: `⚠️ **Scan Failed**\n\n*${errorMessage}*\n\nYou can re-check connection settings or try scanning again.`,
      formattedText: this.formatMarkdown(`⚠️ **Scan Failed**\n\n*${errorMessage}*\n\nYou can re-check connection settings or try scanning again.`),
      timestamp: new Date(),
      actions: [
        { label: 'Try Again', actionKey: 'start_db_scan', icon: 'bi bi-arrow-repeat', variant: 'primary' }
      ],
      chips: ['Start New DB Scan', 'Open DB Scanner Page']
    };

    this.messages = [...this.messages, failMsg];
    this.shouldScroll = true;
    this.saveSessionHistory();
    this.cdr.detectChanges();
    this.scrollToBottom();
  }

  // =========================================================================
  // DB SCAN WORKFLOW: STEP 4 - GENERATE FABRIC ARTIFACTS
  // =========================================================================

  triggerGenerateFabricArtifacts(): void {
    this.isGeneratingArtifacts = true;

    const generatingMsg: ChatMessage = {
      id: 'bot-' + Date.now(),
      sender: 'bot',
      text: `⚙️ **Generating Microsoft Fabric Artifacts...**\n\nTranslating schemas into Delta tables, Views, and Fabric Lakehouse bindings for **${this.activeScanSource}**...`,
      formattedText: this.formatMarkdown(`⚙️ **Generating Microsoft Fabric Artifacts...**\n\nTranslating schemas into Delta tables, Views, and Fabric Lakehouse bindings for **${this.activeScanSource}**...`),
      timestamp: new Date()
    };
    this.messages = [...this.messages, generatingMsg];
    this.shouldScroll = true;
    this.saveSessionHistory();
    this.cdr.detectChanges();
    this.scrollToBottom();

    this.scanner.generateFabricArtifacts(this.activeScanSource).subscribe({
      next: (res: any) => {
        this.isGeneratingArtifacts = false;
        const tablesCount = res.tables?.length || res.tables_info?.length || 0;
        const scriptName = res.generator_script || 'Fabric Generator';

        const artifactResultMsg: ChatMessage = {
          id: 'artifact-result-' + Date.now(),
          sender: 'bot',
          type: 'artifact-result',
          text: `✅ **Microsoft Fabric Artifacts Generated Successfully!**\n\n• **Generator Engine:** \`${scriptName}\`\n• **Status:** ${res.message || 'Artifacts created'}\n• **Delta Tables & Schema Objects:** ${tablesCount > 0 ? tablesCount + ' items' : 'Ready'}\n\nYour target lakehouse definitions are now prepared and deployed in Microsoft Fabric.`,
          formattedText: this.formatMarkdown(`✅ **Microsoft Fabric Artifacts Generated Successfully!**\n\n• **Generator Engine:** \`${scriptName}\`\n• **Status:** ${res.message || 'Artifacts created'}\n• **Delta Tables & Schema Objects:** ${tablesCount > 0 ? tablesCount + ' items' : 'Ready'}\n\nYour target lakehouse definitions are now prepared and deployed in Microsoft Fabric.`),
          timestamp: new Date(),
          actions: [
            { label: '📊 Open Microsoft Fabric Report', actionKey: 'open_fabric_report', icon: 'bi bi-box-arrow-up-right', variant: 'primary' },
            { label: '🗄️ Start Another Scan', actionKey: 'start_db_scan', icon: 'bi bi-arrow-repeat', variant: 'secondary' },
            { label: '🏠 Return to Home', route: '/', icon: 'bi bi-house-door', variant: 'secondary' }
          ],
          chips: ['Start Another Scan', 'Explain BI Migrator', 'Medication Adherence']
        };

        this.messages = [...this.messages, artifactResultMsg];
        this.shouldScroll = true;
        this.saveSessionHistory();
        this.cdr.detectChanges();
        this.scrollToBottom();
      },
      error: (err: any) => {
        this.isGeneratingArtifacts = false;
        const errMsg = err.error?.message || err.message || 'Failed to generate Fabric artifacts.';
        const errResultMsg: ChatMessage = {
          id: 'bot-' + Date.now(),
          sender: 'bot',
          text: `⚠️ **Fabric Artifact Generation Notice**\n\n*${errMsg}*\n\nYou can re-run artifact generation or view the assessment reports directly.`,
          formattedText: this.formatMarkdown(`⚠️ **Fabric Artifact Generation Notice**\n\n*${errMsg}*\n\nYou can re-run artifact generation or view the assessment reports directly.`),
          timestamp: new Date(),
          actions: [
            { label: 'Retry Artifact Generation', actionKey: 'generate_fabric_artifacts', icon: 'bi bi-arrow-repeat', variant: 'primary' },
            { label: '📄 View Assessment Report', actionKey: 'view_metadata_report', icon: 'bi bi-file-earmark-word', variant: 'secondary' }
          ]
        };
        this.messages = [...this.messages, errResultMsg];
        this.shouldScroll = true;
        this.saveSessionHistory();
        this.cdr.detectChanges();
        this.scrollToBottom();
      }
    });
  }

  skipArtifacts(): void {
    const skipMsg: ChatMessage = {
      id: 'bot-' + Date.now(),
      sender: 'bot',
      text: `👍 **Understood!** Fabric artifact generation was skipped.\n\nYour **Assessment Report** and **Migration Plan** remain available whenever you need them. You can also generate Fabric artifacts later from the Database Scanner page.\n\nHow else can I assist you?`,
      formattedText: this.formatMarkdown(`👍 **Understood!** Fabric artifact generation was skipped.\n\nYour **Assessment Report** and **Migration Plan** remain available whenever you need them. You can also generate Fabric artifacts later from the Database Scanner page.\n\nHow else can I assist you?`),
      timestamp: new Date(),
      actions: [
        { label: '📄 View Assessment Report', actionKey: 'view_metadata_report', icon: 'bi bi-file-earmark-word', variant: 'secondary' },
        { label: '🗄️ Start Another Scan', actionKey: 'start_db_scan', icon: 'bi bi-play-circle-fill', variant: 'secondary' }
      ],
      chips: ['Start New DB Scan', 'BI Migrator Overview', 'Medication Adherence']
    };
    this.messages = [...this.messages, skipMsg];
    this.shouldScroll = true;
    this.saveSessionHistory();
    this.cdr.detectChanges();
    this.scrollToBottom();
  }

  // =========================================================================
  // STANDARD KNOWLEDGE BOT RESPONSES
  // =========================================================================

  private generateBotResponse(query: string): ChatMessage {
    const lower = query.toLowerCase();

    // 1. Medication Adherence
    if (lower.includes('medication') || lower.includes('adherence') || lower.includes('healthcare') || lower.includes('patient') || lower.includes('capsule')) {
      const text = `🏥 **AI-Driven Medication Adherence Intelligence**\n\nThis POC provides predictive healthcare insights to improve patient outcomes and therapy continuity:\n\n• **Predictive Adherence Scoring:** Identifies patients at high risk of non-adherence using predictive machine learning models.\n• **Intervention Recommendations:** Generates personalized clinical intervention strategies.\n• **Population Health Analytics:** Interactive dashboards for healthcare providers and clinical teams to track metrics across demographics.\n\nWould you like to explore the Medication module?`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: 'Open Medication Adherence', route: '/medication', icon: 'bi bi-capsule' },
          { label: 'How does it predict risk?', prompt: 'How does medication adherence predict patient risk?', icon: 'bi bi-question-circle' }
        ],
        chips: ['Medication Data Sources', 'Clinical Outcomes', 'Return to Home']
      };
    }

    // 2. Database Scanner & Migration Readiness
    if (lower.includes('database') || lower.includes('scanner') || lower.includes('db') || lower.includes('schema') || lower.includes('sql') || lower.includes('extractor') || lower.includes('synapse') || lower.includes('snowflake')) {
      const text = `🗄️ **Database Assessment & Migration Readiness Scanner**\n\nThis accelerator automates legacy database evaluation for cloud modernization:\n\n• **Multi-Engine Metadata Extraction:** Supports SQL Server, Synapse, Snowflake, Databricks, Dynamics 365, and Oracle.\n• **AI-Driven Assessment:** Uses LLM agents to evaluate schema complexity, dependencies, data types, and stored procedures.\n• **Readiness & Complexity Scoring:** Produces detailed readiness scores, risk flags, and conversion effort estimations.\n• **Automated Artifacts:** Generates Microsoft Fabric Lakehouse schemas and Synapse migration scripts.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Start New DB Scan', actionKey: 'start_db_scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
          { label: 'Open Database Scanner Page', route: '/dbscanner', icon: 'bi bi-database-check' }
        ],
        chips: ['Start New DB Scan', 'Fabric Artifact Generation', 'View Harness Reports']
      };
    }

    // 3. BI Migrator
    if (lower.includes('bi') || lower.includes('migrator') || lower.includes('tableau') || lower.includes('cognos') || lower.includes('qlik') || lower.includes('power bi') || lower.includes('powerbi') || lower.includes('report') || lower.includes('dashboard')) {
      const text = `📊 **BI Modernization Accelerator**\n\nModernizes legacy reporting tools into Microsoft Fabric & Power BI with automated conversions:\n\n• **Legacy BI Ingestion:** Parses worksheets, calculated fields, and dashboards from Tableau (.twb/.twbx), Cognos, and Qlik.\n• **Automated DAX & Semantic Models:** AI Agent Pipeline translates formulas, queries, and business logic into Power BI DAX and Semantic Models.\n• **Visual Mapping & Fabric Integration:** Prepares target Power BI report definitions and Fabric Lakehouse bindings.\n• **Migration Time Savings:** Reduces manual BI report rebuilding time by up to 70%.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: 'Open BI Migrator', route: '/bimigrator', icon: 'bi bi-bar-chart-line-fill' },
          { label: 'Tableau to Power BI Process', prompt: 'How does Tableau to Power BI migration work?', icon: 'bi bi-arrow-repeat' }
        ],
        chips: ['Supported BI Tools', 'DAX Conversion Accuracy', 'Fabric Direct Lake']
      };
    }

    // 4. Microsoft Fabric & Azure Cloud
    if (lower.includes('fabric') || lower.includes('azure') || lower.includes('cloud') || lower.includes('architecture') || lower.includes('onelake') || lower.includes('openai')) {
      const text = `☁️ **Azure & Microsoft Fabric Core Capabilities**\n\nThe Innovation Hub leverages cutting-edge enterprise cloud & AI services:\n\n• **Microsoft Fabric:** Unified OneLake storage, Lakehouse schemas, Direct Lake Power BI, and Data Factory pipelines.\n• **Azure OpenAI / LLM Pipelines:** Multi-agent reasoning for automated code conversion, schema analysis, and report generation.\n• **Harness AI Layers:** Layered validation framework ensuring high fidelity and deterministic outputs.\n• **Security & Scale:** Enterprise-grade security with role-based access control and managed cloud deployments.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Start New DB Scan', actionKey: 'start_db_scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
          { label: 'Explore DB Scanner', route: '/dbscanner', icon: 'bi bi-database-check' },
          { label: 'Explore BI Migrator', route: '/bimigrator', icon: 'bi bi-bar-chart-line-fill' }
        ],
        chips: ['Start New DB Scan', 'Tell me about Medication Adherence', 'What is BI Migrator?']
      };
    }

    // 5. Help / Greetings / General
    if (lower.includes('hello') || lower.includes('hi') || lower.includes('hey') || lower.includes('help') || lower.includes('who are you') || lower.includes('what can you do')) {
      const text = `👋 Hello! I am the **Apexon AI Innovation Hub Assistant**.\n\nI can assist you with:\n1. **Start New DB Scan** – Automated metadata extraction & Fabric assessment.\n2. **Medication Adherence** – AI Healthcare Insights & Prediction.\n3. **BI Migrator** – Modernizing Tableau, Cognos, & Qlik to Power BI / Fabric.\n4. **Azure & Fabric Architecture** – Cloud modernization best practices.\n\nSelect a topic or type your question below!`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Start New DB Scan', actionKey: 'start_db_scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
          { label: 'Medication Adherence', route: '/medication', icon: 'bi bi-capsule' },
          { label: 'BI Migrator', route: '/bimigrator', icon: 'bi bi-bar-chart-line-fill' }
        ],
        chips: ['Start New DB Scan', 'What is Database Scanner?', 'Explain BI Migrator', 'Medication Adherence POC']
      };
    }

    // Fallback response
    const text = `I understand you are asking about: *"${query}"*.\n\nIn the **Apexon AI Innovation Hub**, we specialize in accelerating digital transformation across three flagship POCs:\n\n• **Database Scanner:** Automated metadata extraction & Fabric migration assessment.\n• **BI Migrator:** Automated migration of legacy BI reports into Power BI & Microsoft Fabric.\n• **Medication Adherence:** AI-powered patient risk analytics & healthcare insights.\n\nPlease choose an area below or start a live scan:`;
    return {
      id: 'bot-' + Date.now(),
      sender: 'bot',
      text: text,
      formattedText: this.formatMarkdown(text),
      timestamp: new Date(),
      actions: [
        { label: '🚀 Start New DB Scan', actionKey: 'start_db_scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
        { label: 'Go to Database Scanner', route: '/dbscanner', icon: 'bi bi-database-check' },
        { label: 'Go to BI Migrator', route: '/bimigrator', icon: 'bi bi-bar-chart-line-fill' }
      ],
      chips: ['Start New DB Scan', 'Tell me about Medication Adherence', 'Database Scanner Details', 'BI Migrator Overview']
    };
  }

  private scrollToBottom(): void {
    if (this.chatBodyRef) {
      try {
        const element = this.chatBodyRef.nativeElement;
        element.scrollTop = element.scrollHeight;
      } catch (err) {
        // ignore scroll error
      }
    }
  }

  private formatMarkdown(text: string): string {
    return text
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\n\n/g, '<br/><br/>')
      .replace(/\n• /g, '<br/>• ')
      .replace(/\n/g, '<br/>');
  }

  // =========================================================================
  // SESSION STORAGE MANAGEMENT (CHAT ONLY - NO CREDENTIALS STORED)
  // =========================================================================

  private isBrowser(): boolean {
    return typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined';
  }

  private restoreSession(): void {
    if (!this.isBrowser()) {
      this.initWelcomeMessage();
      return;
    }

    try {
      const savedHistory = window.sessionStorage.getItem(SESSION_HISTORY_KEY);
      if (savedHistory) {
        const parsed = JSON.parse(savedHistory);
        if (Array.isArray(parsed) && parsed.length > 0) {
          this.messages = parsed.map((m: any) => ({
            ...m,
            timestamp: new Date(m.timestamp),
            formattedText: m.formattedText || this.formatMarkdown(m.text)
          }));
        } else {
          this.initWelcomeMessage();
        }
      } else {
        this.initWelcomeMessage();
      }

      const savedState = window.sessionStorage.getItem(SESSION_STATE_KEY);
      if (savedState) {
        const state = JSON.parse(savedState);
        this.isOpen = !!state.isOpen;
        this.hasUnreadNotification = state.hasUnreadNotification !== undefined ? state.hasUnreadNotification : true;
      }

      if (this.isOpen) {
        this.shouldScroll = true;
      }
    } catch (err) {
      console.warn('Could not restore chat session:', err);
      this.initWelcomeMessage();
    }
  }

  private saveSessionHistory(): void {
    if (!this.isBrowser()) return;
    try {
      window.sessionStorage.setItem(SESSION_HISTORY_KEY, JSON.stringify(this.messages));
    } catch (err) {
      console.warn('Could not save chat history to session:', err);
    }
  }

  private saveSessionState(): void {
    if (!this.isBrowser()) return;
    try {
      window.sessionStorage.setItem(SESSION_STATE_KEY, JSON.stringify({
        isOpen: this.isOpen,
        hasUnreadNotification: this.hasUnreadNotification
      }));
    } catch (err) {
      console.warn('Could not save chat state to session:', err);
    }
  }

  private clearSessionStorage(): void {
    if (!this.isBrowser()) return;
    try {
      window.sessionStorage.removeItem(SESSION_HISTORY_KEY);
      window.sessionStorage.removeItem(SESSION_STATE_KEY);
    } catch (err) {
      console.warn('Could not clear chat session:', err);
    }
  }
}
