import { initializeApp } from "@firebase/app";
import {
  connectAuthEmulator,
  getAuth,
  inMemoryPersistence,
  setPersistence,
  signInWithEmailAndPassword,
  signOut,
} from "@firebase/auth";
import { isSessionResponse } from "../../shared/auth";

type BrowserConfig = { apiKey: string; projectId: string; emulatorHost: string };

const form = document.querySelector<HTMLFormElement>("#sign-in");
const configNode = document.querySelector("#firebase-config");
const status = document.querySelector<HTMLElement>("#sign-in-status");
const submit = form?.querySelector<HTMLButtonElement>("button[type=submit]");
const emailInput = form?.querySelector<HTMLInputElement>("#email");
const passwordInput = form?.querySelector<HTMLInputElement>("#password");

if (form && configNode && status && submit && emailInput && passwordInput) {
  const config: BrowserConfig = JSON.parse(configNode.textContent ?? "");
  const app = initializeApp({ apiKey: config.apiKey, projectId: config.projectId });
  const auth = getAuth(app);
  if (config.emulatorHost) {
    connectAuthEmulator(auth, `http://${config.emulatorHost}`, { disableWarnings: true });
  }

  submit.disabled = false;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = new FormData(form);
    const email = emailInput.value.trim();
    const password = passwordInput.value;
    const csrf = String(fields.get("csrfmiddlewaretoken") ?? "");
    submit.disabled = true;
    status.textContent = "Signing in…";

    try {
      await setPersistence(auth, inMemoryPersistence);
      const credential = await signInWithEmailAndPassword(auth, email, password);
      const token = await credential.user.getIdToken();
      const response = await fetch(form.action, {
        method: "POST",
        headers: { "X-CSRFToken": csrf },
        body: new URLSearchParams({ id_token: token }),
        credentials: "same-origin",
      });
      const result: unknown = await response.json();
      if (response.ok && isSessionResponse(result)) {
        window.location.assign(form.dataset.dashboard ?? "/dashboard/");
        return;
      }
      status.textContent = formError(result);
    } catch {
      status.textContent = "Could not sign in. Check your credentials and connection, then try again.";
    } finally {
      passwordInput.value = "";
      await signOut(auth).catch(() => undefined);
      submit.disabled = false;
    }
  });
}

function formError(value: unknown): string {
  if (typeof value === "object" && value && "error" in value && typeof value.error === "string") {
    return value.error;
  }
  return "Could not start a LabLink session. Try again.";
}
