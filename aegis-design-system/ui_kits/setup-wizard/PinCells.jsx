// ui_kits/setup-wizard/PinCells.jsx
function PinCells({ value, onChange, length, autoFocus }) {
  const refs = React.useMemo(() => Array.from({ length: 8 }, () => React.createRef()), []);
  React.useEffect(() => { if (autoFocus) refs[0].current?.focus(); }, [autoFocus]);

  function setDigit(i, v) {
    v = v.replace(/\D/g, '').slice(-1);
    const arr = value.split('');
    while (arr.length <= i) arr.push('');
    arr[i] = v;
    onChange(arr.join(''));
    if (v && i < length - 1) refs[i + 1].current?.focus();
  }

  function handleKey(i, e) {
    if (e.key === 'Backspace' && !value[i] && i > 0) {
      refs[i - 1].current?.focus();
    }
  }

  return (
    <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
      {Array.from({ length }).map((_, i) => {
        const v = value[i] || '';
        const filled = !!v;
        return (
          <input
            key={i}
            ref={refs[i]}
            value={v}
            onChange={(e) => setDigit(i, e.target.value)}
            onKeyDown={(e) => handleKey(i, e)}
            type="password"
            inputMode="numeric"
            style={{
              width: 52,
              height: 60,
              textAlign: 'center',
              fontFamily: 'var(--font-display)',
              fontSize: 30,
              background: 'var(--bg-2)',
              border: `2px solid ${filled ? 'var(--aegis-terracotta)' : 'var(--border-1)'}`,
              borderRadius: 14,
              color: 'var(--fg-1)',
              outline: 'none',
              transition: 'all var(--dur-fast) var(--ease-out)',
            }}
          />
        );
      })}
    </div>
  );
}

window.PinCells = PinCells;
