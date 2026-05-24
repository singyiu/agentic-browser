// ui_kits/setup-wizard/StepIndicator.jsx
function StepIndicator({ step, total }) {
  return (
    <div style={{ display: 'flex', gap: 6, marginBottom: 40, justifyContent: 'center' }}>
      {Array.from({ length: total }).map((_, i) => (
        <div
          key={i}
          style={{
            height: 4,
            width: 40,
            borderRadius: 999,
            background: i <= step ? 'var(--aegis-terracotta)' : 'var(--border-1)',
            transition: 'background var(--dur-med) var(--ease-out)',
          }}
        />
      ))}
    </div>
  );
}

window.StepIndicator = StepIndicator;
