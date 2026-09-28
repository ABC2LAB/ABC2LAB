import { Minus, Plus } from 'lucide-react';
import { graphLegend, graphLinks, graphNodes } from '../../data/mockData.js';

const nodeColors = {
  role: '#6548ca', page: '#f7941d', endpoint: '#2789dc', resource: '#ea6268',
};

const nodeMarks = { role: 'Ur', page: 'Pg', endpoint: 'Ep', resource: 'Db' };

export default function GraphCanvas() {
  const nodesById = Object.fromEntries(graphNodes.map((node) => [node.id, node]));

  return (
    <div className="graph-workspace">
      <div className="graph-toolbar">
        <div><button aria-label="확대"><Plus size={17} /></button><button aria-label="축소"><Minus size={17} /></button></div>
      </div>
      <svg className="graph-canvas" viewBox="0 0 1000 500" role="img" aria-label="웹 자산과 취약점 간 관계 그래프">
        <defs>
          <pattern id="dotGrid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#263449" /></pattern>
          <filter id="nodeGlow"><feGaussianBlur stdDeviation="4" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        <rect width="1000" height="500" fill="url(#dotGrid)" />
        <g className="graph-links">
          {graphLinks.map((link) => {
            const source = nodesById[link.source]; const target = nodesById[link.target];
            return <line key={`${link.source}-${link.target}`} x1={source.x + 26} y1={source.y} x2={target.x - 26} y2={target.y} />;
          })}
        </g>
        <g className="graph-nodes">
          {graphNodes.map((node) => {
            const labelWidth = Math.max(92, node.label.length * 7 + 42);
            return <g key={node.id} transform={`translate(${node.x}, ${node.y})`} className={`graph-node ${node.type}`}>
              <rect x="-20" y="-20" width={labelWidth} height="40" rx="20" />
              <circle r="20" fill={nodeColors[node.type]} />
              <text className="node-mark" x="0" y="3">{nodeMarks[node.type]}</text>
              <text className="node-label" x="30" y="4">{node.label}</text>
            </g>;
          })}
        </g>
      </svg>
      <div className="graph-legend">{graphLegend.map((item) => <span key={item.type}><i style={{ background: nodeColors[item.type] }} />{item.label}</span>)}</div>
    </div>
  );
}
