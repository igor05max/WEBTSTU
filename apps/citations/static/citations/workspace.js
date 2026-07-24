(() => {
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
        const articleNumbers = new Map();
        let nextNumber = 1;
        selected.forEach((item) => {
            if (!articleNumbers.has(item.articleId)) {
                articleNumbers.set(item.articleId, nextNumber++);
            }
            const number = articleNumbers.get(item.articleId);
            const entry = document.createElement("li");
            entry.innerHTML = `
                <div><span>[${number}]</span><strong></strong></div>
                <p></p>
                <button type="button" aria-label="Удалить источник">×</button>
            `;
            entry.querySelector("strong").textContent = item.title;
            entry.querySelector("p").textContent = item.citation;
            entry.querySelector("button").addEventListener("click", () => {
                selected.delete(item.key);
                setButtonState(item.button, false);
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
