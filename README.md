# LabLink

LabLink is a campus equipment catalog and project booking workflow. Its demo seed contains three **sample** records for fictional departments and labs. Students browse current availability and submit requests. Assigned faculty can approve a request to reserve the selected interval or reject it with a reason.

## Architecture

Django serves the pages and handles booking decisions. TypeScript handles browser sign-in through Firebase Authentication. The browser exchanges its Firebase ID token for a secure session with Django. The server reads roles, lab assignments, requests and reservations from Firestore. The scheduler computes recommendations before approval, and the approval transaction checks the selected slot against the current records.

## Local development

Install Python 3.12, Node.js 24, and JDK 21. Run the commands below from the repository root.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm ci
cp .env.example .env
```

Generate a local Django secret with `.venv/bin/python -c 'from secrets import token_urlsafe; print(token_urlsafe(48))'` and put it in `DJANGO_SECRET_KEY` in the ignored `.env` file. The example config targets the local Firebase emulators. The demo web API key is a placeholder for the emulator.

Start the emulators in one terminal. The first launch downloads the Firestore emulator from `storage.googleapis.com`.

```sh
npx --yes firebase-tools@15.37.0 emulators:start --only auth,firestore --project demo-lablink
```

If the Firestore download host is blocked, run the Auth emulator alone with `--only auth` and start the official Google Cloud CLI Firestore emulator in Docker in another terminal:

```sh
docker run --rm -p 127.0.0.1:8080:8080 gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators \
  gcloud beta emulators firestore start --host-port=0.0.0.0:8080 --project=demo-lablink
```

Use the Firebase CLI path when you need to exercise the checked-in Firestore rules locally. With the Firebase CLI, to retain accounts and records across emulator restarts, use `--export-on-exit .firebase/demo` and add `--import .firebase/demo` on subsequent starts.

In another terminal, seed the catalog and create two test identities. Each command prompts for the password and prints the user’s UID. Choose your own passwords.

```sh
.venv/bin/python backend/manage.py seed_demo
.venv/bin/python backend/manage.py create_demo_user student@example.test
.venv/bin/python backend/manage.py create_demo_user faculty@example.test

.venv/bin/python backend/manage.py provision_account STUDENT_UID student
.venv/bin/python backend/manage.py provision_account FACULTY_UID faculty --lab demo-fabrication

./scripts/build
./scripts/dev
```

Replace `STUDENT_UID` and `FACULTY_UID` with the returned UIDs. Open `http://127.0.0.1:8000` locally. To try approval with the example lab assignment, request the **FDM 3D Printer** using a listed weekday interval. Enter the start and deadline in the displayed Asia/Kolkata time zone. Use a second browser profile or sign out before switching between student and faculty accounts. `npm run dev:assets` watches frontend TypeScript.

## Tests

```sh
./scripts/build
./scripts/test
```

Start both emulators to run the integration tests. They exercise Firebase token exchange and role checks, Firestore writes, booking decisions, and concurrent approval, using isolated collection names and temporary Firebase test accounts. Without emulator hosts configured, the integration cases are skipped.

## Data and security

Django renders the HTML and serves the protected workflow. The browser uses Firebase Authentication for email and password sign-in, then exchanges the ID token for an HTTP-only session cookie. Django checks the token and session with Firebase and reads permission data from the Firestore `accounts` collection. Assign roles with the trusted provisioning command. All forms that modify state use CSRF protection. Firestore rules deny direct browser reads and writes.

Firestore stores equipment operating windows and closures, project requests, and confirmed reservations. A pending request does not hold time. Approval writes the decision and reservation atomically, and document versions prevent simultaneous approvals from double-booking an equipment unit or a student. This version supports a seven-calendar-day window and 15-minute increments. Times are stored as instants and displayed in the equipment lab’s time zone.

## Cloud Run

The Dockerfile builds the frontend assets and serves Django with Gunicorn on `PORT`. Use a real Firebase project with Email/Password enabled and a Firestore database. Deploy `firestore.rules`, configure the Firebase project ID and public web API key, and grant the Cloud Run service account the Firestore and Firebase Auth permissions required to read and write bookings, read users, and issue session cookies. Use Application Default Credentials and provision users through Firebase and the management command. Keep `DJANGO_DEBUG=false`, omit emulator variables, set `DJANGO_ALLOWED_HOSTS` to the deployed hostname, set `DJANGO_BEHIND_PROXY=true` behind Cloud Run ingress, and inject `DJANGO_SECRET_KEY` from Secret Manager. No service-account key belongs in the image or repository.

### Deployment verification

1. In the deployment project, enable Firebase Authentication with the Email/Password provider, create the default Firestore database in Native mode, and deploy the checked-in Firestore rules. Provision the Cloud Run runtime identity with the required Firebase Auth and Firestore permissions. Add the Django signing key to Secret Manager and grant the runtime identity access to that secret.
2. Build the repository Dockerfile and publish the image to Artifact Registry. Deploy it to Cloud Run using the runtime identity. Configure `FIREBASE_PROJECT_ID` and the Firebase Web API key for the same Firebase project. Set `DJANGO_ALLOWED_HOSTS` to the exact hostname of the Cloud Run service URL, without the scheme. Enable public HTTP access to the service after its production settings are in place.
3. Keep the default TCP startup probe on the container port. Smoke-test the public HTTPS landing page and the hashed frontend asset on the sign-in page. Django serves the pages and booking endpoints from the same origin, so these routes need no cross-origin CORS settings. Keep CSRF protection on for form submissions.
4. Seed the sample catalogue and create separate student and faculty test identities in the deployment Firebase project. Run the provisioning commands with production credentials, then verify that a student can request equipment and see the reservation after assigned faculty approve it. Verify that a competing approval is rejected when it overlaps a confirmed booking.

## Scheduling policy

The scheduler evaluates the stored request and a single current-time input. It searches the next seven calendar days in the equipment's lab time zone in 15-minute increments. Durations must be 15–480 minutes and divisible by 15. An interval must fit entirely inside operating hours, end by the project deadline, and avoid maintenance, confirmed reservations for the unit, and the student's other reservations. Intervals include their start and exclude their end, so adjacent bookings are allowed.

A unit must be active, in the requested lab, and satisfy all required capability tags. Capability matching ignores case and repeated whitespace. The originally requested equipment's capabilities remain requirements when a student supplies explicit tags. Equipment is open to provisioned students by default. To restrict a unit, set its Firestore `access` to `restricted` and grant eligible students that lab using `provision_account UID student --eligible-lab LAB_ID`. Only assigned faculty can decide a request. Without both student-entered alternative bounds, approval must use the requested equipment and start time. With both bounds, faculty can use another qualified unit in the same lab or another time that fits the student's window.

Scores compare feasible requests. They use integer arithmetic and a maximum of 100 points.

| Component | Points | Formula |
| --- | ---: | --- |
| Deadline urgency | 0–40 | `max(0, 40 - floor(40 × whole seconds until deadline / 604800))` |
| Slot scarcity | 0–25 | `max(0, 26 - number of feasible start-and-unit pairs)` |
| Equipment fit | 10–20 | 20 when the preferred unit has a feasible slot, 10 when only equivalent units fit. All required capabilities are mandatory either way. |
| Prior-usage fairness | 0–15 | `max(0, 15 - floor(approved minutes in the previous seven days / 30))`. Time is clipped to that period. This measures booked time. |

Feasible requests waiting at least 48 hours come first, oldest submission first. Resolve equal waiting times and rank younger requests by descending score, earlier deadline, earlier submission, then request ID. Within a request, the preferred unit comes first, followed by smallest distance from the requested start, earlier start, then unit ID. The ordered pass proposes up to three options per request. Its first proposal temporarily blocks overlapping recommendations for the same unit or student later in the pass. These temporary blocks apply only to that planning run.

Infeasible requests get no recommendation. Urgency and aging never relax a constraint. Approval rechecks eligibility, capabilities, window and deadline, and confirmed overlaps in Firestore. Version preconditions on the request, unit, and student guard the atomic reservation write. If another approval changes those records, the server rereads current state before retrying.

This is a greedy policy for a small campus demo, so results depend on the pending queue and one snapshot can become stale. It does not maximize global utilization, hold tentative slots, or guarantee a reservation for a request that cannot fit before its deadline. Faculty approval is explicit. A chosen alternative can change the options for lower-priority requests.

## Faculty dashboard

The dashboard reads requests, decisions, equipment, and reservations for the signed-in faculty member's assigned labs. It explains conflicts at the requested time and offers feasible alternatives inside the student's consent window. A decision only shows a success confirmation after Firestore commits it. A stale approval leaves the request pending and refreshes the queue. Rejections require a reason, which appears in faculty decision history and the student's booking history.

Utilisation reports cover the trailing 7 or 30 days, with exact UTC boundaries shown on the dashboard. For each unit, available hours come from its recorded operating schedule minus overlapping closures. Booked hours count only the portion of confirmed reservations that falls inside that capacity and the selected period. Upcoming time is excluded. An in-progress reservation contributes its elapsed portion. The dashboard displays “No capacity” for a zero denominator. These figures use the schedule currently recorded in Firestore, so editing a recurring schedule can change historical capacity. They measure booked time. There is no equipment check-in or telemetry in this version.

## Gemini recommendation explanations

Faculty can select **Explain first recommendation** on a pending request. Django recomputes the current recommendation and sends Gemini only a short list of eligible, predefined evidence statements. The model chooses which two points to highlight and their order. Django checks the returned IDs against the evidence for that specific recommendation and renders the corresponding server-owned wording. Project descriptions, account details, equipment IDs, and booking times stay inside LabLink.

Gemini has no access to booking actions or equipment records. The explanation endpoint is authenticated, lab-scoped, CSRF-protected, and read-only with respect to bookings. Availability, ranking, and approval remain deterministic. Catalogue search uses the existing equipment records and filters.

Set `GEMINI_API_KEY` in the ignored local `.env` or inject it from Secret Manager on Cloud Run. The server sends the key in a request header to `generativelanguage.googleapis.com`. Set `GEMINI_MODEL` to a supported Gemini model if needed. With the key unset, the button uses the rules-based explanation and makes no external call. The request uses a fixed prompt size limit, short connect and read timeouts, and a capped response. Quota errors, network failures, and invalid responses fall back to the deterministic explanation without a retry.

## Vercel Hobby

Import this repository with project root `.`, Framework Preset **Other**, and Node.js 24. Use the build, install and output settings in `vercel.json`. Python 3.12 runs the Django WSGI app, including booking and authentication. Vercel serves the generated static assets before dynamic URLs route to Django.

Configure `DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (false), `DJANGO_BEHIND_PROXY` (true), `DJANGO_ALLOWED_HOSTS` (exact Vercel hostname), `FIREBASE_PROJECT_ID`, `FIREBASE_WEB_API_KEY`, and `LABLINK_GOOGLE_CREDENTIALS_JSON` in the Vercel project. The last variable is a sensitive server-only Google service-account credential for the target Firebase project. Keep it out of Git and grant only the required Firebase Auth and Firestore access. Omit emulator variables. `GEMINI_API_KEY` and `GEMINI_MODEL` are optional.

Enable Email/Password sign-in and provision demo users and the default Firestore database in the real Firebase project. Deploy the Firestore rules and seed the sample catalogue before checking student and faculty flows on the live URL.
