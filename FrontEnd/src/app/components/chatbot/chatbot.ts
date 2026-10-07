import { Component, ElementRef, ViewChild, AfterViewChecked, OnInit, OnDestroy, ChangeDetectorRef, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Scanner, ErDiagramResponse } from '../../services/scanner/scanner';

export interface ChatAction {
  label: string;
  route?: string;
  queryParams?: Record<string, string>;
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
}

export interface QuickPrompt {
  label: string;
  query?: string;
  route?: string;
  queryParams?: Record<string, string>;
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

  quickPrompts: QuickPrompt[] = [
    {
      label: 'Start New DB Scan',
      query: 'Start New DB Scan',
      icon: 'bi bi-play-circle-fill'
    },
    {
      label: 'Explain ER Diagrams',
      query: 'Explain the ER diagrams and schema relationships',
      icon: 'bi bi-diagram-3-fill'
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
      label: 'Medication Adherence',
      query: 'Tell me about Medication Adherence',
      icon: 'bi bi-capsule'
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
    private cdr: ChangeDetectorRef,
    private ngZone: NgZone,
    private scanner: Scanner
  ) {}

  ngOnInit(): void {
    // Refresh / initialize fresh chat session on page load
    this.clearSessionStorage();
    this.initWelcomeMessage();
  }

  ngOnDestroy(): void {}

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
    this.messages = [];
    this.clearSessionStorage();
    this.initWelcomeMessage();
    this.shouldScroll = true;
    this.cdr.detectChanges();
  }

  private initWelcomeMessage(): void {
    const rawText = `👋 **Welcome to the Azure Powered AI Innovation Hub Assistant!**\n\nI can help you explore enterprise Proof of Concepts (POCs), architecture, migration accelerators, and AI solutions.\n\nSelect a quick accelerator below or ask me any question!`;
    this.messages = [{
      id: 'welcome-' + Date.now(),
      sender: 'bot',
      text: rawText,
      formattedText: this.formatMarkdown(rawText),
      timestamp: new Date(),
      actions: [
        { label: '🚀 Start New DB Scan', prompt: 'Start New DB Scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
        { label: '📊 Explain ER Diagrams', prompt: 'Explain the ER diagrams and schema relationships', icon: 'bi bi-diagram-3-fill', variant: 'secondary' },
        { label: '🏥 Medication Adherence POC', route: '/medication', icon: 'bi bi-capsule', variant: 'secondary' },
        { label: '📈 BI Modernization Accelerator', route: '/bimigrator', icon: 'bi bi-bar-chart-line-fill', variant: 'secondary' }
      ],
      chips: [
        'Start New DB Scan',
        'Scan SQL Server',
        'Scan Databricks',
        'Scan Snowflake',
        'Explain ER Diagrams',
        'Medication Adherence POC',
        'BI Migrator Details'
      ]
    }];
  }

  onEnterPress(event: Event): void {
    event.preventDefault();
    this.sendMessage();
  }

  isGeneralScanRequest(lower: string): boolean {
    const q = lower.trim();
    return q === 'start new db scan' ||
           q === 'start db scan' ||
           q === 'run db scan' ||
           q === 'run scan' ||
           q === 'new scan' ||
           q === 'start scan' ||
           q === 'scan database' ||
           q === 'database scan' ||
           q === 'start database scan' ||
           q === 'db scan' ||
           q === 'start new scan';
  }

  getSpecificScanSource(lower: string): string | null {
    if (lower.includes('sql server') || lower.includes('mssql') || lower.includes('sqlserver')) return 'sqlserver';
    if (lower.includes('databricks')) return 'Databricks';
    if (lower.includes('snowflake')) return 'Snowflake';
    if (lower.includes('synapse') || lower.includes('azure synapse')) return 'Synapse';
    if (lower.includes('dynamics 365') || lower.includes('dynamics') || lower.includes('dataverse') || lower.includes('d365')) return 'dynamics365';
    if (lower.includes('oracle')) return 'Oracle';
    if (lower.includes('mysql')) return 'MySQL';
    if (lower.includes('postgres') || lower.includes('postgresql')) return 'PostgreSQL';
    if (lower.includes('sqlite')) return 'SQLite';
    return null;
  }

  isErDiagramQuery(lower: string): boolean {
    return lower.includes('er diagram') || 
           lower.includes('er diagrams') || 
           lower.includes('entity relationship') || 
           lower.includes('schema relationship') || 
           lower.includes('table relationship') || 
           lower.includes('explain er') || 
           lower.includes('explain diagram') || 
           lower.includes('explain the er') || 
           lower.includes('erd') || 
           lower.includes('entities and relationships') || 
           lower.includes('how are tables connected') || 
           lower.includes('how tables connect') || 
           lower.includes('keys and relationships') || 
           lower.includes('schema model') || 
           lower.includes('er model');
  }

  buildStartScanPromptResponse(): ChatMessage {
    const text = `🚀 **Start Database Assessment & Migration Scan**\n\nWhich database source would you like to scan and assess for Microsoft Fabric migration?\n\nPlease select your database source below to open its connection modal:`;
    return {
      id: 'bot-' + Date.now(),
      sender: 'bot',
      text: text,
      formattedText: this.formatMarkdown(text),
      timestamp: new Date(),
      actions: [
        { label: '🗄️ SQL Server', route: '/dbscanner', queryParams: { source: 'sqlserver', connect: '1' }, icon: 'bi bi-database-fill-gear', variant: 'primary' },
        { label: '🧱 Databricks', route: '/dbscanner', queryParams: { source: 'Databricks', connect: '1' }, icon: 'bi bi-layers', variant: 'primary' },
        { label: '❄️ Snowflake', route: '/dbscanner', queryParams: { source: 'Snowflake', connect: '1' }, icon: 'bi bi-snow', variant: 'primary' },
        { label: '⚡ Azure Synapse', route: '/dbscanner', queryParams: { source: 'Synapse', connect: '1' }, icon: 'bi bi-cloud-arrow-up-fill', variant: 'secondary' },
        { label: '💼 Dynamics 365 / Dataverse', route: '/dbscanner', queryParams: { source: 'dynamics365', connect: '1' }, icon: 'bi bi-briefcase-fill', variant: 'secondary' },
        { label: '🔴 Oracle Database', route: '/dbscanner', queryParams: { source: 'Oracle', connect: '1' }, icon: 'bi bi-database', variant: 'secondary' },
        { label: '🐬 MySQL', route: '/dbscanner', queryParams: { source: 'MySQL', connect: '1' }, icon: 'bi bi-hdd-network', variant: 'secondary' },
        { label: '🐘 PostgreSQL', route: '/dbscanner', queryParams: { source: 'PostgreSQL', connect: '1' }, icon: 'bi bi-hdd-stack', variant: 'secondary' }
      ],
      chips: [
        'Scan SQL Server',
        'Scan Databricks',
        'Scan Snowflake',
        'Scan Azure Synapse',
        'Scan Dynamics 365',
        'Explain ER Diagrams'
      ]
    };
  }

  buildErModelResponse(model: ErDiagramResponse | null, query: string, activeSource?: string): ChatMessage {
    if (model && model.tables && model.tables.length > 0) {
      const sourceDb = this.formatSourceName(activeSource || model.source || model.database || 'Database');
      const tableCount = model.tables.length;
      const relCount = model.relationships?.length || 0;
      const relMode = model.relationship_source === 'declared' 
        ? 'Declared Foreign Keys (Strict Relational Integrity)' 
        : 'Inferred Relationships (AI Schema Pattern Matching)';

      let tableList = model.tables.slice(0, 8).map(t => {
        const pkStr = t.primary_key ? `PK: \`${t.primary_key}\`` : 'No PK declared';
        const colCount = t.columns?.length || 0;
        return `• **${t.id}** (${colCount} columns, ${pkStr})`;
      }).join('\n');

      if (model.tables.length > 8) {
        tableList += `\n• *...and ${model.tables.length - 8} more tables.*`;
      }

      let relList = '';
      if (model.relationships && model.relationships.length > 0) {
        relList = model.relationships.slice(0, 8).map(r => {
          const inferBadge = r.inferred ? ' *(inferred)*' : ' *(foreign key)*';
          return `• **${r.from}** \`[${r.from_column}]\` ➔ **${r.to}**${inferBadge}`;
        }).join('\n');

        if (model.relationships.length > 8) {
          relList += `\n• *...and ${model.relationships.length - 8} more entity relationships.*`;
        }
      } else {
        relList = '• *No direct foreign key relationships detected across tables (standalone entities).*';
      }

      const text = `📊 **Live Entity-Relationship (ER) Diagram Analysis for ${sourceDb}**\n\nHere is the detailed structural breakdown of your scanned **${sourceDb}** database:\n\n### 🗄️ Database Architecture Overview\n• **Source Engine:** ${sourceDb}\n• **Total Tables Analyzed:** ${tableCount}\n• **Total Relationships Detected:** ${relCount}\n• **Constraint Detection Mode:** ${relMode}\n\n### 📋 Scanned Entities (Tables & Primary Keys)\n${tableList}\n\n### 🔗 Entity Relationships & Referential Integrity\n${relList}\n\n### ☁️ Microsoft Fabric Lakehouse & Direct Lake Optimization\n• **Delta Parquet Storage:** Each entity maps directly to a high-performance Delta table in Microsoft Fabric OneLake.\n• **Semantic Model Ready:** Foreign key relationships form the semantic backbone for Power BI Direct Lake queries and Star Schemas without duplicating data.\n• **Integrity Preservation:** Declared constraints prevent orphan records during ELT ingestion pipelines.\n\n### 👁️ Interactive Visual Diagram\nYou can view and explore the full interactive Mermaid diagram under the **ER Diagrams** tab on the Database Scanner page (with Zoom, Pan, Schema Filtering, and Key Columns mode).`;

      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: `📊 View ${sourceDb} ER Diagrams in Scanner`, route: '/dbscanner', queryParams: { connect: '1' }, icon: 'bi bi-diagram-3-fill', variant: 'primary' },
          { label: '🚀 Start New DB Scan', prompt: 'Start New DB Scan', icon: 'bi bi-database-fill-gear', variant: 'secondary' }
        ],
        chips: ['Explain Primary & Foreign Keys', 'Fabric Delta Lake Mapping', 'Start New DB Scan', 'Database Scanner Overview']
      };
    }

    return this.buildErConceptResponse(query);
  }

  buildErConceptResponse(query: string): ChatMessage {
    const text = `📊 **Entity-Relationship (ER) Diagram & Schema Intelligence**\n\nAn **Entity-Relationship (ER) Diagram** visually represents your database architecture, tables, columns, primary keys, and relational dependencies before modernizing to Microsoft Fabric.\n\n### 🔑 Core ER Diagram Fundamentals\n• **Entities (Tables):** Structured business data domains (e.g., Customers, Orders, Products, Invoices, Transactions).\n• **Attributes (Columns):** Individual fields, data types, and nullability properties for each entity.\n• **Primary Keys (PK):** Unique column(s) that distinctly identify each record in a table.\n• **Foreign Keys (FK):** Columns that reference primary keys in other tables (e.g., \`Orders.CustomerID\` ➔ \`Customers.CustomerID\`), defining 1-to-Many, Many-to-1, or Many-to-Many associations.\n\n### 🤖 AI Agent Extraction & Microsoft Fabric Modernization\n1. **Declared Constraints:** Reads native primary keys and foreign keys directly from system catalogs.\n2. **Inferred Relationships:** For cloud lakehouses or tables lacking explicit foreign keys, the AI engine infers relations by matching column naming patterns (such as \`*_id\` and \`*_key\`).\n3. **Interactive Visualizer:** Renders an interactive Mermaid ER graph on the **Database Scanner -> ER Diagrams** tab with schema filtering and zoom controls.\n4. **Microsoft Fabric Mapping:** Automatically optimizes schemas into Delta Lake Parquet tables and Direct Lake semantic models for Power BI.\n\n💡 *No active scan has been performed in this session yet.* To view and analyze the live ER diagram for your database, select a data source below and start a scan!`;

    return {
      id: 'bot-' + Date.now(),
      sender: 'bot',
      text: text,
      formattedText: this.formatMarkdown(text),
      timestamp: new Date(),
      actions: [
        { label: '🗄️ Scan SQL Server', route: '/dbscanner', queryParams: { source: 'sqlserver', connect: '1' }, icon: 'bi bi-database-fill-gear', variant: 'primary' },
        { label: '🧱 Scan Databricks', route: '/dbscanner', queryParams: { source: 'Databricks', connect: '1' }, icon: 'bi bi-layers', variant: 'secondary' },
        { label: '❄️ Scan Snowflake', route: '/dbscanner', queryParams: { source: 'Snowflake', connect: '1' }, icon: 'bi bi-snow', variant: 'secondary' },
        { label: '⚡ Scan Azure Synapse', route: '/dbscanner', queryParams: { source: 'Synapse', connect: '1' }, icon: 'bi bi-cloud-arrow-up-fill', variant: 'secondary' }
      ],
      chips: ['Start New DB Scan', 'Scan SQL Server', 'Scan Databricks', 'Scan Snowflake', 'Explain Primary & Foreign Keys', 'Fabric Delta Lake Mapping']
    };
  }

  sendMessage(customText?: string): void {
    const textToSend = (customText ?? this.userInput).trim();
    if (!textToSend || this.isTyping) return;

    const lower = textToSend.toLowerCase();

    // 1. If it's a specific scan request (e.g., 'scan databricks', 'scan sql server', etc.), navigate to dbscanner with that source
    const specificSource = this.getSpecificScanSource(lower);
    if (specificSource && (lower.includes('scan') || lower.includes('connect'))) {
      this.scanner.requestOpenConnection(specificSource);
      this.router.navigate(['/dbscanner'], { queryParams: { source: specificSource, connect: '1', _t: Date.now().toString() } });
    }

    // Add user message
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
    this.cdr.detectChanges();
    this.scrollToBottom();

    // 2. Check if query is a general start scan request (asking to start a scan without specifying engine)
    if (this.isGeneralScanRequest(lower)) {
      const responseDelay = Math.min(300 + textToSend.length * 4, 600);
      setTimeout(() => {
        this.ngZone.run(() => {
          const botResponse = this.buildStartScanPromptResponse();
          this.messages = [...this.messages, botResponse];
          this.isTyping = false;
          this.shouldScroll = true;
          this.cdr.detectChanges();
          this.scrollToBottom();
        });
      }, responseDelay);
      return;
    }

    // 3. Check if query is asking to explain ER diagrams
    if (this.isErDiagramQuery(lower)) {
      const activeScan = this.scanner.getCompletedScan();
      if (activeScan && activeScan.metadataFile) {
        this.scanner.getErDiagram(activeScan.metadataFile).subscribe({
          next: (model) => {
            this.ngZone.run(() => {
              const botResponse = this.buildErModelResponse(model, textToSend, activeScan.source);
              this.messages = [...this.messages, botResponse];
              this.isTyping = false;
              this.shouldScroll = true;
              this.cdr.detectChanges();
              this.scrollToBottom();
            });
          },
          error: () => {
            this.ngZone.run(() => {
              const botResponse = this.buildErConceptResponse(textToSend);
              this.messages = [...this.messages, botResponse];
              this.isTyping = false;
              this.shouldScroll = true;
              this.cdr.detectChanges();
              this.scrollToBottom();
            });
          }
        });
        return;
      }

      // If no active scan has been performed yet, immediately return conceptual explanation
      const responseDelay = Math.min(300 + textToSend.length * 4, 600);
      setTimeout(() => {
        this.ngZone.run(() => {
          const botResponse = this.buildErConceptResponse(textToSend);
          this.messages = [...this.messages, botResponse];
          this.isTyping = false;
          this.shouldScroll = true;
          this.cdr.detectChanges();
          this.scrollToBottom();
        });
      }, responseDelay);
      return;
    }

    // 4. Natural bot response delay for other queries
    const responseDelay = Math.min(400 + textToSend.length * 5, 800);
    setTimeout(() => {
      this.ngZone.run(() => {
        const botResponse = this.generateBotResponse(textToSend);
        this.messages = [...this.messages, botResponse];
        this.isTyping = false;
        this.shouldScroll = true;
        this.cdr.detectChanges();
        this.scrollToBottom();
      });
    }, responseDelay);
  }

  formatSourceName(source: string): string {
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
    if (s === 'sap' || s === 'sap hana') return 'SAP HANA';
    return source.charAt(0).toUpperCase() + source.slice(1);
  }

  onQuickPromptClick(prompt: QuickPrompt): void {
    if (prompt.query) {
      this.sendMessage(prompt.query);
    } else if (prompt.route) {
      this.handleAction({
        label: prompt.label,
        route: prompt.route,
        queryParams: prompt.queryParams,
        icon: prompt.icon
      });
    }
  }

  selectPrompt(query: string): void {
    const qLower = query.trim().toLowerCase();

    if (this.isGeneralScanRequest(qLower)) {
      this.sendMessage(query);
      return;
    }

    const specificSource = this.getSpecificScanSource(qLower);
    if (specificSource && (qLower.includes('scan') || qLower.includes('connect'))) {
      this.scanner.requestOpenConnection(specificSource);
      this.router.navigate(['/dbscanner'], { queryParams: { source: specificSource, connect: '1', _t: Date.now().toString() } });
      const sourceName = this.formatSourceName(specificSource);
      const text = `🚀 **Database Scanner Launched for ${sourceName}**\n\nI have loaded the **Database Scanner** with the **${sourceName} Connection Dialog** on your screen.\n\n• **Step 1:** Enter or confirm your ${sourceName} connection credentials.\n• **Step 2:** Click **Connect** — your assessment scan will start immediately upon successful credentials validation.\n• **Step 3:** The **Live Execution & Assessment Console** will stream real-time logs, AI tokenomics, ER Diagrams, and Word reports.\n\n*The chatbot will remain open here to assist you during the scan.*`;
      
      this.messages = [
        ...this.messages,
        {
          id: 'user-' + Date.now(),
          sender: 'user',
          text: query,
          formattedText: query,
          timestamp: new Date()
        },
        {
          id: 'bot-' + (Date.now() + 1),
          sender: 'bot',
          text: text,
          formattedText: this.formatMarkdown(text),
          timestamp: new Date(),
          chips: ['Scan Databricks', 'Scan Snowflake', 'Scan Azure Synapse', 'Database Scanner Overview']
        }
      ];
      this.shouldScroll = true;
      this.cdr.detectChanges();
      return;
    }

    this.sendMessage(query);
  }

  handleAction(action: ChatAction): void {
    if (!action) return;

    if (action.actionKey === 'start_db_scan') {
      this.sendMessage('Start New DB Scan');
      return;
    }

    if (action.prompt) {
      this.sendMessage(action.prompt);
      return;
    }

    if (action.route) {
      if (action.route === '/dbscanner') {
        const src = action.queryParams?.['source'];
        if (src) {
          this.scanner.requestOpenConnection(src);
        }
        this.router.navigate([action.route], { queryParams: { ...action.queryParams, _t: Date.now().toString() } });

        const srcName = src ? this.formatSourceName(src) : null;
        const text = srcName 
          ? `🚀 **Database Scanner Launched for ${srcName}**\n\nI have loaded the **Database Scanner** with the **${srcName} Connection Modal** on your screen.\n\n• **Step 1:** Enter or confirm your credentials in the dialog.\n• **Step 2:** Click **Connect** — your assessment scan starts immediately upon authentication.\n• **Step 3:** The **Live Execution & Assessment Console** will stream real-time logs and generate AI migration reports.\n\n*The chatbot will remain open here for any questions.*`
          : `🚀 **Database Scanner Opened**\n\nI have loaded the **Database Scanner** on your screen. You can select your database source to begin a cloud assessment scan.`;

        this.messages = [
          ...this.messages,
          {
            id: 'bot-' + Date.now(),
            sender: 'bot',
            text: text,
            formattedText: this.formatMarkdown(text),
            timestamp: new Date(),
            chips: ['Start New DB Scan', 'Scan SQL Server', 'Scan Databricks', 'Scan Snowflake', 'Database Scanner Overview']
          }
        ];
        this.shouldScroll = true;
        this.cdr.detectChanges();
        return;
      } else if (action.route === '/medication') {
        const text = `🏥 **Medication Adherence POC Opened**\n\nI have loaded the **AI-Driven Medication Adherence Intelligence** dashboard on your screen. You can explore patient adherence risk scoring, clinician recommendations, and population analytics.`;
        this.messages = [
          ...this.messages,
          {
            id: 'bot-' + Date.now(),
            sender: 'bot',
            text: text,
            formattedText: this.formatMarkdown(text),
            timestamp: new Date(),
            chips: ['Start New DB Scan', 'BI Migrator Details', 'What is Microsoft Fabric?']
          }
        ];
        this.shouldScroll = true;
        this.cdr.detectChanges();
        return;
      } else if (action.route === '/bimigrator') {
        const text = `📊 **BI Migrator POC Opened**\n\nI have loaded the **BI Modernization Accelerator** on your screen. You can upload legacy Tableau, Cognos, or Qlik reports to convert into Power BI DAX and Microsoft Fabric Lakehouse bindings.`;
        this.messages = [
          ...this.messages,
          {
            id: 'bot-' + Date.now(),
            sender: 'bot',
            text: text,
            formattedText: this.formatMarkdown(text),
            timestamp: new Date(),
            chips: ['Start New DB Scan', 'Medication Adherence POC', 'Database Scanner Overview']
          }
        ];
        this.shouldScroll = true;
        this.cdr.detectChanges();
        return;
      }
    }

    // Fallback: If action only had a label, treat label as prompt
    if (action.label) {
      const cleanLabel = action.label.replace(/^[^\w\s]+/, '').trim();
      this.selectPrompt(cleanLabel || action.label);
    }
  }

  // =========================================================================
  // KNOWLEDGE BASE & INTENT DISPATCH
  // =========================================================================

  private generateBotResponse(query: string): ChatMessage {
    const lower = query.toLowerCase();

    // 0. Return to Home
    if (lower === 'return to home' || lower === 'go to home' || lower === 'home page' || lower === 'home') {
      this.router.navigate(['/']);
      const text = `🏠 **Navigated to Home Dashboard**\n\nI have loaded the **Home Dashboard** on your screen. Explore our featured POC accelerators, system architecture, or ask me any question!`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Start New DB Scan', prompt: 'Start New DB Scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
          { label: '🏥 Medication Adherence POC', route: '/medication', icon: 'bi bi-capsule' },
          { label: '📊 BI Migrator', route: '/bimigrator', icon: 'bi bi-bar-chart-line-fill' }
        ],
        chips: ['Start New DB Scan', 'Explain ER Diagrams', 'Scan SQL Server', 'Scan Databricks', 'BI Migrator Overview']
      };
    }

    // 1. General Database Scan Request
    if (this.isGeneralScanRequest(lower) || lower.includes('start scan') || lower.includes('scan database') || lower.includes('run scan')) {
      return this.buildStartScanPromptResponse();
    }

    // 2. Specific Database Engine Scans
    if (lower.includes('scan sql server') || lower.includes('sql server scan') || lower.includes('mssql')) {
      const text = `🗄️ **SQL Server Assessment & Migration to Microsoft Fabric**\n\nTo scan a Microsoft SQL Server database:\n1. Click below to launch the **Database Scanner**.\n2. Enter your SQL Server host, database, and credentials in the native connection console.\n3. The **Live Execution & Assessment Console** will automatically stream extraction, constraint validation, and generate migration reports.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Launch SQL Server Scanner', route: '/dbscanner', queryParams: { source: 'sqlserver', connect: '1' }, icon: 'bi bi-database-fill-gear', variant: 'primary' },
          { label: 'Open Database Scanner Page', route: '/dbscanner', icon: 'bi bi-database-check' }
        ],
        chips: ['Scan Databricks', 'Scan Snowflake', 'Scan Azure Synapse', 'Database Scanner Overview']
      };
    }

    if (lower.includes('scan databricks') || lower.includes('databricks scan')) {
      const text = `🧱 **Databricks Unity Catalog Assessment**\n\nTo scan Databricks Unity Catalog schemas:\n1. Click below to open the **Database Scanner** with Databricks selected.\n2. Provide your Databricks server hostname, Catalog name, Personal Access Token (PAT), and HTTP Path.\n3. Watch live table analysis and Fabric Direct Lake synchronization in the **Live Execution Console**.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Launch Databricks Scanner', route: '/dbscanner', queryParams: { source: 'Databricks', connect: '1' }, icon: 'bi bi-layers', variant: 'primary' },
          { label: 'Open Database Scanner Page', route: '/dbscanner', icon: 'bi bi-database-check' }
        ],
        chips: ['Scan SQL Server', 'Scan Snowflake', 'Scan Azure Synapse', 'Fabric Migration Plan']
      };
    }

    if (lower.includes('scan snowflake') || lower.includes('snowflake scan')) {
      const text = `❄️ **Snowflake Assessment & Fabric Modernization**\n\nTo scan a Snowflake data warehouse:\n1. Open the **Database Scanner** for Snowflake.\n2. Connect via Programmatic Access Token (PAT) or username/password with account, warehouse, and role.\n3. Live schemas and ER diagrams will be visualized in the assessment console.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Launch Snowflake Scanner', route: '/dbscanner', queryParams: { source: 'Snowflake', connect: '1' }, icon: 'bi bi-snow', variant: 'primary' },
          { label: 'Open Database Scanner Page', route: '/dbscanner', icon: 'bi bi-database-check' }
        ],
        chips: ['Scan SQL Server', 'Scan Databricks', 'Scan Azure Synapse', 'Database Scanner Overview']
      };
    }

    if (lower.includes('scan synapse') || lower.includes('synapse scan') || lower.includes('azure synapse')) {
      const text = `⚡ **Azure Synapse SQL Pool Assessment**\n\nTo scan Azure Synapse dedicated or serverless SQL pools:\n1. Click below to launch the **Database Scanner** for Synapse.\n2. Enter your Synapse SQL endpoint (\`<workspace>.sql.azuresynapse.net\`) and database.\n3. The scan analyzes external tables, views, stored procedures, and migration readiness.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Launch Azure Synapse Scanner', route: '/dbscanner', queryParams: { source: 'Synapse', connect: '1' }, icon: 'bi bi-cloud-arrow-up-fill', variant: 'primary' },
          { label: 'Open Database Scanner Page', route: '/dbscanner', icon: 'bi bi-database-check' }
        ],
        chips: ['Scan SQL Server', 'Scan Databricks', 'Scan Snowflake', 'Database Scanner Overview']
      };
    }

    if (lower.includes('dynamics 365') || lower.includes('dynamics') || lower.includes('dataverse') || lower.includes('d365')) {
      const text = `💼 **Dynamics 365 / Dataverse Assessment**\n\nTo scan Microsoft Dynamics 365 or Dataverse environments:\n1. Launch the **Database Scanner** for Dynamics 365.\n2. Authenticate via Azure AD App Registration (Client Secret) or direct user credentials.\n3. Extract entity schemas, relationships, and prepare Microsoft Fabric Lakehouse shortcuts.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Launch Dynamics 365 Scanner', route: '/dbscanner', queryParams: { source: 'dynamics365', connect: '1' }, icon: 'bi bi-briefcase-fill', variant: 'primary' },
          { label: 'Open Database Scanner Page', route: '/dbscanner', icon: 'bi bi-database-check' }
        ],
        chips: ['Scan SQL Server', 'Scan Databricks', 'Database Scanner Overview']
      };
    }

    // 3. Medication Adherence
    if (lower.includes('medication') || lower.includes('adherence') || lower.includes('healthcare') || lower.includes('patient') || lower.includes('capsule')) {
      const text = `🏥 **AI-Driven Medication Adherence Intelligence**\n\nThis healthcare POC delivers predictive clinical intelligence:\n\n• **Predictive Adherence Scoring:** Identifies patients at risk of therapy gaps using machine learning.\n• **Intervention Recommendations:** Provides clinician-tailored intervention pathways.\n• **Population Health Analytics:** Interactive dashboards for clinical teams across demographics.\n\nExplore the interactive dashboard below:`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🏥 Open Medication Adherence', route: '/medication', icon: 'bi bi-capsule', variant: 'primary' }
        ],
        chips: ['Start New DB Scan', 'BI Migrator Overview', 'Return to Home']
      };
    }

    // 4. BI Migrator
    if (lower.includes('bi') || lower.includes('migrator') || lower.includes('tableau') || lower.includes('cognos') || lower.includes('qlik') || lower.includes('power bi') || lower.includes('powerbi') || lower.includes('report') || lower.includes('dashboard')) {
      const text = `📊 **BI Modernization Accelerator**\n\nAutomates legacy reporting migration into Microsoft Fabric & Power BI:\n\n• **Legacy BI Ingestion:** Parses Tableau (.twb/.twbx), Cognos, and Qlik reports.\n• **Automated DAX & Semantic Models:** AI Pipeline converts formulas and measures into Power BI DAX.\n• **Visual Mapping & Fabric Integration:** Prepares target Power BI report definitions and Lakehouse bindings.\n• **Migration Acceleration:** Up to 70% reduction in report rebuilding time.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '📊 Open BI Migrator', route: '/bimigrator', icon: 'bi bi-bar-chart-line-fill', variant: 'primary' }
        ],
        chips: ['Start New DB Scan', 'Medication Adherence POC', 'Database Scanner Overview']
      };
    }

    // 5. ER Diagrams & Schema Relationships
    if (this.isErDiagramQuery(lower)) {
      return this.buildErConceptResponse(query);
    }

    // 6. Primary & Foreign Keys
    if ((lower.includes('primary') && lower.includes('foreign') && lower.includes('key')) || lower.includes('primary key') || lower.includes('foreign key')) {
      const text = `🔑 **Primary Keys vs. Foreign Keys in Database Modernization**\n\n• **Primary Key (PK):**\n  - Enforces entity uniqueness and prevents duplicate records.\n  - Example: \`CustomerID\` uniquely identifies each record in the \`Customers\` table.\n  - In Microsoft Fabric OneLake, primary keys serve as unique join keys for Delta tables.\n\n• **Foreign Key (FK):**\n  - Establishes referential integrity between parent and child tables.\n  - Example: \`Orders.CustomerID\` references \`Customers.CustomerID\`, ensuring orders link to valid customers.\n  - Enforces 1-to-Many or Many-to-Many cardinality.\n\n• **Declared vs. Inferred Keys:**\n  - **Declared Keys:** Constraints explicitly enforced by the database system catalogs.\n  - **Inferred Keys:** AI-discovered associations derived by matching column naming patterns like \`*_id\` and \`*_key\` when constraints are not explicitly declared.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '📊 View ER Diagrams in Scanner', route: '/dbscanner', icon: 'bi bi-diagram-3-fill', variant: 'primary' },
          { label: '🚀 Start New DB Scan', prompt: 'Start New DB Scan', icon: 'bi bi-play-circle-fill', variant: 'secondary' }
        ],
        chips: ['Explain ER Diagrams', 'Fabric Delta Lake Mapping', 'Start New DB Scan', 'Database Scanner Overview']
      };
    }

    // 7. Microsoft Fabric Delta Lake & Direct Lake Mapping
    if (lower.includes('delta lake mapping') || lower.includes('direct lake') || lower.includes('delta mapping')) {
      const text = `🌊 **Microsoft Fabric Delta Lake & Direct Lake Mapping**\n\nWhen modernizing relational ER schemas to Microsoft Fabric:\n\n• **OneLake Delta Parquet:** Tables are converted to open Apache Parquet format with ACID transaction logs (\`_delta_log\`), providing scalable columnar storage.\n• **Direct Lake Mode in Power BI:** Queries read Delta tables directly in OneLake at memory speed without importing or duplicating data.\n• **Star Schema Optimization:** Relational 3NF schemas are optimized into Fact and Dimension tables for high-performance reporting.\n• **Automated Pipelines:** Fabric Data Pipelines orchestrate data flow and Delta synchronization.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '📊 View ER Diagrams in Scanner', route: '/dbscanner', icon: 'bi bi-diagram-3-fill', variant: 'primary' },
          { label: '🚀 Start New DB Scan', prompt: 'Start New DB Scan', icon: 'bi bi-play-circle-fill', variant: 'secondary' }
        ],
        chips: ['Explain ER Diagrams', 'Explain Primary & Foreign Keys', 'Start New DB Scan', 'Microsoft Fabric Info']
      };
    }

    // 8. Microsoft Fabric & Cloud Architecture
    if (lower.includes('fabric') || lower.includes('azure') || lower.includes('cloud') || lower.includes('architecture') || lower.includes('onelake') || lower.includes('openai')) {
      const text = `☁️ **Azure & Microsoft Fabric Core Capabilities**\n\nThe Innovation Hub integrates modern enterprise cloud & AI services:\n\n• **Microsoft Fabric:** Unified OneLake storage, Delta Lake tables, and Direct Lake Power BI.\n• **Azure OpenAI / LLM Multi-Agent System:** Automated schema extraction, reasoning, and report generation.\n• **Harness AI Layers:** Layered validation ensuring deterministic migration plans.\n• **Enterprise Governance:** End-to-end security and automated governance validation.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Start New DB Scan', prompt: 'Start New DB Scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
          { label: 'Explore BI Migrator', route: '/bimigrator', icon: 'bi bi-bar-chart-line-fill' }
        ],
        chips: ['Start New DB Scan', 'Explain ER Diagrams', 'Medication Adherence POC', 'What is BI Migrator?']
      };
    }

    // 9. Database Scanner General Info
    if (lower.includes('database') || lower.includes('scanner') || lower.includes('schema') || lower.includes('readiness')) {
      const text = `🗄️ **Database Assessment & Migration Readiness Scanner**\n\nThis accelerator automates legacy database evaluation for cloud modernization:\n\n• **Multi-Engine Support:** SQL Server, Synapse, Snowflake, Databricks, Dynamics 365, Oracle, and MySQL.\n• **AI-Driven Assessment:** Evaluates schema complexity, dependencies, data types, and stored procedures.\n• **Readiness & Complexity Scoring:** Produces detailed readiness scores, risk flags, and conversion effort estimations.\n• **Live Console & ER Diagrams:** Tracks execution live with detailed diagnostic logs, tokenomics, and interactive ER diagrams.`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Start New DB Scan', prompt: 'Start New DB Scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
          { label: '📊 View ER Diagrams in Scanner', route: '/dbscanner', icon: 'bi bi-diagram-3-fill' }
        ],
        chips: ['Start New DB Scan', 'Explain ER Diagrams', 'Scan SQL Server', 'Scan Databricks']
      };
    }

    // 10. Help / Greetings / General
    if (lower.includes('hello') || lower.includes('hi') || lower.includes('hey') || lower.includes('help') || lower.includes('who are you') || lower.includes('what can you do')) {
      const text = `👋 Hello! I am the **Apexon AI Innovation Hub Assistant**.\n\nI can assist you with:\n1. **Database Scanner & ER Diagrams** – Automated metadata extraction, ER modeling & Fabric assessment.\n2. **Medication Adherence** – AI Healthcare Insights & Prediction.\n3. **BI Migrator** – Modernizing Tableau, Cognos, & Qlik to Power BI / Fabric.\n4. **Azure & Fabric Architecture** – Cloud modernization best practices.\n\nSelect a topic or type your question below!`;
      return {
        id: 'bot-' + Date.now(),
        sender: 'bot',
        text: text,
        formattedText: this.formatMarkdown(text),
        timestamp: new Date(),
        actions: [
          { label: '🚀 Start New DB Scan', prompt: 'Start New DB Scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
          { label: '📊 Explain ER Diagrams', prompt: 'Explain the ER diagrams and schema relationships', icon: 'bi bi-diagram-3-fill' },
          { label: '🏥 Medication Adherence', route: '/medication', icon: 'bi bi-capsule' }
        ],
        chips: ['Start New DB Scan', 'Explain ER Diagrams', 'Scan SQL Server', 'Scan Databricks', 'Medication Adherence POC', 'BI Migrator Overview']
      };
    }

    // Fallback response
    const text = `I understand you are asking about: *"${query}"*.\n\nIn the **Apexon AI Innovation Hub**, we specialize in accelerating digital transformation across three flagship POCs:\n\n• **Database Scanner:** Automated metadata extraction & Fabric migration assessment.\n• **BI Migrator:** Automated migration of legacy BI reports into Power BI & Microsoft Fabric.\n• **Medication Adherence:** AI-powered patient risk analytics & healthcare insights.\n\nPlease choose an area below or launch a live scan:`;
    return {
      id: 'bot-' + Date.now(),
      sender: 'bot',
      text: text,
      formattedText: this.formatMarkdown(text),
      timestamp: new Date(),
      actions: [
        { label: '🚀 Start New DB Scan', prompt: 'Start New DB Scan', icon: 'bi bi-play-circle-fill', variant: 'primary' },
        { label: 'Go to BI Migrator', route: '/bimigrator', icon: 'bi bi-bar-chart-line-fill' },
        { label: 'Go to Medication Adherence', route: '/medication', icon: 'bi bi-capsule' }
      ],
      chips: ['Start New DB Scan', 'Scan SQL Server', 'Scan Databricks', 'Tell me about Medication Adherence', 'BI Migrator Overview']
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
  // STATE MANAGEMENT (FRESH STATE ON REFRESH - ZERO CREDENTIALS STORED)
  // =========================================================================

  private isBrowser(): boolean {
    return typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined';
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
      window.sessionStorage.removeItem('apexon_active_scan');
    } catch (err) {
      console.warn('Could not clear session storage:', err);
    }
  }
}
