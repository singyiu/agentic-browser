// ui_kits/parent-review/Dropdown.jsx
//
// Tall, generous dropdown — its trigger height is doubled (~96px) by design,
// so the selector reads as a card rather than a thin row. Override with
// the --dropdown-h CSS custom property if you need a shorter variant.

function Dropdown({ label, value, options, onChange }) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);

  React.useEffect(() => {
    function handler(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const current = options.find(o => o.value === value) || options[0];

  return (
    <div className="dropdown" ref={ref}>
      <button
        type="button"
        className="dropdown-trigger"
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}
      >
        <span style={{ minWidth: 0 }}>
          <span className="trigger-label">{label}</span>
          <span className="trigger-value">{current?.label}</span>
        </span>
        <svg className="dropdown-caret" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 9l6 6 6-6"/>
        </svg>
      </button>

      {open && (
        <div className="dropdown-menu" role="listbox">
          {options.map(o => (
            <button
              key={o.value}
              type="button"
              className="dropdown-item"
              aria-selected={o.value === value}
              onClick={() => { onChange(o.value); setOpen(false); }}
            >
              <span>{o.label}</span>
              {o.meta && <span className="item-meta">{o.meta}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

window.Dropdown = Dropdown;
