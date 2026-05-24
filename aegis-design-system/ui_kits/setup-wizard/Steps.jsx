// ui_kits/setup-wizard/Steps.jsx
function StepWelcome({ onNext }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <img src="../../assets/aegis-shield.svg" width="64" height="72" alt="" style={{ marginBottom: 22 }} />
      <h1 style={{ fontFamily: 'var(--font-display)', fontStyle: 'italic', fontSize: 56, lineHeight: 1, margin: 0, letterSpacing: '-0.015em' }}>
        Welcome to Aegis.
      </h1>
      <p style={{ fontSize: 18, color: 'var(--fg-2)', lineHeight: 1.5, marginTop: 18, marginBottom: 32 }}>
        A shield, not a cage. Let's set up the PIN you'll use to review the things your kid asks to unblock.
      </p>
      <button className="btn btn-primary btn-lg btn-block" onClick={onNext}>Let's go →</button>
      <div style={{ fontSize: 13, color: 'var(--fg-3)', marginTop: 18 }}>
        Takes about 30 seconds.
      </div>
    </div>
  );
}

function StepChoosePin({ pin, setPin, length, setLength, onNext }) {
  const valid = pin.length >= 4;
  return (
    <div style={{ textAlign: 'center' }}>
      <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 38, lineHeight: 1.1, margin: 0 }}>
        Pick a PIN only you'll remember.
      </h2>
      <p style={{ fontSize: 16, color: 'var(--fg-2)', lineHeight: 1.5, marginTop: 14, marginBottom: 32 }}>
        4 to 8 digits. Your kid never sees it — we store it as a salted hash on this machine.
      </p>
      <PinCells value={pin} onChange={(v) => setPin(v.slice(0, length))} length={length} autoFocus />
      <div style={{ display: 'flex', gap: 12, justifyContent: 'center', alignItems: 'center', marginTop: 18, fontSize: 13, color: 'var(--fg-2)' }}>
        <span>Length:</span>
        {[4, 6, 8].map(n => (
          <button
            key={n}
            onClick={() => { setLength(n); setPin(pin.slice(0, n)); }}
            className="pill"
            style={{
              cursor: 'pointer', border: 0,
              background: length === n ? 'var(--aegis-terracotta-soft)' : 'var(--bg-2)',
              color: length === n ? 'var(--aegis-terracotta-3)' : 'var(--fg-1)',
              boxShadow: length === n ? 'none' : 'inset 0 0 0 1px var(--border-1)',
              padding: '6px 14px',
              fontFamily: 'var(--font-mono)',
              fontWeight: 600,
            }}
          >{n}</button>
        ))}
      </div>
      <button
        className="btn btn-primary btn-lg btn-block"
        onClick={onNext}
        disabled={!valid}
        style={{
          marginTop: 32,
          opacity: valid ? 1 : 0.4,
          cursor: valid ? 'pointer' : 'not-allowed',
        }}
      >Next →</button>
    </div>
  );
}

function StepConfirmPin({ pin, length, onConfirm, onBack }) {
  const [check, setCheck] = React.useState('');
  const [err, setErr] = React.useState(false);

  React.useEffect(() => {
    if (check.length === pin.length) {
      if (check === pin) {
        setTimeout(onConfirm, 200);
      } else {
        setErr(true);
        setTimeout(() => { setCheck(''); setErr(false); }, 600);
      }
    } else {
      setErr(false);
    }
  }, [check, pin]);

  return (
    <div style={{ textAlign: 'center' }}>
      <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 38, lineHeight: 1.1, margin: 0 }}>
        Once more, to be sure.
      </h2>
      <p style={{ fontSize: 16, color: 'var(--fg-2)', lineHeight: 1.5, marginTop: 14, marginBottom: 32 }}>
        Type it again. If you forget it later, you'll need to delete{' '}
        <code style={{ fontSize: 13 }}>data/guardian_admin.json</code> and start over.
      </p>
      <PinCells value={check} onChange={setCheck} length={length} autoFocus />
      <div style={{ fontSize: 13, marginTop: 20, color: err ? 'var(--aegis-block)' : 'var(--fg-3)', minHeight: 18 }}>
        {err ? "Those didn't match — try again." : '\u00A0'}
      </div>
      <button className="btn btn-ghost" onClick={onBack} style={{ marginTop: 14 }}>← Back</button>
    </div>
  );
}

function StepDone({ onContinue }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{
        width: 72, height: 72, borderRadius: 999,
        background: 'var(--aegis-sage-soft)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        margin: '0 auto 22px',
      }}>
        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--aegis-sage)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 6L9 17l-5-5"/>
        </svg>
      </div>
      <h2 style={{ fontFamily: 'var(--font-display)', fontStyle: 'italic', fontSize: 48, lineHeight: 1.05, margin: 0 }}>
        You're set.
      </h2>
      <p style={{ fontSize: 17, color: 'var(--fg-2)', lineHeight: 1.5, marginTop: 16, marginBottom: 32 }}>
        Aegis is watching out for requests. We'll show them to you whenever your kid asks to unblock something.
      </p>
      <button className="btn btn-primary btn-lg btn-block" onClick={onContinue}>Go to review →</button>
    </div>
  );
}

window.StepWelcome = StepWelcome;
window.StepChoosePin = StepChoosePin;
window.StepConfirmPin = StepConfirmPin;
window.StepDone = StepDone;
