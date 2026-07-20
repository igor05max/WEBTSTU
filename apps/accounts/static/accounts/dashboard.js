(() => {
    const body = document.body;
    const toggle = document.querySelector("[data-sidebar-toggle]");
    const overlay = document.querySelector("[data-sidebar-overlay]");
    const sidebar = document.querySelector("[data-sidebar]");

    const setMenuOpen = (isOpen) => {
        body.classList.toggle("dashboard-menu-open", isOpen);
        toggle?.setAttribute("aria-expanded", String(isOpen));
    };

    toggle?.addEventListener("click", () => {
        setMenuOpen(!body.classList.contains("dashboard-menu-open"));
    });

    overlay?.addEventListener("click", () => setMenuOpen(false));

    sidebar?.addEventListener("click", (event) => {
        if (event.target.closest("a") && window.matchMedia("(max-width: 900px)").matches) {
            setMenuOpen(false);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") setMenuOpen(false);
    });
})();
