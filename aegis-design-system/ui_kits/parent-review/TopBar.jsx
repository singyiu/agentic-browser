// ui_kits/parent-review/TopBar.jsx
function TopBar({ teens, activeTeen, onTeenChange, pendingByTeen }) {
  return (
    <header style={{
      position: 'sticky', top: 0, zIndex: 10,
      background: 'color-mix(in oklch, var(--bg-1), transparent 8%)',
      backdropFilter: 'blur(8px)',
      borderBottom: '1px solid var(--border-1)',
    }}>
      <div style={{
        maxWidth: 880, margin: '0 auto',
        padding: '20px 32px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16,
      }}>
        <Brand />
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {teens.map(t => {
            const count = pendingByTeen[t] || 0;
            const active = activeTeen === t;
            return (
              <button
                key={t}
                onClick={() => onTeenChange(t)}
                className="pill"
                style={{
                  cursor: 'pointer',
                  border: 0,
                  background: active ? 'var(--aegis-terracotta-soft)' : 'var(--bg-2)',
                  color: active ? 'var(--aegis-terracotta-3)' : 'var(--fg-1)',
                  boxShadow: active ? 'none' : 'inset 0 0 0 1px var(--border-1)',
                  padding: '7px 13px',
                  fontWeight: active ? 700 : 600,
                }}
              >
                {t === 'all' ? 'Everyone' : t}
                {count > 0 && (
                  <span style={{
                    marginLeft: 4,
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    color: active ? 'var(--aegis-terracotta-3)' : 'var(--fg-2)',
                  }}>{count}</span>
                )}
              </button>
            );
          })}
          <div style={{ width: 1, height: 22, background: 'var(--border-1)', margin: '0 4px' }} />
          <span className="pill pill-tag" style={{ background: 'var(--bg-1)' }}>
            <span style={{ width: 6, height: 6, background: 'var(--aegis-sage)', borderRadius: 999 }}></span>
            Signed in
          </span>
        </div>
      </div>
    </header>
  );
}

window.TopBar = TopBar;
