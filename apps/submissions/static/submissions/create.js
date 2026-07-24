(function () {
    "use strict";

    function initializeDestinationAndTemplate() {
        var articleType = document.getElementById("id_article_type");
        var journalInput = document.getElementById("id_journal_query");
        var journalHidden = document.getElementById("id_journal");
        var topicInput = document.getElementById("id_publication_topic_query");
        var topicHidden = document.getElementById("id_publication_topic");
        var templateHidden = document.getElementById("id_formatting_template");
        var templateFile = document.getElementById("id_formatting_template_file");
        var journalField = document.querySelector("[data-destination-field='journal']");
        var topicField = document.querySelector("[data-destination-field='topic']");
        var templateEmpty = document.querySelector("[data-template-empty]");
        var templateEmptyText = document.querySelector("[data-template-empty-text]");
        var templateSelected = document.querySelector("[data-template-selected]");
        var templateName = document.querySelector("[data-template-name]");
        var templateMeta = document.querySelector("[data-template-meta]");
        var templateDownload = document.querySelector("[data-template-download]");
        var templateLatexDownload = document.querySelector("[data-template-latex-download]");
        var rulesPanel = document.querySelector("[data-template-rules]");
        var rulesList = document.querySelector("[data-template-rules-list]");
        if (!articleType || !journalInput || !journalHidden || !topicInput || !topicHidden || !templateHidden) {
            return;
        }

        function selectedTypeCode() {
            var selected = articleType.options[articleType.selectedIndex];
            return selected ? selected.getAttribute("data-code") || "" : "";
        }

        function selectedDestinationKind() {
            var selected = articleType.options[articleType.selectedIndex];
            return selected ? selected.getAttribute("data-destination-kind") || "" : "";
        }

        function readableValue(value, unit) {
            if (typeof value === "boolean") {
                return value ? "Да" : "Нет";
            }
            var aliases = {
                portrait: "Книжная",
                landscape: "Альбомная",
                justify: "По ширине",
                left: "По левому краю",
                center: "По центру",
                right: "По правому краю"
            };
            var rendered = aliases[String(value).toLowerCase()] || String(value);
            return unit ? rendered + " " + unit : rendered;
        }

        function ruleRows(rules) {
            var rows = [];
            var page = rules.page || {};
            var margins = page.margins_cm || {};
            var body = rules.body || {};
            var structure = rules.structure || {};
            var documentRules = rules.document || {};
            var descriptors = [
                ["Формат страницы", page.size],
                ["Ориентация", page.orientation],
                ["Верхнее поле", margins.top, "см"],
                ["Правое поле", margins.right, "см"],
                ["Нижнее поле", margins.bottom, "см"],
                ["Левое поле", margins.left, "см"],
                ["Основной шрифт", body.font_family],
                ["Размер шрифта", body.font_size_pt, "пт"],
                ["Межстрочный интервал", body.line_spacing],
                ["Абзацный отступ", body.first_line_indent_cm, "см"],
                ["Выравнивание", body.alignment],
                ["Минимальный объём", structure.min_words, "слов"],
                ["Максимальный объём", structure.max_words, "слов"]
            ];
            descriptors.forEach(function (descriptor) {
                var value = descriptor[1];
                if (value !== null && value !== undefined && value !== "") {
                    rows.push({
                        name: descriptor[0],
                        value: readableValue(value, descriptor[2])
                    });
                }
            });

            var blocks = Array.isArray(documentRules.blocks) ? documentRules.blocks : [];
            if (blocks.length) {
                var required = [];
                var optional = [];
                blocks.forEach(function (block) {
                    if (!block || typeof block !== "object") return;
                    var label = block.label || block.source_label || block.role;
                    if (!label) return;
                    (block.required ? required : optional).push(label);
                });
                if (required.length) {
                    rows.push({name: "Обязательные элементы", value: required.join(", ")});
                }
                if (optional.length) {
                    rows.push({name: "Необязательные элементы", value: optional.join(", ")});
                }
            }
            return rows;
        }

        function renderTemplate(template) {
            if (!template) {
                templateHidden.value = "";
                templateEmpty.hidden = false;
                templateSelected.hidden = true;
                if (templateLatexDownload) {
                    templateLatexDownload.hidden = true;
                    templateLatexDownload.removeAttribute("href");
                }
                rulesPanel.hidden = true;
                rulesList.replaceChildren();
                if (selectedDestinationKind() === "journal") {
                    templateEmptyText.textContent = "Для статьи без сохранённого шаблона нужно загрузить новый файл.";
                } else {
                    templateEmptyText.textContent = "Шаблона ещё нет. Можно продолжить без него или загрузить первый.";
                }
                return;
            }

            templateHidden.value = template.id || "";
            templateEmpty.hidden = true;
            templateSelected.hidden = false;
            templateName.textContent = template.file_name || "Шаблон оформления";
            templateMeta.textContent = "Версия " + (template.version || "—") + " · " +
                (template.status_label || template.status || "") +
                (template.uploaded_by ? " · загрузил " + template.uploaded_by : "");
            templateDownload.href = template.download_url || "#";
            if (templateLatexDownload) {
                templateLatexDownload.href = template.latex_preview_url || "#";
                templateLatexDownload.hidden = !template.latex_preview_url;
            }
            rulesList.replaceChildren();
            var rows = ruleRows(template.rules || {});
            rows.slice(0, 16).forEach(function (row) {
                var item = document.createElement("div");
                var name = document.createElement("span");
                name.textContent = row.name;
                var value = document.createElement("strong");
                value.textContent = row.value;
                item.appendChild(name);
                item.appendChild(value);
                rulesList.appendChild(item);
            });
            rulesPanel.hidden = !rows.length;
        }

        function fetchTemplateById() {
            if (!templateHidden.value) {
                return;
            }
            var pattern = templateHidden.getAttribute("data-template-detail-url");
            if (!pattern) {
                return;
            }
            fetch(pattern.replace("{id}", encodeURIComponent(templateHidden.value)), {
                headers: {"X-Requested-With": "XMLHttpRequest"}
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("template-load-failed");
                    }
                    return response.json();
                })
                .then(function (payload) {
                    renderTemplate(payload.template || null);
                })
                .catch(function () {
                    renderTemplate(null);
                });
        }

        function initializeSearch(input, hidden, url, emptyLabel) {
            var list = document.createElement("div");
            list.className = "journal-suggest";
            list.hidden = true;
            input.insertAdjacentElement("afterend", list);
            var requestNumber = 0;
            var debounceTimer = null;

            function hideResults() {
                list.hidden = true;
                list.replaceChildren();
            }

            function renderResults(results) {
                list.replaceChildren();
                if (!results.length) {
                    var empty = document.createElement("div");
                    empty.className = "journal-suggest-empty";
                    empty.textContent = emptyLabel;
                    list.appendChild(empty);
                    list.hidden = false;
                    return;
                }
                results.forEach(function (item) {
                    var option = document.createElement("button");
                    option.type = "button";
                    option.className = "journal-suggest-option";
                    option.dataset.id = item.id || "";
                    option.dataset.label = item.label || item.name || "";
                    option._templatePayload = item.template || null;

                    var name = document.createElement("strong");
                    name.textContent = item.name || item.label || "";
                    option.appendChild(name);
                    var meta = document.createElement("small");
                    if (item.issn) {
                        var issn = document.createElement("span");
                        issn.textContent = "ISSN " + item.issn;
                        meta.appendChild(issn);
                    }
                    if (item.level) {
                        var level = document.createElement("span");
                        level.textContent = "Уровень " + item.level;
                        meta.appendChild(level);
                    }
                    var state = document.createElement("span");
                    state.textContent = item.template ? "Шаблон v" + item.template.version : "Шаблона нет";
                    meta.appendChild(state);
                    option.appendChild(meta);
                    list.appendChild(option);
                });
                list.hidden = false;
            }

            function runSearch() {
                var query = input.value.trim();
                hidden.value = "";
                renderTemplate(null);
                if (query.length < 2 || !articleType.value) {
                    hideResults();
                    return;
                }
                requestNumber += 1;
                var currentRequest = requestNumber;
                fetch(url + "?q=" + encodeURIComponent(query) + "&article_type=" + encodeURIComponent(articleType.value), {
                    headers: {"X-Requested-With": "XMLHttpRequest"}
                })
                    .then(function (response) {
                        if (!response.ok) {
                            throw new Error("destination-search-failed");
                        }
                        return response.json();
                    })
                    .then(function (payload) {
                        if (currentRequest === requestNumber) {
                            renderResults(payload.results || []);
                        }
                    })
                    .catch(function () {
                        if (currentRequest === requestNumber) {
                            hideResults();
                        }
                    });
            }

            input.addEventListener("input", function () {
                window.clearTimeout(debounceTimer);
                debounceTimer = window.setTimeout(runSearch, 220);
            });
            input.addEventListener("blur", function () {
                window.setTimeout(hideResults, 180);
            });
            list.addEventListener("pointerdown", function (event) {
                var option = event.target.closest(".journal-suggest-option");
                if (!option) {
                    return;
                }
                event.preventDefault();
                hidden.value = option.dataset.id || "";
                input.value = option.dataset.label || "";
                renderTemplate(option._templatePayload || null);
                hideResults();
            });
        }

        function updateDestinationVisibility(resetValues) {
            var code = selectedTypeCode();
            var destinationKind = selectedDestinationKind();
            journalField.hidden = destinationKind !== "journal";
            topicField.hidden = !code || destinationKind !== "topic";
            if (resetValues) {
                journalHidden.value = "";
                topicHidden.value = "";
                templateHidden.value = "";
                if (destinationKind === "journal") {
                    topicInput.value = "";
                } else {
                    journalInput.value = "";
                }
                renderTemplate(null);
            }
        }

        initializeSearch(
            journalInput,
            journalHidden,
            journalInput.getAttribute("data-journal-search-url"),
            "Журнал не найден"
        );
        initializeSearch(
            topicInput,
            topicHidden,
            topicInput.getAttribute("data-topic-search-url"),
            "Совпадений нет — тема или событие будет создано"
        );

        articleType.addEventListener("change", function () {
            updateDestinationVisibility(true);
        });
        if (templateFile) {
            templateFile.addEventListener("change", function () {
                var file = templateFile.files && templateFile.files[0];
                if (!file) {
                    if (templateHidden.value) {
                        fetchTemplateById();
                    }
                    return;
                }
                templateEmpty.hidden = true;
                templateSelected.hidden = false;
                templateName.textContent = file.name;
                templateMeta.textContent = templateHidden.value
                    ? "Новый шаблон заменит предложенный и станет последней версией."
                    : "Новый шаблон будет сохранён для следующих пользователей.";
                templateDownload.removeAttribute("href");
                if (templateLatexDownload) {
                    templateLatexDownload.hidden = true;
                    templateLatexDownload.removeAttribute("href");
                }
                rulesPanel.hidden = true;
            });
        }

        updateDestinationVisibility(false);
        if (templateHidden.value) {
            fetchTemplateById();
        } else {
            renderTemplate(null);
        }
    }

    function initializeFileZone() {
        var zone = document.querySelector("[data-file-zone]");
        if (!zone) {
            return;
        }

        var input = zone.querySelector("input[type='file']");
        var title = zone.querySelector("[data-file-title]");
        var meta = zone.querySelector("[data-file-meta]");
        if (!input || !title || !meta) {
            return;
        }

        function formatSize(bytes) {
            if (!bytes) {
                return "Размер файла не определён";
            }
            if (bytes < 1024 * 1024) {
                return Math.max(1, Math.round(bytes / 1024)) + " КБ";
            }
            return (bytes / (1024 * 1024)).toFixed(1).replace(".0", "") + " МБ";
        }

        function displayFile() {
            var file = input.files && input.files[0];
            if (!file) {
                title.textContent = "Перетащите файл сюда";
                meta.textContent = "или выберите его на компьютере";
                return;
            }
            title.textContent = file.name;
            meta.textContent = formatSize(file.size);
        }

        input.addEventListener("change", displayFile);

        ["dragenter", "dragover"].forEach(function (eventName) {
            zone.addEventListener(eventName, function (event) {
                event.preventDefault();
                zone.classList.add("is-dragging");
            });
        });

        ["dragleave", "drop"].forEach(function (eventName) {
            zone.addEventListener(eventName, function (event) {
                event.preventDefault();
                zone.classList.remove("is-dragging");
            });
        });

        zone.addEventListener("drop", function (event) {
            if (!event.dataTransfer || !event.dataTransfer.files.length) {
                return;
            }
            try {
                input.files = event.dataTransfer.files;
                displayFile();
                input.dispatchEvent(new Event("change", {bubbles: true}));
            } catch (error) {
                input.click();
            }
        });

        displayFile();
    }

    function initializeWizard() {
        var form = document.querySelector("[data-submission-wizard]");
        if (!form) {
            return;
        }
        var steps = Array.prototype.slice.call(form.querySelectorAll("[data-wizard-step]"));
        var progressButtons = Array.prototype.slice.call(form.querySelectorAll("[data-wizard-go]"));
        var articleType = document.getElementById("id_article_type");
        var fileInput = document.getElementById("id_file");
        var journalInput = document.getElementById("id_journal_query");
        var topicInput = document.getElementById("id_publication_topic_query");
        var templateInput = document.getElementById("id_formatting_template");
        var templateFile = document.getElementById("id_formatting_template_file");
        var currentStep = 1;
        var maxVisitedStep = 1;

        function stepElement(number) {
            return steps.find(function (step) {
                return Number(step.getAttribute("data-wizard-step")) === number;
            });
        }

        function destinationKind() {
            if (!articleType || articleType.selectedIndex < 0) {
                return "";
            }
            return articleType.options[articleType.selectedIndex].getAttribute("data-destination-kind") || "";
        }

        function clearStepError(step) {
            var error = step.querySelector("[data-wizard-error]");
            if (error) {
                error.remove();
            }
        }

        function showStepError(step, message, field) {
            clearStepError(step);
            var error = document.createElement("div");
            error.className = "submission-wizard-error";
            error.setAttribute("data-wizard-error", "");
            error.setAttribute("role", "alert");
            error.textContent = message;
            var actions = step.querySelector(".submission-wizard-actions");
            step.insertBefore(error, actions || null);
            if (field && typeof field.focus === "function") {
                field.focus({preventScroll: true});
            }
        }

        function validateStep(number) {
            var step = stepElement(number);
            if (!step) {
                return true;
            }
            clearStepError(step);

            if (number === 1 && (!articleType || !articleType.value)) {
                showStepError(step, "Выберите тип материала.", articleType);
                return false;
            }
            if (number === 2 && (!fileInput || !fileInput.files || !fileInput.files.length)) {
                showStepError(step, "Сначала выберите файл материала.", fileInput);
                return false;
            }
            if (number === 3) {
                var targetInput = destinationKind() === "journal" ? journalInput : topicInput;
                if (!targetInput || !targetInput.value.trim()) {
                    showStepError(
                        step,
                        destinationKind() === "journal"
                            ? "Укажите журнал и выберите его из подсказок."
                            : "Укажите тему или название события.",
                        targetInput
                    );
                    return false;
                }
            }
            if (
                number === 4 &&
                destinationKind() === "journal" &&
                (!templateInput || !templateInput.value) &&
                (!templateFile || !templateFile.files || !templateFile.files.length)
            ) {
                showStepError(
                    step,
                    "Для журнала без сохранённого шаблона загрузите файл шаблона.",
                    templateFile
                );
                return false;
            }

            var invalidField = Array.prototype.find.call(
                step.querySelectorAll("input, select, textarea"),
                function (field) {
                    return !field.disabled && !field.checkValidity();
                }
            );
            if (invalidField) {
                invalidField.reportValidity();
                return false;
            }
            return true;
        }

        function showStep(number, options) {
            var target = stepElement(number);
            if (!target) {
                return;
            }
            currentStep = number;
            maxVisitedStep = Math.max(maxVisitedStep, number);
            steps.forEach(function (step) {
                step.hidden = Number(step.getAttribute("data-wizard-step")) !== number;
            });
            progressButtons.forEach(function (button) {
                var buttonStep = Number(button.getAttribute("data-wizard-go"));
                button.classList.toggle("is-complete", buttonStep < number);
                button.classList.toggle("is-active", buttonStep === number);
                button.disabled = buttonStep > maxVisitedStep;
                if (buttonStep === number) {
                    button.setAttribute("aria-current", "step");
                } else {
                    button.removeAttribute("aria-current");
                }
            });
            if (!options || options.focus !== false) {
                target.scrollIntoView({behavior: "smooth", block: "start"});
            }
        }

        form.addEventListener("click", function (event) {
            var next = event.target.closest("[data-wizard-next]");
            var back = event.target.closest("[data-wizard-back]");
            var progress = event.target.closest("[data-wizard-go]");
            if (next) {
                if (validateStep(currentStep)) {
                    showStep(Math.min(steps.length, currentStep + 1));
                }
                return;
            }
            if (back) {
                showStep(Math.max(1, currentStep - 1));
                return;
            }
            if (progress && !progress.disabled) {
                var requestedStep = Number(progress.getAttribute("data-wizard-go"));
                if (requestedStep <= maxVisitedStep) {
                    showStep(requestedStep);
                }
            }
        });

        form.addEventListener("submit", function (event) {
            for (var number = 1; number <= steps.length; number += 1) {
                if (!validateStep(number)) {
                    event.preventDefault();
                    showStep(number);
                    return;
                }
            }
            var submitButton = form.querySelector("[data-wizard-submit]");
            var submitLabel = form.querySelector("[data-wizard-submit-label]");
            if (submitButton) {
                submitButton.disabled = true;
            }
            if (submitLabel) {
                submitLabel.textContent = "Открываем материал…";
            }
        });

        var errorStep = steps.find(function (step) {
            return step.querySelector(".has-error, .submission-form-alert");
        });
        var initialStep = errorStep ? Number(errorStep.getAttribute("data-wizard-step")) : 1;
        var optionalDetails = form.querySelector("[data-optional-details]");
        if (optionalDetails && optionalDetails.querySelector(".has-error")) {
            optionalDetails.open = true;
        }
        maxVisitedStep = initialStep;
        showStep(initialStep, {focus: false});
        form.classList.add("is-wizard-ready");
    }

    function initializeCoauthors() {
        var picker = document.querySelector("[data-coauthor-picker]");
        if (!picker) {
            return;
        }

        var select = picker.querySelector("select[multiple]");
        var search = picker.querySelector("[data-coauthor-search]");
        var selectedArea = picker.querySelector("[data-coauthor-selected]");
        var optionsArea = picker.querySelector("[data-coauthor-options]");
        if (!select || !search || !selectedArea || !optionsArea) {
            return;
        }

        var options = Array.prototype.slice.call(select.options);

        function optionSearchText(option) {
            return [option.text, option.dataset.username, option.dataset.unit]
                .join(" ")
                .toLocaleLowerCase("ru-RU");
        }

        function toggleOption(option, selected) {
            option.selected = selected;
            select.dispatchEvent(new Event("change", {bubbles: true}));
            render();
        }

        function createChip(option) {
            var chip = document.createElement("span");
            chip.className = "coauthor-chip";
            chip.appendChild(document.createTextNode(option.text));

            var remove = document.createElement("button");
            remove.type = "button";
            remove.setAttribute("aria-label", "Убрать соавтора " + option.text);
            remove.textContent = "×";
            remove.addEventListener("click", function () {
                toggleOption(option, false);
            });
            chip.appendChild(remove);
            return chip;
        }

        function createOptionButton(option) {
            var button = document.createElement("button");
            button.type = "button";
            button.className = "coauthor-option" + (option.selected ? " is-selected" : "");
            button.setAttribute("aria-pressed", option.selected ? "true" : "false");

            var mark = document.createElement("span");
            mark.className = "coauthor-option-mark";
            mark.textContent = "✓";
            button.appendChild(mark);

            var name = document.createElement("span");
            name.className = "coauthor-option-name";
            name.textContent = option.text;
            button.appendChild(name);

            button.addEventListener("click", function () {
                toggleOption(option, !option.selected);
            });
            return button;
        }

        function render() {
            var query = search.value.trim().toLocaleLowerCase("ru-RU");
            var visibleOptions = options.filter(function (option) {
                return !query || optionSearchText(option).indexOf(query) !== -1;
            });

            selectedArea.replaceChildren();
            options.filter(function (option) {
                return option.selected;
            }).forEach(function (option) {
                selectedArea.appendChild(createChip(option));
            });

            optionsArea.replaceChildren();
            if (!visibleOptions.length) {
                var empty = document.createElement("div");
                empty.className = "coauthor-empty";
                empty.textContent = "Пользователи не найдены";
                optionsArea.appendChild(empty);
                return;
            }

            visibleOptions.slice(0, 60).forEach(function (option) {
                optionsArea.appendChild(createOptionButton(option));
            });

            if (visibleOptions.length > 60) {
                var remaining = document.createElement("div");
                remaining.className = "coauthor-results-note";
                remaining.textContent = "Показаны первые 60 из " + visibleOptions.length + ". Уточните запрос, чтобы найти нужного пользователя.";
                optionsArea.appendChild(remaining);
            }
        }

        search.addEventListener("input", render);
        select.addEventListener("change", render);
        picker.classList.add("is-enhanced");
        render();
    }

    function initializeMetadataExtraction() {
        var fileInput = document.getElementById("id_file");
        var panel = document.querySelector("[data-metadata-extraction]");
        if (!fileInput || !panel) {
            return;
        }
        var endpoint = fileInput.getAttribute("data-metadata-extract-url");
        var status = panel.querySelector("[data-metadata-status]");
        var summary = panel.querySelector("[data-metadata-summary]");
        var requestNumber = 0;
        var fieldMap = {
            title: document.getElementById("id_title"),
            abstract: document.getElementById("id_abstract"),
            document_authors: document.getElementById("id_document_authors"),
            organizations: document.getElementById("id_organizations"),
            contact_emails: document.getElementById("id_contact_emails"),
            keywords: document.getElementById("id_keywords")
        };

        Object.keys(fieldMap).forEach(function (key) {
            var field = fieldMap[key];
            if (!field) {
                return;
            }
            field.addEventListener("input", function (event) {
                if (event.isTrusted) {
                    field.dataset.autoExtracted = "false";
                }
            });
        });

        function csrfToken() {
            var input = document.querySelector("input[name='csrfmiddlewaretoken']");
            return input ? input.value : "";
        }

        function setPanel(kind, title, text) {
            panel.hidden = false;
            panel.classList.remove("is-success", "is-warning");
            if (kind) {
                panel.classList.add("is-" + kind);
            }
            status.textContent = title;
            summary.textContent = text;
        }

        function applyValue(field, value) {
            if (!field || !value) {
                return;
            }
            var mayReplace = !field.value.trim() || field.dataset.autoExtracted === "true";
            if (!mayReplace) {
                return;
            }
            field.value = value;
            field.dataset.autoExtracted = "true";
            field.dispatchEvent(new Event("change", {bubbles: true}));
        }

        function applyMatchedUsers(matches) {
            var select = document.getElementById("id_co_authors");
            if (!select) {
                return 0;
            }
            var count = 0;
            (matches || []).forEach(function (match) {
                if (match.is_current_user) {
                    return;
                }
                var option = Array.prototype.find.call(select.options, function (item) {
                    return String(item.value) === String(match.user_id);
                });
                if (option && !option.selected) {
                    option.selected = true;
                    count += 1;
                }
            });
            if (count) {
                select.dispatchEvent(new Event("change", {bubbles: true}));
            }
            return count;
        }

        function runExtraction() {
            var file = fileInput.files && fileInput.files[0];
            if (!file || !endpoint) {
                panel.hidden = true;
                return;
            }
            requestNumber += 1;
            var currentRequest = requestNumber;
            setPanel("", "Читаем документ…", "Ищем название, авторов, организации, e-mail, аннотацию и ключевые слова.");
            var body = new FormData();
            body.append("file", file, file.name);
            fetch(endpoint, {
                method: "POST",
                body: body,
                headers: {
                    "X-CSRFToken": csrfToken(),
                    "X-Requested-With": "XMLHttpRequest"
                },
                credentials: "same-origin"
            })
                .then(function (response) {
                    return response.json().then(function (payload) {
                        if (!response.ok) {
                            throw new Error(payload.error || "metadata-extraction-failed");
                        }
                        return payload;
                    });
                })
                .then(function (payload) {
                    if (currentRequest !== requestNumber) {
                        return;
                    }
                    var metadata = payload.metadata || {};
                    Object.keys(fieldMap).forEach(function (key) {
                        applyValue(fieldMap[key], metadata[key] || "");
                    });
                    var matchedCount = applyMatchedUsers(payload.matched_users || []);
                    var authorCount = (metadata.authors || []).length;
                    var found = [];
                    if (metadata.title) { found.push("название"); }
                    if (authorCount) { found.push("авторов: " + authorCount); }
                    if (metadata.abstract) { found.push("аннотацию"); }
                    if (metadata.keywords) { found.push("ключевые слова"); }
                    if (metadata.contact_emails) { found.push("e-mail"); }
                    var parserWarning = payload.analysis && payload.analysis.parse_error;
                    var text = found.length ? "Распознано: " + found.join(", ") + "." : "Автоматически заполнить поля не удалось.";
                    if (matchedCount) {
                        text += " Пользователей системы сопоставлено: " + matchedCount + ".";
                    }
                    if (parserWarning) {
                        text += " " + parserWarning;
                    }
                    setPanel(found.length ? "success" : "warning", found.length ? "Метаданные добавлены — проверьте их" : "Нужна ручная проверка", text);
                })
                .catch(function (error) {
                    if (currentRequest !== requestNumber) {
                        return;
                    }
                    setPanel("warning", "Не удалось прочитать метаданные", error.message === "metadata-extraction-failed" ? "Заполните поля вручную; файл всё равно можно отправить." : error.message);
                });
        }

        fileInput.addEventListener("change", runExtraction);
    }

    document.addEventListener("DOMContentLoaded", function () {
        initializeDestinationAndTemplate();
        initializeFileZone();
        initializeWizard();
        initializeCoauthors();
    });
})();
