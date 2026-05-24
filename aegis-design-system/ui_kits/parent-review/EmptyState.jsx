// ui_kits/parent-review/EmptyState.jsx
function EmptyState() {
  return (
    <div className="card-hairline" style={{
      textAlign: 'center',
      padding: '64px 32px',
    }}>
      <img src="../../assets/aegis-shield.svg" width="56" height="63" alt="" style={{ opacity: 0.5, marginBottom: 16 }} />
      <div style={{ fontFamily: 'var(--font-display)', fontSize: 32, color: 'var(--fg-1)', lineHeight: 1.1, marginBottom: 8 }}>
        Nothing pending.
      </div>
      <div style={{ fontSize: 16, color: 'var(--fg-2)' }}>Quiet afternoon.</div>
    </div>
  );
}

window.EmptyState = EmptyState;
