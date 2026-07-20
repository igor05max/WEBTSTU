(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        var toggle = document.querySelector("[data-list-filter-toggle]");
        var panel = document.querySelector("[data-list-extra-filters]");
        var actionMenus = Array.prototype.slice.call(document.querySelectorAll(".submissions-row-actions"));

        function closeActionMenus(exceptMenu) {
            actionMenus.forEach(function (menu) {
                if (menu !== exceptMenu) {
                    menu.removeAttribute("open");
                }
            });
        }

        if (toggle && panel) {
            function setOpen(open) {
                toggle.setAttribute("aria-expanded", open ? "true" : "false");
                panel.hidden = !open;
            }

            toggle.addEventListener("click", function () {
                setOpen(toggle.getAttribute("aria-expanded") !== "true");
            });
        }

        actionMenus.forEach(function (menu) {
            var summary = menu.querySelector("summary");
            if (summary) {
                summary.addEventListener("click", function () {
                    closeActionMenus(menu);
                });
            }
        });

        document.addEventListener("click", function (event) {
            if (!event.target.closest(".submissions-row-actions")) {
                closeActionMenus(null);
            }
        });

        document.querySelectorAll("[data-delete-draft-form]").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                var title = form.getAttribute("data-submission-title") || "этот материал";
                if (!window.confirm("Удалить черновик «" + title + "»? Восстановить его будет нельзя.")) {
                    event.preventDefault();
                }
            });
        });

        document.addEventListener("keydown", function (event) {
            if (event.key !== "Escape") {
                return;
            }
            closeActionMenus(null);
            if (toggle && panel && toggle.getAttribute("aria-expanded") === "true") {
                toggle.setAttribute("aria-expanded", "false");
                panel.hidden = true;
                toggle.focus();
            }
        });
    });
})();
