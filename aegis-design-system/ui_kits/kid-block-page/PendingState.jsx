// ui_kits/kid-block-page/PendingState.jsx
function PendingState({ submittedAgo, onCheck, checking }) {
  return (
    <div style={{
      marginTop: 28,
      background: 'var(--aegis-pending-bg)',
      borderRadius: 20,
      padding: '24px 26px',
      animation: 'fadeUp var(--dur-slow) var(--ease-out)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span className="pill pill-pending"><span className="dot"></span>Waiting on your parent</span>
        <span style={{ fontSize: 13, color: 'var(--fg-2)' }}>sent {submittedAgo}</span>
      </div>
      <div style={{ fontFamily: 'var(--font-display)', fontSize: 24, lineHeight: 1.2, color: 'var(--fg-1)', marginBottom: 6 }}>
        We sent your request.
      </div>
      <p style={{ fontSize: 15, color: 'var(--fg-2)', margin: 0, marginBottom: 18, lineHeight: 1.5 }}>
        They'll see it next time they open the review page. You can keep browsing other stuff — we'll let you know.
      </p>
      <button className="btn btn-secondary" onClick={onCheck} disabled={checking} style={{ background: 'var(--bg-1)' }}>
        {checking ? 'Checking…' : 'Check if approved'}
      </button>
    </div>
  );
}

window.PendingState = PendingState;
