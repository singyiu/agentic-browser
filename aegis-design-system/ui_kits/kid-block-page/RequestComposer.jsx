// ui_kits/kid-block-page/RequestComposer.jsx
function RequestComposer({ onCancel, onSubmit }) {
  const [note, setNote] = React.useState('');
  const ref = React.useRef();
  React.useEffect(() => { ref.current?.focus(); }, []);

  return (
    <div style={{ marginTop: 28, animation: 'fadeUp var(--dur-slow) var(--ease-out)' }}>
      <div className="field">
        <label>A quick note (optional)</label>
        <textarea
          ref={ref}
          className="textarea"
          placeholder="e.g. for my history project — found this in the search"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={240}
        />
        <div className="hint" style={{ textAlign: 'right' }}>{note.length} / 240</div>
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 16, justifyContent: 'flex-end' }}>
        <button className="btn btn-ghost" onClick={onCancel}>Never mind</button>
        <button className="btn btn-primary" onClick={() => onSubmit(note)}>Send request</button>
      </div>
    </div>
  );
}

window.RequestComposer = RequestComposer;
