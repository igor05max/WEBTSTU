(() => {
    const sourceForm = document.querySelector("[data-citation-source-form]");
    if (sourceForm) {
        const submission = sourceForm.querySelector("[name='submission']");
        const file = sourceForm.querySelector("[name='file']");
        const text = sourceForm.querySelector("[name='text']");
        const fileStatus = sourceForm.querySelector("[data-citation-file-status]");
        const submitButton = sourceForm.querySelector("[data-citation-search-submit]");

        const clearFile = () => {
            if (file) file.value = "";
            if (fileStatus) {
                fileStatus.hidden = true;
                fileStatus.textContent = "";
            }
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
                fileStatus.textContent = `Выбран файл: ${selectedFile.name} · ${megabytes.toFixed(2)} МБ`;
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

        sourceForm.addEventListener("submit", () => {
            const selectedFile = file?.files?.[0];
            if (!selectedFile || !submitButton) return;
            submitButton.disabled = true;
            submitButton.textContent = "Файл загружен · ищем источники…";
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

    const setButtonState = (button, isAdded) => {
        button.classList.toggle("is-added", isAdded);
        button.setAttribute("aria-pressed", isAdded ? "true" : "false");
        button.innerHTML = isAdded
            ? "<span>−</span> Убрать ссылку"
            : "<span>+</span> Добавить ссылку";
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
                    items: [],
                });
            }
            grouped.get(item.articleId).items.push(item);
        });
        [...grouped.values()].forEach((group, index) => {
            const number = index + 1;
            const entry = document.createElement("li");
            entry.innerHTML = `
                <div><span>[${number}]</span><strong></strong></div>
                <p></p>
                <button type="button" aria-label="Удалить источник">×</button>
            `;
            entry.querySelector("strong").textContent = group.title;
            entry.querySelector("p").textContent = group.items.length > 1
                ? `Для ${group.items.length} фрагментов · ${group.citation}`
                : group.citation;
            entry.querySelector("button").addEventListener("click", () => {
                group.items.forEach((item) => {
                    selected.delete(item.key);
                    setButtonState(item.button, false);
                });
                render();
            });
            list.append(entry);
        });
        const hasItems = selected.size > 0;
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

    document.querySelectorAll("[data-add-citation]").forEach((button) => {
        button.addEventListener("click", () => {
            const source = button.closest(".citation-source");
            const claim = button.closest(".citation-claim");
            const key = `${button.dataset.claimId}::${button.dataset.articleId}`;
            if (selected.has(key)) {
                selected.delete(key);
                setButtonState(button, false);
                render();
                return;
            }
            selected.set(key, {
                key,
                claimId: button.dataset.claimId,
                articleId: button.dataset.articleId,
                title: source.querySelector("h4").textContent.trim(),
                citation: source.querySelector(".citation-text").textContent.trim(),
                claim: claim.querySelector("h3").textContent.trim(),
                button,
            });
            setButtonState(button, true);
            render();
        });
    });

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
            placements.push(`${item.claim} [${articleNumbers.get(item.articleId)}]`);
        });
        const text = `ССЫЛКИ В ТЕКСТЕ\n${placements.join("\n\n")}\n\nСПИСОК ЛИТЕРАТУРЫ\n${references.join("\n")}`;
        await navigator.clipboard.writeText(text);
        copyButton.textContent = "Скопировано";
        window.setTimeout(() => { copyButton.textContent = "Скопировать список"; }, 1800);
    });

    render();
})();
