import { Component, ElementRef, afterRenderEffect, computed, effect, inject, input, signal, untracked, viewChild } from '@angular/core';
import { ErDiagramResponse, ErRelationship, ErTable, Scanner } from '../../services/scanner/scanner';

type ColumnMode = 'all' | 'keys';
type Mermaid = typeof import('mermaid').default;

// Above this many columns in total the diagram starts in "key columns only"
// mode - every column of every table is unreadable once fitted to the view.
const KEYS_ONLY_THRESHOLD = 120;

// Mermaid is large, so it is only fetched the first time a diagram is drawn
// instead of being part of the initial bundle.
let mermaidLoader: Promise<Mermaid> | null = null;

function loadMermaid(): Promise<Mermaid> {
  mermaidLoader ??= import('mermaid').then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      suppressErrorRendering: true,
      maxTextSize: 2_000_000,
      maxEdges: 5000,
      theme: 'base',
      er: { useMaxWidth: false },
      themeVariables: {
        darkMode: true,
        fontFamily: 'Segoe UI, Tahoma, Geneva, Verdana, sans-serif',
        background: '#030c18',
        mainBkg: '#0b1f36',
        nodeBorder: '#FF5A1F',
        nodeTextColor: '#ffffff',
        textColor: '#e6edf5',
        lineColor: '#8a99ad',
        edgeLabelBackground: '#061528',
        tertiaryColor: '#061528',
        rowOdd: '#061528',
        rowEven: '#0a1c31',
      },
    });
    return mermaid;
  });
  return mermaidLoader;
}

// Mermaid ER identifiers allow letters, digits, and underscores (types also
// parentheses, commas, hyphens, and brackets), and must not start with a digit.
function mermaidName(name: string): string {
  const cleaned = name.replace(/[^A-Za-z0-9_]/g, '_');
  return /^[A-Za-z_]/.test(cleaned) ? cleaned : `_${cleaned}`;
}

function mermaidType(type: string): string {
  const cleaned = (type || 'UNKNOWN')
    .trim()
    .replace(/\s*,\s*/g, ',')
    .replace(/\s+/g, '_')
    .replace(/[^A-Za-z0-9_(),\-[\]]/g, '');
  return /^[A-Za-z_]/.test(cleaned) ? cleaned : `_${cleaned}`;
}

function quoted(text: string): string {
  return `"${text.replace(/"/g, "'")}"`;
}

export function buildMermaid(tables: ErTable[], relationships: ErRelationship[], keysOnly: boolean): string {
  // Positional ids sidestep schema.table names that clean up to the same
  // identifier; the real name is shown through the entity alias.
  const ids = new Map(tables.map((table, i) => [table.id, `T${i}`]));
  const lines = ['erDiagram'];

  for (const table of tables) {
    const columns = keysOnly ? table.columns.filter(c => c.pk || c.fk) : table.columns;
    lines.push(`  ${ids.get(table.id)}[${quoted(table.id)}] {`);
    for (const column of columns) {
      const keys = [column.pk ? 'PK' : '', column.fk ? 'FK' : ''].filter(Boolean).join(', ');
      lines.push(`    ${mermaidType(column.type)} ${mermaidName(column.name)}${keys ? ' ' + keys : ''}`);
    }
    lines.push('  }');
  }

  for (const rel of relationships) {
    const from = ids.get(rel.from);
    const to = ids.get(rel.to);
    if (!from || !to) continue;
    // The referenced table is the "one" side. Dashed (..) marks an inferred
    // relationship, solid (--) a declared foreign key.
    lines.push(`  ${to} ||${rel.inferred ? '..' : '--'}o{ ${from} : ${quoted(rel.from_column)}`);
  }

  return lines.join('\n');
}

@Component({
  selector: 'app-er-diagram',
  templateUrl: './er-diagram.html',
  styleUrl: './er-diagram.css',
})
export class ErDiagram {
  /** True once the scan has completed - the diagram is only built then. */
  readonly ready = input(false);
  /** output_files.fabric_migration_metadata of the completed scan. */
  readonly metadataFile = input<string | undefined>();

  private scanner = inject(Scanner);
  private canvas = viewChild.required<ElementRef<HTMLDivElement>>('canvas');

  readonly model = signal<ErDiagramResponse | null>(null);
  readonly loading = signal(false);
  readonly drawing = signal(false);
  readonly error = signal('');
  readonly schema = signal('all');
  readonly columnMode = signal<ColumnMode>('all');
  readonly zoom = signal(1);

  readonly zoomPercent = computed(() => Math.round(this.zoom() * 100));
  readonly schemas = computed(() => [...new Set((this.model()?.tables ?? []).map(t => t.schema))].sort());
  readonly showCanvas = computed(() => this.ready() && !this.loading() && !this.error() && !!this.model()?.tables.length);

  readonly visible = computed(() => {
    const model = this.model();
    if (!model) return { tables: [] as ErTable[], relationships: [] as ErRelationship[] };
    const schema = this.schema();
    if (schema === 'all') return { tables: model.tables, relationships: model.relationships };

    const ids = new Set(model.tables.filter(t => t.schema === schema).map(t => t.id));
    const relationships = model.relationships.filter(r => ids.has(r.from) || ids.has(r.to));
    // Keep the other-schema tables this schema links to, so cross-schema
    // relationships stay visible instead of silently disappearing.
    relationships.forEach(r => ids.add(r.from).add(r.to));
    return { tables: model.tables.filter(t => ids.has(t.id)), relationships };
  });

  private readonly mermaidText = computed(() => {
    const { tables, relationships } = this.visible();
    return tables.length ? buildMermaid(tables, relationships, this.columnMode() === 'keys') : '';
  });

  private drawSeq = 0;
  private naturalSize = { width: 0, height: 0 };

  constructor() {
    effect(() => {
      const ready = this.ready();
      const file = this.metadataFile();
      untracked(() => (ready ? this.load(file) : this.model.set(null)));
    });

    afterRenderEffect(() => {
      const text = this.mermaidText();
      untracked(() => {
        if (text) {
          void this.draw(text);
        } else {
          this.drawSeq++;
          this.canvas().nativeElement.innerHTML = '';
        }
      });
    });
  }

  reload() {
    this.load(this.metadataFile());
  }

  zoomBy(factor: number) {
    this.setZoom(this.zoom() * factor);
  }

  /**
   * Fits the whole diagram into the view. On first draw, a diagram that
   * would shrink below 50% that way is fitted to the width only, so its
   * text stays readable and the rest is a scroll away.
   */
  fit(initial = false) {
    const host = this.canvas().nativeElement;
    const { width, height } = this.naturalSize;
    if (!width || !height) return;
    const byWidth = (host.clientWidth - 32) / width;
    const byHeight = (host.clientHeight - 32) / height;
    const whole = Math.min(1, byWidth, byHeight);
    this.setZoom(initial && whole < 0.5 ? Math.min(1, byWidth) : whole);
  }

  download() {
    const svg = this.canvas().nativeElement.querySelector('svg');
    if (!svg) return;
    const copy = svg.cloneNode(true) as SVGSVGElement;
    copy.removeAttribute('style');
    copy.setAttribute('width', String(this.naturalSize.width));
    copy.setAttribute('height', String(this.naturalSize.height));
    // The theme is light-on-dark, so carry the background into the file.
    copy.style.backgroundColor = '#061528';

    const blob = new Blob([new XMLSerializer().serializeToString(copy)], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const model = this.model();
    const stem = [model?.source, model?.database, this.schema() === 'all' ? '' : this.schema()]
      .filter(Boolean)
      .join('_')
      .replace(/[^A-Za-z0-9_-]+/g, '_');
    link.href = url;
    link.download = `${stem || 'scan'}_ER_Diagram.svg`;
    link.click();
    URL.revokeObjectURL(url);
  }

  private load(file: string | undefined) {
    this.loading.set(true);
    this.error.set('');
    this.model.set(null);
    this.scanner.getErDiagram(file).subscribe({
      next: (response) => {
        this.schema.set('all');
        const columnCount = response.tables.reduce((sum, t) => sum + t.columns.length, 0);
        this.columnMode.set(columnCount > KEYS_ONLY_THRESHOLD ? 'keys' : 'all');
        this.model.set(response);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err.error?.message ?? 'Could not load the ER diagram for this scan.');
        this.loading.set(false);
      },
    });
  }

  private async draw(text: string) {
    const seq = ++this.drawSeq;
    this.drawing.set(true);
    try {
      const mermaid = await loadMermaid();
      const { svg } = await mermaid.render(`er-diagram-${seq}`, text);
      if (seq !== this.drawSeq) return;

      const host = this.canvas().nativeElement;
      host.innerHTML = svg;
      const element = host.querySelector('svg');
      const box = element?.viewBox.baseVal;
      this.naturalSize = {
        width: box?.width || element?.getBoundingClientRect().width || 0,
        height: box?.height || element?.getBoundingClientRect().height || 0,
      };
      this.fit(true);
    } catch (err) {
      if (seq === this.drawSeq) {
        this.error.set(`Could not draw the ER diagram: ${err instanceof Error ? err.message : err}`);
      }
    } finally {
      if (seq === this.drawSeq) this.drawing.set(false);
    }
  }

  private setZoom(value: number) {
    const zoom = Math.min(3, Math.max(0.1, value));
    this.zoom.set(zoom);
    const svg = this.canvas().nativeElement.querySelector('svg');
    if (svg) {
      svg.style.maxWidth = 'none';
      svg.style.width = `${this.naturalSize.width * zoom}px`;
      svg.style.height = `${this.naturalSize.height * zoom}px`;
    }
  }
}
