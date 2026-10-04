/* CognitiveGraph: layered semantic graph of agents and their relations.
   Pure SVG, no dependencies. Columns run left to right by generation.
   Data only: every node and edge comes from props; nothing is inferred. */

import { useMemo } from 'react';
import { Empty } from './ui';

export type CognitiveEdgeKind =
  | 'delegation'
  | 'sibling'
  | 'dependency'
  | 'message'
  | 'evidence'
  | 'verification'
  | 'lineage';

export interface CognitiveNode {
  id: string;
  role: string;
  status: string;
  generation?: number | null;
}

export interface CognitiveEdge {
  from: string;
  to: string;
  kind: CognitiveEdgeKind;
}

interface CognitiveGraphProps {
  nodes: CognitiveNode[];
  edges: CognitiveEdge[];
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
  height?: number;
}

const C = {
  text: '#d7dde6',
  faint: '#647080',
  node: '#161d29',
  border: '#2c3a4e',
  accent: '#5b8def',
};

const NODE_W = 176;
const NODE_H = 62;
const COL_GAP = 100;
const ROW_GAP = 18;
const PAD = 28;

function statusColor(status: string): string {
  const s = status.toUpperCase();
  if (s === 'RUNNING' || s === 'CREATED' || s === 'ACTIVE') return '#5b8def';
  if (s === 'COMPLETED') return '#3fb27f';
  if (s === 'BLOCKED' || s === 'WAITING' || s === 'PAUSED') return '#d9a13b';
  if (s === 'FAILED' || s === 'DENIED' || s === 'CANCELLED' || s === 'TERMINATED') return '#e05d5d';
  return '#647080';
}

const EDGE_STYLE: Record<CognitiveEdgeKind, { color: string; dash: string; width: number }> = {
  delegation: { color: '#8b98ab', dash: '', width: 1.6 },
  sibling: { color: '#647080', dash: '2 4', width: 1.2 },
  dependency: { color: '#647080', dash: '', width: 1.2 },
  message: { color: '#5b8def', dash: '6 4', width: 1.4 },
  evidence: { color: '#3fb27f', dash: '6 4', width: 1.4 },
  verification: { color: '#d9a13b', dash: '6 4', width: 1.4 },
  lineage: { color: '#9aa6b5', dash: '2 4', width: 1.2 },
};

const KIND_LABEL: Record<CognitiveEdgeKind, string> = {
  delegation: 'Delegation',
  sibling: 'Sibling',
  dependency: 'Dependency',
  message: 'Message',
  evidence: 'Evidence',
  verification: 'Verification',
  lineage: 'Lineage',
};

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}...` : s;
}

interface PlacedNode extends CognitiveNode {
  x: number;
  y: number;
}

function edgePath(a: PlacedNode, b: PlacedNode): string {
  const x1 = a.x + NODE_W;
  const y1 = a.y + NODE_H / 2;
  const x2 = b.x;
  const y2 = b.y + NODE_H / 2;
  const dx = Math.max(30, COL_GAP / 2);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

export default function CognitiveGraph({
  nodes,
  edges,
  selectedId,
  onSelect,
  height = 380,
}: CognitiveGraphProps) {
  const { placed, viewW, viewH, drawn } = useMemo(() => {
    const byGen = new Map<number, CognitiveNode[]>();
    for (const n of nodes) {
      const g = n.generation ?? 0;
      const arr = byGen.get(g);
      if (arr) arr.push(n);
      else byGen.set(g, [n]);
    }
    const gens = [...byGen.keys()].sort((a, b) => a - b);
    const placed = new Map<string, PlacedNode>();
    let needH = PAD * 2;
    gens.forEach((g, gi) => {
      const col = byGen.get(g)!;
      const colH = col.length * NODE_H + (col.length - 1) * ROW_GAP;
      needH = Math.max(needH, colH + PAD * 2);
      const x = PAD + gi * (NODE_W + COL_GAP);
      const top = PAD + Math.max(0, (height - PAD * 2 - colH) / 2);
      col.forEach((n, i) => {
        placed.set(n.id, { ...n, x, y: top + i * (NODE_H + ROW_GAP) });
      });
    });
    const viewW = gens.length === 0 ? 100 : PAD * 2 + gens.length * NODE_W + (gens.length - 1) * COL_GAP;
    const viewH = Math.max(height, needH);
    const drawn: { a: PlacedNode; b: PlacedNode; kind: CognitiveEdgeKind; key: string }[] = [];
    const seenEdge = new Set<string>();
    edges.forEach((e, i) => {
      const a = placed.get(e.from);
      const b = placed.get(e.to);
      if (!a || !b || e.from === e.to) return;
      const key = `${e.kind}:${e.from}->${e.to}`;
      if (seenEdge.has(key)) return;
      seenEdge.add(key);
      drawn.push({ a, b, kind: e.kind, key: `${key}:${i}` });
    });
    return { placed, viewW, viewH, drawn };
  }, [nodes, edges, height]);

  const legendKinds = useMemo(() => {
    const ks: CognitiveEdgeKind[] = [];
    for (const k of Object.keys(EDGE_STYLE) as CognitiveEdgeKind[]) {
      if (drawn.some((d) => d.kind === k)) ks.push(k);
    }
    return ks;
  }, [drawn]);

  if (nodes.length === 0) {
    return <Empty title="No agents yet" />;
  }

  const markerColors = [...new Set(drawn.map((d) => EDGE_STYLE[d.kind].color))];

  return (
    <div>
      <svg
        viewBox={`0 0 ${viewW} ${viewH}`}
        style={{ width: '100%', height: viewH, display: 'block', cursor: 'default' }}
        onClick={() => onSelect?.(null)}
      >
        <defs>
          {markerColors.map((c) => (
            <marker
              key={c}
              id={`arr-${c.replace('#', '')}`}
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 1 L 9 5 L 0 9 z" fill={c} />
            </marker>
          ))}
        </defs>
        {drawn.map((d) => {
          const st = EDGE_STYLE[d.kind];
          return (
            <path
              key={d.key}
              d={edgePath(d.a, d.b)}
              fill="none"
              stroke={st.color}
              strokeWidth={st.width}
              strokeDasharray={st.dash || undefined}
              markerEnd={`url(#arr-${st.color.replace('#', '')})`}
              opacity={0.85}
            />
          );
        })}
        {[...placed.values()].map((n) => {
          const selected = n.id === selectedId;
          const dot = statusColor(n.status);
          return (
            <g
              key={n.id}
              transform={`translate(${n.x} ${n.y})`}
              style={{ cursor: 'pointer' }}
              onClick={(e) => {
                e.stopPropagation();
                onSelect?.(n.id);
              }}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={4}
                fill={C.node}
                stroke={selected ? C.accent : C.border}
                strokeWidth={selected ? 2 : 1}
              />
              <circle cx={14} cy={NODE_H / 2} r={5} fill={dot} />
              <text x={28} y={24} fill={C.text} fontSize={12.5} fontWeight={600}>
                {truncate(n.role, 20)}
              </text>
              <text x={28} y={42} fill={C.faint} fontSize={10.5} fontFamily="ui-monospace, monospace">
                {truncate(n.status, 16)}
              </text>
              <text x={28} y={55} fill={C.faint} fontSize={10} fontFamily="ui-monospace, monospace">
                {truncate(n.id, 14)}
              </text>
            </g>
          );
        })}
      </svg>
      {legendKinds.length > 0 && (
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', padding: '8px 4px 2px' }}>
          {legendKinds.map((k) => {
            const st = EDGE_STYLE[k];
            return (
              <span key={k} className="faint" style={{ fontSize: 11.5, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <svg width={28} height={8}>
                  <line
                    x1={0}
                    y1={4}
                    x2={28}
                    y2={4}
                    stroke={st.color}
                    strokeWidth={st.width}
                    strokeDasharray={st.dash || undefined}
                  />
                </svg>
                {KIND_LABEL[k]}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
