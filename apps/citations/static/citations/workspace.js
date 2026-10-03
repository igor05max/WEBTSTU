(() => {
    const sourceForm = document.querySelector("[data-citation-source-form]");
    if (sourceForm) {
        const submission = sourceForm.querySelector("[name='submission']");
        const file = sourceForm.querySelector("[name='file']");
        const text = sourceForm.querySelector("[name='text']");
        const fileStatus = sourceForm.querySelector("[data-citation-file-status]");
        const fileStatusName = sourceForm.querySelector("[data-citation-file-name]");
        const fileStatusSize = sourceForm.querySelector("[data-citation-file-size]");
        const submitButton = sourceForm.querySelector("[data-citation-search-submit]");
        const localLoading = sourceForm.querySelector("[data-citation-local-loading]");

        const clearFile = () => {
            if (file) file.value = "";
            if (fileStatus) {
                fileStatus.hidden = true;
            }
            if (fileStatusName) {
                fileStatusName.textContent = "";
                fileStatusName.removeAttribute("title");
            }
            if (fileStatusSize) fileStatusSize.textContent = "";
        };

        file?.addEventListener("change", () => {
            const selectedFile = file.files?.[0];
            if (!selectedFile) {
                clearFile();
                return;
            }
            if (submission) submission.value = "";
            if (text) text.value = "";
            if (fileStatus) {
                const megabytes = Math.max(0.01, selectedFile.size / 1024 / 1024);
                if (fileStatusName) {
                    fileStatusName.textContent = selectedFile.name;
                    fileStatusName.title = selectedFile.name;
                }
                if (fileStatusSize) {
                    fileStatusSize.textContent = `${megabytes.toFixed(2)} МБ`;
                }
                fileStatus.hidden = false;
            }
        });

        submission?.addEventListener("change", () => {
            if (!submission.value) return;
            clearFile();
            if (text) text.value = "";
        });

        text?.addEventListener("input", () => {
            if (!text.value.trim()) return;
            if (submission) submission.value = "";
            clearFile();
        });

        sourceForm.addEventListener("submit", (event) => {
            if (event.defaultPrevented || !sourceForm.checkValidity()) return;
            const selectedFile = file?.files?.[0];
            if (submitButton) {
                submitButton.disabled = true;
                submitButton.textContent = selectedFile
                    ? "Файл загружен · ищем источники…"
                    : "Анализируем · ищем источники…";
            }
            if (localLoading) localLoading.hidden = false;
            sourceForm.setAttribute("aria-busy", "true");
        });
    }

    const autoForm = document.querySelector("[data-auto-analyze]");
    if (autoForm) {
        window.setTimeout(() => autoForm.requestSubmit(), 120);
    }

    const plan = document.querySelector("[data-citation-plan]");
    if (!plan) return;

    const selected = new Map();
    const list = plan.querySelector("[data-plan-list]");
    const empty = plan.querySelector("[data-plan-empty]");
    const copyButton = plan.querySelector("[data-copy-plan]");
    const applyForm = plan.querySelector("[data-apply-form]");
    const selectionInput = plan.querySelector("[data-selection-input]");
    const planCount = plan.querySelector("[data-plan-count]");

    const setButtonState = (button, isAdded, number = null) => {
        button.classList.toggle("is-added", isAdded);
        button.setAttribute("aria-pressed", isAdded ? "true" : "false");
        button.textContent = isAdded
            ? `Выбрано · [${number}] · убрать`
            : "Выбрать для этого текста";
        button.closest(".citation-source")?.classList.toggle("is-selected", isAdded);
    };

    const render = () => {
        list.innerHTML = "";
        const grouped = new Map();
        selected.forEach((item) => {
            if (!grouped.has(item.articleId)) {
                grouped.set(item.articleId, {
                    articleId: item.articleId,
                    title: item.title,
                    citation: item.citation,
                    url: item.url,
                    items: [],
                });
            }
            grouped.get(item.articleId).items.push(item);
        });
        const articleNumbers = new Map();
        [...grouped.values()].forEach((group, index) => {
            const number = index + 1;
            articleNumbers.set(group.articleId, number);
            const entry = document.createElement("li");
            entry.innerHTML = `
                <div class="citation-plan-entry-heading">
                    <span>[${number}]</span><strong></strong>
                    <button type="button" aria-label="Убрать статью из документа">×</button>
                </div>
                <ul class="citation-plan-placements"></ul>
                <a class="citation-plan-source-link" target="_blank" rel="noreferrer">Открыть статью ↗</a>
                <details class="citation-plan-reference"><summary>Запись в списке литературы</summary><p></p></details>
            `;
            entry.querySelector("strong").textContent = group.title;
            const placements = entry.querySelector(".citation-plan-placements");
            group.items.forEach((item) => {
                const placement = document.createElement("li");
                placement.textContent = `Ваш текст №${item.claimNumber}: ${item.claim}`;
                placements.append(placement);
            });
            const articleLink = entry.querySelector(".citation-plan-source-link");
            if (group.url) {
                articleLink.href = group.url;
            } else {
                articleLink.hidden = true;
            }
            entry.querySelector("p").textContent = group.citation;
            entry.querySelector("button").addEventListener("click", () => {
                group.items.forEach((item) => {
                    selected.delete(item.key);
                    setButtonState(item.button, false);
                });
                render();
            });
            list.append(entry);
        });
        selected.forEach((item) => {
            setButtonState(item.button, true, articleNumbers.get(item.articleId));
        });
        document.querySelectorAll(".citation-claim").forEach((claim) => {
            const numbers = [...new Set(
                [...selected.values()]
                    .filter((item) => item.claimId === claim.dataset.claimId)
                    .map((item) => articleNumbers.get(item.articleId))
            )].sort((a, b) => a - b);
            claim.querySelector("[data-claim-markers]").textContent = numbers.length
                ? numbers.map((number) => `[${number}]`).join(" ")
                : "—";
            claim.classList.toggle("has-citation", numbers.length > 0);
        });
        const hasItems = selected.size > 0;
        if (planCount) planCount.textContent = String(grouped.size);
        empty.hidden = hasItems;
        copyButton.hidden = !hasItems;
        if (applyForm) applyForm.hidden = !hasItems;
        if (selectionInput) {
            selectionInput.value = JSON.stringify(
                [...selected.values()].map((item) => ({
                    claim_id: item.claimId,
                    article_id: item.articleId,
                }))
            );
        }
    };

    const updateSelection = (button, shouldAdd) => {
        const source = button.closest(".citation-source");
        const claim = button.closest(".citation-claim");
        const key = `${button.dataset.claimId}::${button.dataset.articleId}`;
        const add = shouldAdd ?? !selected.has(key);
        if (add) {
            selected.set(key, {
                key,
                claimId: button.dataset.claimId,
                articleId: button.dataset.articleId,
                claimNumber: button.dataset.claimNumber,
                title: source.querySelector("h4").textContent.trim(),
                citation: source.querySelector(".citation-text").textContent.trim(),
                url: source.querySelector(".citation-source-actions a")?.href || "",
                claim: claim.querySelector("h3").textContent.trim(),
                button,
            });
        } else {
            selected.delete(key);
            setButtonState(button, false);
        }
    };

    const citationButtons = [...document.querySelectorAll("[data-add-citation]")];
    citationButtons.forEach((button) => {
        button.addEventListener("click", () => {
            updateSelection(button);
            render();
        });
    });

    const initialSelectionsElement = document.getElementById(
        "citation-initial-selections"
    );
    if (initialSelectionsElement) {
        try {
            const initialSelections = JSON.parse(
                initialSelectionsElement.textContent || "[]"
            );
            const initialKeys = new Set(
                initialSelections.map(
                    (item) => `${item.claim_id}::${item.article_id}`
                )
            );
            citationButtons.forEach((button) => {
                const key = `${button.dataset.claimId}::${button.dataset.articleId}`;
                if (initialKeys.has(key)) {
                    updateSelection(button, true);
                }
            });
        } catch (_error) {
            // A stale saved selection must not break the recommendations page.
        }
    }

    copyButton?.addEventListener("click", async () => {
        const articleNumbers = new Map();
        let nextNumber = 1;
        const references = [];
        const placements = [];
        selected.forEach((item) => {
            if (!articleNumbers.has(item.articleId)) {
                articleNumbers.set(item.articleId, nextNumber++);
                references.push(`[${articleNumbers.get(item.articleId)}] ${item.citation}`);
            }
            placements.push(`Ваш текст №${item.claimNumber}: ${item.claim} [${articleNumbers.get(item.articleId)}]`);
        });
        const text = `ССЫЛКИ В ТЕКСТЕ\n${placements.join("\n\n")}\n\nСПИСОК ЛИТЕРАТУРЫ\n${references.join("\n")}`;
        await navigator.clipboard.writeText(text);
        copyButton.textContent = "Скопировано";
        window.setTimeout(() => {
            copyButton.textContent = "Скопировать ссылки и список литературы";
        }, 1800);
    });

    render();
})();
