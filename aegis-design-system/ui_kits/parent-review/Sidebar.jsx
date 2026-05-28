// ui_kits/parent-review/Sidebar.jsx
function Sidebar({ active, onChange, teens, activeTeen, onTeenChange, pendingByTeen, onLock }) {
  const items = [
    { id: 'review',    label: 'Review',           icon: 'inbox',     count: pendingByTeen.all || 0 },
    { id: 'whitelist', label: 'Whitelist',        icon: 'list-check' },
    { id: 'history',   label: 'Recent decisions', icon: 'clock' },
    { id: 'settings',  label: 'Settings',         icon: 'sliders'    },
  ];

  return (
    <aside style={{
      width: 264,
      flexShrink: 0,
      borderRight: '1px solid var(--border-1)',
      background: 'var(--bg-1)',
      display: 'flex',
      flexDirection: 'column',
      position: 'sticky',
      top: 0,
      height: '100vh',
    }}>
      <div style={{ padding: '24px 22px 18px' }}>
        <Brand />
      </div>

      <nav style={{ padding: '4px 12px', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {items.map(it => (
          <button
            key={it.id}
            onClick={() => onChange(it.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '10px 12px',
              borderRadius: 10,
              border: 0,
              background: active === it.id ? 'var(--aegis-terracotta-soft)' : 'transparent',
              color: active === it.id ? 'var(--aegis-terracotta-3)' : 'var(--fg-1)',
              cursor: 'pointer',
              fontFamily: 'var(--font-sans)',
              fontSize: 15,
              fontWeight: active === it.id ? 600 : 500,
              textAlign: 'left',
              width: '100%',
              transition: 'background var(--dur-fast) var(--ease-out)',
            }}
          >
            <SidebarIcon name={it.icon} active={active === it.id} />
            <span style={{ flex: 1 }}>{it.label}</span>
            {it.count > 0 && (
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
                fontWeight: 600,
                padding: '2px 8px',
                borderRadius: 999,
                background: active === it.id ? 'var(--aegis-terracotta)' : 'var(--aegis-ochre)',
                color: 'var(--aegis-cream)',
                minWidth: 22,
                textAlign: 'center',
              }}>{it.count}</span>
            )}
          </button>
        ))}
      </nav>

      <div style={{ padding: '20px 22px 8px' }}>
        <div className="eyebrow" style={{ fontSize: 11, marginBottom: 12 }}>Teens</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {teens.map(t => {
            const count = pendingByTeen[t] || 0;
            const isActive = activeTeen === t;
            const label = t === 'all' ? 'Everyone' : t;
            return (
              <button
                key={t}
                onClick={() => onTeenChange(t)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '8px 10px',
                  borderRadius: 8,
                  border: 0,
                  background: isActive ? 'var(--bg-3)' : 'transparent',
                  color: 'var(--fg-1)',
                  cursor: 'pointer',
                  fontFamily: 'var(--font-sans)',
                  fontSize: 14,
                  fontWeight: isActive ? 600 : 500,
                  textAlign: 'left',
                  width: '100%',
                  textTransform: t === 'all' ? 'none' : 'capitalize',
                }}
              >
                <TeenAvatar name={t} />
                <span style={{ flex: 1 }}>{label}</span>
                {count > 0 && (
                  <span style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 11,
                    color: 'var(--fg-2)',
                  }}>{count}</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div style={{ flex: 1 }}></div>

      <div style={{
        padding: '16px 18px',
        borderTop: '1px solid var(--border-1)',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
      }}>
        <div style={{
          width: 32, height: 32, borderRadius: 999,
          background: 'var(--aegis-sage-soft)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
        }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--aegis-sage)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>
          </svg>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-1)', lineHeight: 1.2 }}>Signed in</div>
          <div style={{ fontSize: 11, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' }}>127.0.0.1:2947</div>
        </div>
        <button
          onClick={onLock}
          title="Lock"
          style={{
            background: 'transparent', border: 0, padding: 6, borderRadius: 8,
            color: 'var(--fg-2)', cursor: 'pointer',
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
        </button>
      </div>
    </aside>
  );
}

function SidebarIcon({ name, active }) {
  const stroke = active ? 'var(--aegis-terracotta-3)' : 'var(--fg-2)';
  const common = { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke, strokeWidth: 1.75, strokeLinecap: 'round', strokeLinejoin: 'round' };
  if (name === 'inbox') return (
    <svg {...common}><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>
  );
  if (name === 'list-check') return (
    <svg {...common}><path d="M11 6h10"/><path d="M11 12h10"/><path d="M11 18h10"/><path d="M3 6l2 2 3-3"/><path d="M3 12l2 2 3-3"/><path d="M3 18l2 2 3-3"/></svg>
  );
  if (name === 'clock') return (
    <svg {...common}><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>
  );
  if (name === 'sliders') return (
    <svg {...common}><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
  );
  return null;
}

function TeenAvatar({ name }) {
  // 'all' gets a stacked-circles glyph, named teens get a tinted initial
  if (name === 'all') {
    return (
      <div style={{
        width: 22, height: 22, borderRadius: 999,
        background: 'var(--bg-3)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        flexShrink: 0,
      }}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--fg-2)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
        </svg>
      </div>
    );
  }
  const palette = {
    alex: ['oklch(0.93 0.04 45)', 'var(--aegis-terracotta-3)'],
    sam:  ['oklch(0.93 0.03 145)', 'var(--aegis-sage)'],
  };
  const [bg, fg] = palette[name] || ['var(--bg-3)', 'var(--fg-1)'];
  return (
    <div style={{
      width: 22, height: 22, borderRadius: 999,
      background: bg, color: fg,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: 'var(--font-display)',
      fontSize: 13, fontWeight: 600,
      flexShrink: 0,
      textTransform: 'uppercase',
    }}>
      {name.charAt(0)}
    </div>
  );
}

window.Sidebar = Sidebar;
window.SidebarIcon = SidebarIcon;
window.TeenAvatar = TeenAvatar;
