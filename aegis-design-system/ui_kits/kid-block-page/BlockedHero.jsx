// ui_kits/kid-block-page/BlockedHero.jsx
function BlockedHero({ url, reason }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <img src="../../assets/aegis-shield.svg" width="72" height="81" alt="" style={{ marginBottom: 22 }} />
      <h1 style={{
        fontFamily: 'var(--font-display)',
        fontSize: 56,
        lineHeight: 1.05,
        letterSpacing: '-0.015em',
        margin: 0,
      }}>
        This one needs your parent's okay.
      </h1>
      <p style={{
        fontSize: 18,
        color: 'var(--fg-2)',
        lineHeight: 1.5,
        marginTop: 16,
        marginBottom: 28,
        maxWidth: 440,
        marginLeft: 'auto',
        marginRight: 'auto',
      }}>
        We can ask them for you — add a note if it helps explain why.
      </p>
      <div className="card-hairline" style={{
        textAlign: 'left',
        padding: '14px 18px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        background: 'var(--bg-2)',
      }}>
        <div className="eyebrow" style={{ fontSize: 11 }}>Page</div>
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 14,
          color: 'var(--fg-1)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>{url}</div>
        <div style={{
          fontSize: 13,
          color: 'var(--fg-2)',
          marginTop: 6,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <span className="pill pill-block" style={{ padding: '3px 9px' }}><span className="dot"></span>{reason}</span>
        </div>
      </div>
    </div>
  );
}

window.BlockedHero = BlockedHero;
