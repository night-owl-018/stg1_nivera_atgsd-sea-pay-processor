const logbox = document.getElementById("logbox");

const ws = new WebSocket("ws://" + location.host + "/logs");

ws.onmessage = e => {
    logbox.textContent += e.data;
    logbox.scrollTop = logbox.scrollHeight;
};

function run() {
    fetch("/run", { method: "POST" });
}
