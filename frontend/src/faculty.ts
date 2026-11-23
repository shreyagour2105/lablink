const forms = document.querySelectorAll<HTMLFormElement>("[data-faculty-form]");

for (const form of forms) {
  form.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    if (form.dataset.loading) {
      event.preventDefault();
      return;
    }
    form.dataset.loading = "true";
    const status = form.querySelector<HTMLElement>("[data-loading-status]");
    if (status) {
      status.classList.remove("visually-hidden");
      status.textContent = form.dataset.loadingMessage ?? "Saving decision…";
    }
  });
}

window.addEventListener("pageshow", () => {
  for (const form of forms) {
    delete form.dataset.loading;
    const status = form.querySelector<HTMLElement>("[data-loading-status]");
    if (status) {
      status.textContent = "";
      status.classList.add("visually-hidden");
    }
  }
});

const dialog = document.querySelector<HTMLDialogElement>("#decision-dialog");
const title = document.querySelector("#decision-title");
const description = document.querySelector("#decision-description");
const confirmButton = document.querySelector("#confirm-decision");

if (dialog?.showModal && title && description && confirmButton) {
  let pending: { form: HTMLFormElement; button: HTMLButtonElement } | undefined;
  let confirmed = false;

  document.addEventListener("submit", (event) => {
    const form = event.target;
    const button = event.submitter;
    if (
      !(form instanceof HTMLFormElement) || !form.hasAttribute("data-decision-form") ||
      !(button instanceof HTMLButtonElement) || confirmed
    ) return;

    event.preventDefault();
    pending = { form, button };
    const approving = button.value === "approved";
    const rescheduling = button.value === "reschedule requested";
    const slot = form.querySelector<HTMLSelectElement>("select[name=slot]")?.selectedOptions[0];
    const slotLabel = slot?.textContent?.trim().replace(/^(?:#\d+ · )?(?:Requested|Alternative) · /, "");
    title.textContent = approving ? "Approve this request?" : rescheduling ? "Request rescheduling?" : "Reject this request?";
    description.textContent = approving && slotLabel
      ? `Reserve ${slotLabel} if it is still available.`
      : "Your decision note will be visible to the student.";
    confirmButton.textContent = approving ? "Confirm approval" : rescheduling ? "Confirm rescheduling" : "Confirm rejection";
    dialog.returnValue = "";
    dialog.showModal();
    dialog.querySelector<HTMLElement>("[autofocus]")?.focus();
  }, { capture: true });

  dialog.addEventListener("close", () => {
    if (!pending) return;
    const { form, button } = pending;
    pending = undefined;
    button.focus();
    if (dialog.returnValue === "confirm") {
      confirmed = true;
      try {
        form.requestSubmit(button);
      } finally {
        confirmed = false;
      }
    }
  });
}
