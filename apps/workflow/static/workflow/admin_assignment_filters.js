(function () {
    const endpointUrl = "/workflow/assignment-options/";

    function createOption(value, label, selected) {
        const option = document.createElement("option");
        option.value = String(value);
        option.textContent = label;
        if (selected) {
            option.selected = true;
        }
        return option;
    }

    function fillSelect(selectElement, items, selectedValue) {
        if (!selectElement) {
            return;
        }

        const normalizedSelectedValue = selectedValue == null ? "" : String(selectedValue);
        const fragment = document.createDocumentFragment();
        fragment.appendChild(createOption("", "---------", normalizedSelectedValue === ""));

        let matched = normalizedSelectedValue === "";
        items.forEach((item) => {
            const isSelected = String(item.id) === normalizedSelectedValue;
            if (isSelected) {
                matched = true;
            }
            fragment.appendChild(createOption(item.id, item.name, isSelected));
        });

        selectElement.innerHTML = "";
        selectElement.appendChild(fragment);
        if (!matched) {
            selectElement.value = "";
        }
    }

    function findRow(selectElement) {
        const row =
            selectElement.closest("[data-assignment-scope]")
            || selectElement.closest("tr")
            || selectElement.closest(".inline-related")
            || selectElement.closest(".form-row");
        if (!row || row.classList.contains("empty-form")) {
            return null;
        }
        return row;
    }

    function getRowFields(row) {
        const groupSelect = row.querySelector(
            'select[name$="-target_unit"], select[name="target_unit"], select[name$="-assigned_unit"], select[name="assigned_unit"]'
        );
        const roleSelect = row.querySelector(
            'select[name$="-target_group"], select[name="target_group"], select[name$="-assigned_group"], select[name="assigned_group"]'
        );
        const userSelect = row.querySelector(
            'select[name$="-target_user"], select[name="target_user"], select[name$="-assigned_user"], select[name="assigned_user"]'
        );

        if (!groupSelect || !roleSelect || !userSelect) {
            return null;
        }

        return { groupSelect, roleSelect, userSelect };
    }

    async function loadOptions(groupId, roleId) {
        const params = new URLSearchParams();
        if (groupId) {
            params.set("group_id", groupId);
        }
        if (roleId) {
            params.set("role_id", roleId);
        }

        const response = await fetch(endpointUrl + "?" + params.toString(), {
            credentials: "same-origin",
            headers: { "X-Requested-With": "XMLHttpRequest" },
        });

        if (!response.ok) {
            throw new Error("Не удалось загрузить зависимые списки.");
        }

        return response.json();
    }

    async function syncFromGroup(row) {
        const fields = getRowFields(row);
        if (!fields) {
            return;
        }

        const currentRole = fields.roleSelect.value;
        const currentUser = fields.userSelect.value;
        const groupId = fields.groupSelect.value;

        if (!groupId) {
            fillSelect(fields.roleSelect, [], "");
            fillSelect(fields.userSelect, [], "");
            return;
        }

        const rolePayload = await loadOptions(groupId, "");
        fillSelect(fields.roleSelect, rolePayload.roles, currentRole);

        const effectiveRole = fields.roleSelect.value;
        if (!effectiveRole) {
            fillSelect(fields.userSelect, [], "");
            return;
        }

        const userPayload = await loadOptions(groupId, effectiveRole);
        fillSelect(fields.userSelect, userPayload.users, currentUser);
    }

    async function syncFromRole(row) {
        const fields = getRowFields(row);
        if (!fields) {
            return;
        }

        const groupId = fields.groupSelect.value;
        const roleId = fields.roleSelect.value;
        const currentUser = fields.userSelect.value;

        if (!groupId || !roleId) {
            fillSelect(fields.userSelect, [], "");
            return;
        }

        const userPayload = await loadOptions(groupId, roleId);
        fillSelect(fields.userSelect, userPayload.users, currentUser);
    }

    function initializeExistingRows() {
        document.querySelectorAll(
            'select[name$="-target_unit"], select[name="target_unit"], select[name$="-assigned_unit"], select[name="assigned_unit"]'
        ).forEach((selectElement) => {
            const row = findRow(selectElement);
            if (row) {
                syncFromGroup(row).catch(console.error);
            }
        });
    }

    document.addEventListener("change", function (event) {
        const target = event.target;
        if (!(target instanceof HTMLSelectElement)) {
            return;
        }

        if (
            target.name.endsWith("-target_unit")
            || target.name === "target_unit"
            || target.name.endsWith("-assigned_unit")
            || target.name === "assigned_unit"
        ) {
            const row = findRow(target);
            if (row) {
                syncFromGroup(row).catch(console.error);
            }
            return;
        }

        if (
            target.name.endsWith("-target_group")
            || target.name === "target_group"
            || target.name.endsWith("-assigned_group")
            || target.name === "assigned_group"
        ) {
            const row = findRow(target);
            if (row) {
                syncFromRole(row).catch(console.error);
            }
        }
    });

    document.addEventListener("DOMContentLoaded", function () {
        initializeExistingRows();
    });
})();
