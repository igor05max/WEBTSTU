(() => {
    const passwordInput = document.getElementById("id_password");
    const passwordToggle = document.querySelector("[data-password-toggle]");
    const authForm = document.querySelector("[data-auth-form]");

    if (passwordInput && passwordToggle) {
        passwordToggle.addEventListener("click", () => {
            const shouldShowPassword = passwordInput.type === "password";
            passwordInput.type = shouldShowPassword ? "text" : "password";
            passwordToggle.setAttribute("aria-pressed", String(shouldShowPassword));
            passwordToggle.setAttribute(
                "aria-label",
                shouldShowPassword ? "Скрыть пароль" : "Показать пароль",
            );
            passwordInput.focus({ preventScroll: true });
        });
    }

    if (authForm) {
        authForm.addEventListener("submit", () => {
            const submitButton = authForm.querySelector(".auth-submit");
            submitButton?.classList.add("is-loading");
        });
    }
})();
