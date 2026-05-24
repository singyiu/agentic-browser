// ui_kits/parent-review/ReviewApp.jsx
const SEED_REQUESTS = [
  {
    id: 'req_a',
    teen: 'alex',
    url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    reason: 'not on the whitelist yet',
    ago: '4 min ago',
    note: 'for my history project — found this in the search',
  },
  {
    id: 'req_b',
    teen: 'sam',
    url: 'https://beyblade.fandom.com/wiki/Valtryek_V2',
    reason: 'not on the whitelist yet',
    ago: '11 min ago',
    note: 'reading about the new layer system',
  },
  {
    id: 'req_c',
    teen: 'alex',
    url: 'https://www.reddit.com/r/woodworking/comments/abc123',
    reason: 'classifier: needs review (social platform)',
    ago: '32 min ago',
    note: null,
  },
];

const SEED_HISTORY = [
  { id: 'h1', teen: 'alex', decision: 'approve', whitelist_entry: 'khanacademy.org*', url: 'khanacademy.org/math', ago: 'yesterday' },
  { id: 'h2', teen: 'sam',  decision: 'approve', whitelist_entry: 'BeyBlade anime',  url: 'youtube.com/watch?v=…', ago: 'yesterday' },
  { id: 'h3', teen: 'alex', decision: 'reject',  whitelist_entry: null,              url: 'tiktok.com',           ago: '2d ago' },
];

function ReviewApp() {
  const [unlocked, setUnlocked] = React.useState(false);
  const [activeTeen, setActiveTeen] = React.useState('all');
  const [expandedId, setExpandedId] = React.useState(null);
  const [requests, setRequests] = React.useState(SEED_REQUESTS);
  const [history, setHistory] = React.useState(SEED_HISTORY);

  const teens = ['all', 'alex', 'sam'];
  const visible = activeTeen === 'all' ? requests : requests.filter(r => r.teen === activeTeen);
  const pendingByTeen = requests.reduce((acc, r) => {
    acc[r.teen] = (acc[r.teen] || 0) + 1;
    acc.all = (acc.all || 0) + 1;
    return acc;
  }, {});

  function decide(id, decision, payload) {
    const req = requests.find(r => r.id === id);
    if (!req) return;
    setRequests(rs => rs.filter(r => r.id !== id));
    setExpandedId(null);
    setHistory(h => [{
      id: 'h_' + id,
      teen: req.teen,
      decision,
      whitelist_entry: decision === 'approve' ? payload : null,
      url: req.url.replace(/^https?:\/\//, ''),
      ago: 'just now',
    }, ...h]);
  }

  const headline = visible.length === 0
    ? "You're all caught up."
    : visible.length === 1
      ? 'One page waiting on you.'
      : `${visible.length} pages waiting on you.`;

  return (
    <div className="app">
      <TopBar
        teens={teens}
        activeTeen={activeTeen}
        onTeenChange={setActiveTeen}
        pendingByTeen={pendingByTeen}
      />
      <main style={{ maxWidth: 880, margin: '0 auto', padding: '40px 32px 96px' }}>
        <div style={{ marginBottom: 32 }}>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 48, lineHeight: 1.05, margin: 0 }}>
            {headline}
          </h1>
          <p style={{ fontSize: 17, color: 'var(--fg-2)', marginTop: 10, marginBottom: 0 }}>
            Approve to add to {activeTeen === 'all' ? "the kid's" : `${activeTeen}'s`} whitelist. Reject to send a note back.
          </p>
        </div>

        <section style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {visible.length === 0
            ? <EmptyState />
            : visible.map(req => (
              <RequestRow
                key={req.id}
                req={req}
                expanded={expandedId === req.id}
                onExpand={setExpandedId}
                onApprove={(id, entry) => decide(id, 'approve', entry)}
                onReject={(id, note) => decide(id, 'reject', note)}
              />
            ))}
        </section>

        {history.length > 0 && (
          <section style={{ marginTop: 56 }}>
            <div className="eyebrow" style={{ marginBottom: 14 }}>Recent decisions</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {history.slice(0, 5).map(h => <HistoryItem key={h.id} item={h} />)}
            </div>
          </section>
        )}

        <footer style={{
          marginTop: 80,
          paddingTop: 24,
          borderTop: '1px solid var(--border-1)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          fontSize: 13, color: 'var(--fg-2)',
        }}>
          <span>Aegis guardian — running on this machine</span>
          <span style={{ fontFamily: 'var(--font-mono)' }}>127.0.0.1:2947</span>
        </footer>
      </main>

      {!unlocked && <PinGate onUnlock={() => setUnlocked(true)} />}
    </div>
  );
}

window.ReviewApp = ReviewApp;
