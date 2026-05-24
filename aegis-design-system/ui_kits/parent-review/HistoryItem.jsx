// ui_kits/parent-review/HistoryItem.jsx
function HistoryItem({ item }) {
  const decided = item.decision === 'approve';
  return (
    <div className="card-hairline" style={{
      padding: '14px 18px',
      display: 'grid',
      gridTemplateColumns: 'auto 1fr auto',
      gap: 14,
      alignItems: 'center',
      background: 'transparent',
    }}>
      <span className={`pill ${decided ? 'pill-allow' : 'pill-block'}`}>
        <span className="dot"></span>{decided ? 'Approved' : 'Rejected'}
      </span>
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--fg-1)',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {decided ? item.whitelist_entry : item.url}
        </div>
        <div className="meta-row" style={{ fontSize: 12, marginTop: 3 }}>
          <span>{item.teen}</span>
          <span className="sep"></span>
          <span>{item.ago}</span>
        </div>
      </div>
      <button className="btn btn-ghost btn-sm" style={{ color: 'var(--fg-2)' }}>Undo</button>
    </div>
  );
}

window.HistoryItem = HistoryItem;
