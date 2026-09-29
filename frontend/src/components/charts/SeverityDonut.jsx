export default function SeverityDonut({ data }) {
  const total = data.reduce((sum, item) => sum + item.count, 0);
  const { segments } = data.reduce((chart, item) => {
    const nextCursor = chart.cursor + (item.count / total) * 360;
    return {
      cursor: nextCursor,
      segments: [...chart.segments, `${item.color} ${chart.cursor}deg ${nextCursor}deg`],
    };
  }, { cursor: 0, segments: [] });

  return (
    <div className="donut-block">
      <div className="donut" style={{ background: `conic-gradient(${segments.join(', ')})` }}>
        <div><strong>{total}</strong><span>전체 취약점</span></div>
      </div>
      <div className="donut-legend">
        {data.map((item) => <div key={item.severity}><span><i style={{ background: item.color }} />{item.label}</span><strong>{item.count}</strong></div>)}
      </div>
    </div>
  );
}
