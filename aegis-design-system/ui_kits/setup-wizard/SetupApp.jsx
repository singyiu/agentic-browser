// ui_kits/setup-wizard/SetupApp.jsx
function SetupApp() {
  const [step, setStep] = React.useState(0);
  const [pin, setPin] = React.useState('');
  const [length, setLength] = React.useState(4);

  return (
    <div className="app" style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '48px 24px',
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Subtle shield watermark */}
      <img
        src="../../assets/aegis-shield.svg"
        alt=""
        aria-hidden
        style={{
          position: 'absolute',
          right: -120, bottom: -120,
          width: 520, height: 'auto',
          opacity: 0.04,
          pointerEvents: 'none',
        }}
      />

      <main style={{ width: '100%', maxWidth: 520, position: 'relative', zIndex: 1 }}>
        <StepIndicator step={step} total={4} />

        <div style={{
          background: 'var(--bg-2)',
          borderRadius: 24,
          padding: '48px 48px',
          boxShadow: 'var(--shadow-soft)',
          minHeight: 420,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
        }}>
          {step === 0 && <StepWelcome onNext={() => setStep(1)} />}
          {step === 1 && (
            <StepChoosePin
              pin={pin} setPin={setPin}
              length={length} setLength={setLength}
              onNext={() => setStep(2)}
            />
          )}
          {step === 2 && (
            <StepConfirmPin
              pin={pin}
              length={length}
              onConfirm={() => setStep(3)}
              onBack={() => setStep(1)}
            />
          )}
          {step === 3 && <StepDone onContinue={() => alert('In production: redirect to /review')} />}
        </div>

        <footer style={{
          marginTop: 24,
          textAlign: 'center',
          fontSize: 12,
          color: 'var(--fg-3)',
          fontFamily: 'var(--font-mono)',
        }}>
          {step === 0
            ? 'first-time setup · 127.0.0.1:2947/setup'
            : `step ${step} of 3`
          }
        </footer>
      </main>
    </div>
  );
}

window.SetupApp = SetupApp;
