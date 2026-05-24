// ui_kits/parent-review/Brand.jsx
function Brand({ size = 'md' }) {
  const wordmarkSize = size === 'lg' ? 36 : size === 'sm' ? 22 : 28;
  const shieldSize = size === 'lg' ? 36 : size === 'sm' ? 22 : 28;
  return (
    <a className="brand" href="#">
      <img src="../../assets/aegis-shield.svg" alt="" width={shieldSize} height={shieldSize * 1.125} />
      <span className="wordmark" style={{ fontSize: wordmarkSize }}>Aegis</span>
    </a>
  );
}

window.Brand = Brand;
