// ui_kits/kid-block-page/ApprovedState.jsx
function ApprovedState({ onOpen }) {
  return (
    <div style={{
      marginTop: 28,
      background: 'var(--aegis-allow-bg)',
      borderRadius: 20,
      padding: '28px 26px',
      textAlign: 'center',
      animation: 'fadeUp var(--dur-slow) var(--ease-out), pulseSage 1.2s var(--ease-out) 1',
    }}>
      <span className="pill pill-allow" style={{ marginBottom: 14 }}>
        <span className="dot"></span>Approved
      </span>
      <div style={{ fontFamily: 'var(--font-display)', fontSize: 30, lineHeight: 1.15, color: 'var(--fg-1)', marginBottom: 6 }}>
        Your parent said yes.
      </div>
      <p style={{ fontSize: 15, color: 'var(--fg-2)', margin: 0, marginBottom: 20, lineHeight: 1.5 }}>
        Reopen the page to try it again.
      </p>
      <button className="btn btn-approve btn-lg" onClick={onOpen}>
        Open the page →
      </button>
    </div>
  );
}

window.ApprovedState = ApprovedState;
