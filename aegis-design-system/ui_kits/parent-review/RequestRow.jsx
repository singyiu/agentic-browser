// ui_kits/parent-review/RequestRow.jsx
function RequestRow({ req, expanded, onExpand, onApprove, onReject }) {
  const [allowEntry, setAllowEntry] = React.useState(req.url);
  const [rejectNote, setRejectNote] = React.useState('');
  const [mode, setMode] = React.useState(null); // 'approve' | 'reject' | null

  React.useEffect(() => {
    if (!expanded) setMode(null);
  }, [expanded]);

  const suggestions = React.useMemo(() => {
    try {
      const u = new URL(req.url);
      const host = u.hostname;
      const section = host + (u.pathname.split('/').slice(0, 2).join('/') || '');
      return [
        { label: 'this page', value: host + u.pathname },
        { label: 'this section', value: section + '*' },
        { label: 'whole site', value: host + '*' },
      ];
    } catch (e) {
      return [];
    }
  }, [req.url]);

  return (
    <article
      className="card"
      style={{
        padding: 0,
        boxShadow: expanded ? 'var(--shadow-lifted)' : 'var(--shadow-soft)',
        transition: 'box-shadow var(--dur-med) var(--ease-out)',
        overflow: 'hidden',
      }}
    >
      <div style={{ padding: '22px 26px', display: 'grid', gridTemplateColumns: '1fr auto', gap: 22, alignItems: 'center' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10 }}>
            <span className="pill pill-pending"><span className="dot"></span>Pending</span>
            <span style={{ fontSize: 13, color: 'var(--fg-2)' }}>
              from <strong style={{ color: 'var(--fg-1)' }}>{req.teen}</strong>
            </span>
          </div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 15, color: 'var(--fg-1)',
            marginBottom: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>{req.url}</div>
          <div className="meta-row">
            <span>{req.reason}</span>
            <span className="sep"></span>
            <span>asked {req.ago}</span>
          </div>
          {req.note && (
            <div style={{
              marginTop: 12,
              padding: '11px 14px',
              background: 'var(--bg-1)',
              borderRadius: 12,
              fontSize: 14,
              color: 'var(--fg-1)',
              fontStyle: 'italic',
              lineHeight: 1.5,
            }}>
              "{req.note}"
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, alignSelf: 'start', marginTop: 4 }}>
          <button
            className="btn btn-secondary"
            onClick={() => { onExpand(req.id); setMode('reject'); }}
          >Reject</button>
          <button
            className="btn btn-approve"
            onClick={() => { onExpand(req.id); setMode('approve'); }}
          >Approve</button>
        </div>
      </div>

      {expanded && mode === 'approve' && (
        <div style={{
          padding: '22px 26px',
          borderTop: '1px solid var(--border-1)',
          background: 'var(--bg-1)',
        }}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>Approve as</div>
          <input
            className="input"
            value={allowEntry}
            onChange={(e) => setAllowEntry(e.target.value)}
            style={{ fontFamily: 'var(--font-mono)', fontSize: 14 }}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            {suggestions.map(s => (
              <button
                key={s.value}
                className="pill pill-tag"
                style={{ cursor: 'pointer', border: 0, fontFamily: 'var(--font-mono)' }}
                onClick={() => setAllowEntry(s.value)}
              >
                {s.label} <span style={{ color: 'var(--fg-2)' }}>{s.value}</span>
              </button>
            ))}
            <button
              className="pill pill-tag"
              style={{ cursor: 'pointer', border: 0 }}
              onClick={() => setAllowEntry('BeyBlade anime')}
            >
              topic <span style={{ color: 'var(--fg-2)' }}>BeyBlade anime</span>
            </button>
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 18, justifyContent: 'flex-end' }}>
            <button className="btn btn-ghost" onClick={() => onExpand(null)}>Cancel</button>
            <button className="btn btn-approve" onClick={() => onApprove(req.id, allowEntry)}>
              Add to {req.teen}'s whitelist
            </button>
          </div>
        </div>
      )}

      {expanded && mode === 'reject' && (
        <div style={{
          padding: '22px 26px',
          borderTop: '1px solid var(--border-1)',
          background: 'var(--bg-1)',
        }}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>Note for {req.teen} (optional)</div>
          <textarea
            className="textarea"
            placeholder="e.g. let's find a school-safe version of this"
            value={rejectNote}
            onChange={(e) => setRejectNote(e.target.value)}
          />
          <div style={{ display: 'flex', gap: 10, marginTop: 14, justifyContent: 'flex-end' }}>
            <button className="btn btn-ghost" onClick={() => onExpand(null)}>Cancel</button>
            <button className="btn btn-danger" onClick={() => onReject(req.id, rejectNote)}>Reject request</button>
          </div>
        </div>
      )}
    </article>
  );
}

window.RequestRow = RequestRow;
