// ui_kits/parent-review/PinGate.jsx
function PinGate({ onUnlock }) {
  const [pin, setPin] = React.useState(['', '', '', '']);
  const [err, setErr] = React.useState(false);
  const refs = [React.useRef(), React.useRef(), React.useRef(), React.useRef()];

  React.useEffect(() => { refs[0].current?.focus(); }, []);

  function setDigit(i, v) {
    v = v.replace(/\D/g, '').slice(-1);
    const next = [...pin];
    next[i] = v;
    setPin(next);
    setErr(false);
    if (v && i < 3) refs[i + 1].current?.focus();
    if (next.every(d => d) && i === 3) {
      // mock: PIN is 7240
      if (next.join('') === '7240') {
        setTimeout(onUnlock, 180);
      } else {
        setErr(true);
        setTimeout(() => { setPin(['', '', '', '']); refs[0].current?.focus(); }, 500);
      }
    }
  }

  function handleKey(i, e) {
    if (e.key === 'Backspace' && !pin[i] && i > 0) refs[i - 1].current?.focus();
  }

  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'color-mix(in oklch, var(--aegis-espresso), transparent 35%)',
      backdropFilter: 'blur(20px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 50,
    }}>
      <div className="card" style={{
        width: 420, padding: 40, textAlign: 'center',
        boxShadow: 'var(--shadow-lifted)',
      }}>
        <img src="../../assets/aegis-shield.svg" width="48" height="54" alt="" style={{ marginBottom: 16 }} />
        <div style={{ fontFamily: 'var(--font-display)', fontSize: 30, lineHeight: 1.15, marginBottom: 6 }}>
          Welcome back.
        </div>
        <div style={{ fontSize: 15, color: 'var(--fg-2)', marginBottom: 24 }}>
          Enter your parent PIN to review requests.
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginBottom: 16 }}>
          {[0, 1, 2, 3].map(i => (
            <input
              key={i}
              ref={refs[i]}
              value={pin[i]}
              onChange={(e) => setDigit(i, e.target.value)}
              onKeyDown={(e) => handleKey(i, e)}
              type="password"
              inputMode="numeric"
              style={{
                width: 56, height: 64,
                textAlign: 'center',
                fontFamily: 'var(--font-display)',
                fontSize: 32,
                background: 'var(--bg-2)',
                border: `2px solid ${err ? 'var(--aegis-block)' : (pin[i] ? 'var(--aegis-terracotta)' : 'var(--border-1)')}`,
                borderRadius: 14,
                color: 'var(--fg-1)',
                outline: 'none',
                transition: 'all var(--dur-fast) var(--ease-out)',
              }}
            />
          ))}
        </div>
        <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: err ? 'var(--aegis-block)' : 'var(--fg-3)' }}>
          {err ? 'That PIN didn\'t match — try again.' : 'demo PIN: 7240'}
        </div>
      </div>
    </div>
  );
}

window.PinGate = PinGate;
