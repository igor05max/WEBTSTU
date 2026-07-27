(function () {
    "use strict";

    if (window.SiteLoading) {
        return;
    }

    var nextToken = 1;
    var tasks = new Map();
    var root = null;
    var titleNode = null;
    var messageNode = null;
    var recentAction = null;

    function createRoot() {
        if (root || !document.body) {
            return;
        }
        root = document.createElement("div");
        root.className = "site-loading-root";
        root.hidden = true;
        root.setAttribute("data-site-loading-root", "");
        root.setAttribute("role", "status");
        root.setAttribute("aria-live", "polite");
        root.setAttribute("aria-atomic", "true");
        root.innerHTML = [
            '<div class="site-loading-progress" aria-hidden="true"></div>',
            '<div class="site-loading-panel">',
            '<span class="site-loading-spinner" aria-hidden="true"></span>',
            '<div class="site-loading-copy">',
            '<strong class="site-loading-title">Загружаем данные</strong>',
            '<p class="site-loading-message">Пожалуйста, подождите — результат появится на этой странице.</p>',
            "</div>",
            "</div>",
        ].join("");
        document.body.appendChild(root);
        titleNode = root.querySelector(".site-loading-title");
        messageNode = root.querySelector(".site-loading-message");
    }

    function actionText(element) {
        if (!element) {
            return "";
        }
        return String(
            element.getAttribute("aria-label")
            || element.value
            || element.textContent
            || ""
        ).replace(/\s+/g, " ").trim().slice(0, 90);
    }

    function markAction(element, active) {
        if (!element) {
            return;
        }
        var count = Number(element.dataset.siteLoadingCount || 0);
        count = Math.max(0, count + (active ? 1 : -1));
        element.dataset.siteLoadingCount = String(count);
        element.classList.toggle("is-site-loading", count > 0);
        if (count > 0) {
            element.setAttribute("aria-busy", "true");
        } else {
            element.removeAttribute("aria-busy");
            delete element.dataset.siteLoadingCount;
        }
    }

    function visibleTasks() {
        return Array.from(tasks.values()).filter(function (task) {
            return task.visible;
        });
    }

    function render() {
        createRoot();
        if (!root) {
            return;
        }
        var active = visibleTasks();
        if (!active.length) {
            root.classList.remove("is-visible");
            window.setTimeout(function () {
                if (!visibleTasks().length) {
                    root.hidden = true;
                }
            }, 180);
            document.body.removeAttribute("aria-busy");
            return;
        }
        var navigationTask = active.slice().reverse().find(function (task) {
            return task.mode === "navigation";
        });
        var current = navigationTask || active[active.length - 1];
        root.hidden = false;
        root.dataset.mode = navigationTask ? "navigation" : "background";
        titleNode.textContent = current.title;
        messageNode.textContent = current.message;
        window.requestAnimationFrame(function () {
            root.classList.add("is-visible");
        });
    }

    function start(options) {
        options = options || {};
        var token = nextToken++;
        var mode = options.mode === "navigation" ? "navigation" : "background";
        var task = {
            token: token,
            mode: mode,
            title: options.title || (
                mode === "navigation" ? "Загружаем страницу" : "Получаем данные"
            ),
            message: options.message || (
                mode === "navigation"
                    ? "Новая страница откроется сразу после подготовки данных."
                    : "Результат постепенно появится на текущей странице."
            ),
            element: options.element || null,
            visible: false,
            timer: null,
        };
        tasks.set(token, task);
        markAction(task.element, true);
        task.timer = window.setTimeout(function () {
            if (!tasks.has(token)) {
                return;
            }
            task.visible = true;
            render();
        }, mode === "navigation" ? 420 : 280);
        return token;
    }

    function stop(token) {
        var task = tasks.get(token);
        if (!task) {
            return;
        }
        window.clearTimeout(task.timer);
        tasks.delete(token);
        markAction(task.element, false);
        render();
    }

    function reset() {
        tasks.forEach(function (task) {
            window.clearTimeout(task.timer);
            markAction(task.element, false);
        });
        tasks.clear();
        if (root) {
            root.classList.remove("is-visible");
            root.hidden = true;
        }
        if (document.body) {
            document.body.removeAttribute("aria-busy");
        }
    }

    function requestIsSilent(input, init) {
        try {
            var headers = new Headers(
                (init && init.headers)
                || (input instanceof Request ? input.headers : undefined)
            );
            return headers.get("X-Site-Loading") === "silent";
        } catch (_error) {
            return false;
        }
    }

    function recentButton() {
        if (!recentAction || Date.now() - recentAction.time > 1500) {
            return null;
        }
        return recentAction.element;
    }

    function backgroundMessage(element) {
        var label = actionText(element);
        return label
            ? "Выполняем действие «" + label + "». Результат появится здесь."
            : "Сервер обрабатывает запрос. Результат появится на текущей странице.";
    }

    function isDownloadAction(element, targetUrl) {
        if (element && element.hasAttribute && element.hasAttribute("download")) {
            return true;
        }
        var label = actionText(element);
        var url = String(targetUrl || "");
        return (
            /скач|download/i.test(label)
            || /(?:\/download(?:\/|$)|\.(?:docx?|pdf|tex|zip|rar|7z|xlsx?|csv)(?:$|[?#]))/i.test(url)
        );
    }

    var originalFetch = window.fetch;
    if (typeof originalFetch === "function") {
        window.fetch = function (input, init) {
            if (requestIsSilent(input, init)) {
                return originalFetch.apply(this, arguments);
            }
            var element = recentButton();
            var token = start({
                mode: "background",
                title: "Обрабатываем запрос",
                message: backgroundMessage(element),
                element: element,
            });
            try {
                return originalFetch.apply(this, arguments).finally(function () {
                    stop(token);
                });
            } catch (error) {
                stop(token);
                throw error;
            }
        };
    }

    if (window.XMLHttpRequest) {
        var originalOpen = XMLHttpRequest.prototype.open;
        var originalSend = XMLHttpRequest.prototype.send;
        var originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
        XMLHttpRequest.prototype.open = function () {
            this.__siteLoadingSilent = false;
            return originalOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
            if (
                String(name).toLowerCase() === "x-site-loading"
                && String(value).toLowerCase() === "silent"
            ) {
                this.__siteLoadingSilent = true;
            }
            return originalSetRequestHeader.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function () {
            if (this.__siteLoadingSilent) {
                return originalSend.apply(this, arguments);
            }
            var element = recentButton();
            var token = start({
                mode: "background",
                title: "Обрабатываем запрос",
                message: backgroundMessage(element),
                element: element,
            });
            this.addEventListener("loadend", function () {
                stop(token);
            }, {once: true});
            return originalSend.apply(this, arguments);
        };
    }

    function isNavigatingLink(link, event) {
        if (
            !link
            || event.defaultPrevented
            || event.button !== 0
            || event.metaKey
            || event.ctrlKey
            || event.shiftKey
            || event.altKey
            || link.dataset.noSiteLoading !== undefined
            || (link.target && link.target !== "_self")
        ) {
            return false;
        }
        var href = link.getAttribute("href") || "";
        if (
            !href
            || href[0] === "#"
            || /^(?:mailto|tel|javascript):/i.test(href)
            || isDownloadAction(link, href)
        ) {
            return false;
        }
        try {
            var target = new URL(link.href, window.location.href);
            if (target.origin !== window.location.origin) {
                return false;
            }
            return !(
                target.pathname === window.location.pathname
                && target.search === window.location.search
                && target.hash
            );
        } catch (_error) {
            return false;
        }
    }

    document.addEventListener("click", function (event) {
        var action = event.target.closest(
            "button, input[type='submit'], input[type='button'], [role='button']"
        );
        if (action) {
            recentAction = {element: action, time: Date.now()};
        }
        var link = event.target.closest("a[href]");
        if (!isNavigatingLink(link, event)) {
            return;
        }
        window.setTimeout(function () {
            if (event.defaultPrevented) {
                return;
            }
            var label = actionText(link);
            start({
                mode: "navigation",
                title: "Загружаем страницу",
                message: label
                    ? "Переходим к разделу «" + label + "»."
                    : "Подготавливаем содержимое новой страницы.",
                element: link,
            });
        }, 0);
    }, true);

    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (
            !form
            || form.dataset.noSiteLoading !== undefined
            || String(form.getAttribute("method") || "get").toLowerCase() === "dialog"
        ) {
            return;
        }
        var submitter = event.submitter || recentButton();
        if (isDownloadAction(submitter, form.getAttribute("action") || "")) {
            return;
        }
        window.setTimeout(function () {
            if (event.defaultPrevented || form.dataset.siteNavigationStarted === "true") {
                return;
            }
            form.dataset.siteNavigationStarted = "true";
            var label = actionText(submitter);
            start({
                mode: "navigation",
                title: "Отправляем данные",
                message: label
                    ? "Выполняем действие «" + label + "»."
                    : "Сохраняем данные и подготавливаем результат.",
                element: submitter,
            });
        }, 0);
    }, true);

    window.addEventListener("beforeunload", function () {
        if (!visibleTasks().some(function (task) {
            return task.mode === "navigation";
        })) {
            start({
                mode: "navigation",
                title: "Загружаем страницу",
                message: "Подготавливаем содержимое новой страницы.",
            });
        }
    });
    window.addEventListener("pageshow", reset);

    window.SiteLoading = {
        start: start,
        stop: stop,
        reset: reset,
    };
}());
