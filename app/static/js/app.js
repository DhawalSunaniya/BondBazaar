// BondBazaar Front-end Utilities
document.addEventListener("DOMContentLoaded", () => {
    // Theme toggle init
    const currentTheme = localStorage.getItem("bb-theme") || "dark";
    document.documentElement.setAttribute("data-theme", currentTheme);
    const themeBtn = document.getElementById("themeToggleBtn");
    if (themeBtn) {
        themeBtn.innerText = currentTheme === "dark" ? "☀️ Light" : "🌙 Dark";
        themeBtn.addEventListener("click", () => {
            const active = document.documentElement.getAttribute("data-theme");
            const next = active === "dark" ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", next);
            localStorage.setItem("bb-theme", next);
            themeBtn.innerText = next === "dark" ? "☀️ Light" : "🌙 Dark";
        });
    }

    // Modal listeners
    window.openModal = function(id) {
        const el = document.getElementById(id);
        if (el) el.classList.add("active");
    };
    window.closeModal = function(id) {
        const el = document.getElementById(id);
        if (el) el.classList.remove("active");
    };

    // Close on overlay click
    document.querySelectorAll(".modal-overlay").forEach(overlay => {
        overlay.addEventListener("click", (e) => {
            if (e.target === overlay) overlay.classList.remove("active");
        });
    });
});

// Helper Indian Number Formatter for JS
function formatINR(val) {
    if (isNaN(val)) return "₹0.00";
    const parts = Number(val).toFixed(2).split(".");
    let s = parts[0];
    const dec = parts[1];
    const isNeg = s.startsWith("-");
    if (isNeg) s = s.substring(1);

    if (s.length <= 3) {
        return (isNeg ? "-₹" : "₹") + s + "." + dec;
    }
    const last3 = s.substring(s.length - 3);
    let rest = s.substring(0, s.length - 3);
    const chunks = [];
    while (rest.length > 2) {
        chunks.push(rest.substring(rest.length - 2));
        rest = rest.substring(0, rest.length - 2);
    }
    if (rest.length > 0) chunks.push(rest);
    chunks.reverse();
    return (isNeg ? "-₹" : "₹") + chunks.join(",") + "," + last3 + "." + dec;
}
