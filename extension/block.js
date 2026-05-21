// Populate the block page from query params (MV3 extension pages disallow inline JS).
const params = new URLSearchParams(location.search);
const reason = params.get("reason");
const url = params.get("url");
if (reason) document.getElementById("reason").textContent = reason;
if (url) document.getElementById("url").textContent = url;
document.getElementById("back").addEventListener("click", () => history.back());
