// ui_kits/kid-block-page/BlockApp.jsx
function BlockApp() {
  const [state, setState] = React.useState('idle'); // idle | composing | pending | approved
  const [checking, setChecking] = React.useState(false);

  const url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
  const reason = 'not on the whitelist yet';

  function submit(note) {
    // pretend POST to /access-request
    setState('pending');
  }

  function check() {
    setChecking(true);
    setTimeout(() => {
      setChecking(false);
      setState('approved');
    }, 1200);
  }

  return (
    <div className="app" style={{
      minHeight: '100vh',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '64px 24px',
    }}>
      <main style={{ width: '100%', maxWidth: 560 }}>
        <BlockedHero url={url} reason={reason} />

        {state === 'idle' && (
          <div style={{ display: 'flex', gap: 10, marginTop: 28, justifyContent: 'center' }}>
            <button className="btn btn-secondary btn-lg" onClick={() => window.history.back()}>← Go back</button>
            <button className="btn btn-primary btn-lg" onClick={() => setState('composing')}>Ask my parent</button>
          </div>
        )}

        {state === 'composing' && (
          <RequestComposer
            onCancel={() => setState('idle')}
            onSubmit={submit}
          />
        )}

        {state === 'pending' && (
          <PendingState
            submittedAgo="just now"
            onCheck={check}
            checking={checking}
          />
        )}

        {state === 'approved' && (
          <ApprovedState onOpen={() => alert('In production: tab reloads, page now allowed.')} />
        )}

        <footer style={{
          marginTop: 56,
          textAlign: 'center',
          fontSize: 12,
          color: 'var(--fg-3)',
          display: 'flex',
          gap: 10,
          alignItems: 'center',
          justifyContent: 'center',
        }}>
          <img src="../../assets/aegis-shield.svg" width="14" height="16" alt="" style={{ opacity: 0.6 }} />
          Aegis — keeping the open web open, carefully.
        </footer>
      </main>

      <style>{`
        @keyframes fadeUp {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes pulseSage {
          0%   { box-shadow: 0 0 0 0 var(--aegis-sage-soft); }
          50%  { box-shadow: 0 0 0 14px transparent; }
          100% { box-shadow: 0 0 0 0 transparent; }
        }
      `}</style>
    </div>
  );
}

window.BlockApp = BlockApp;
