# Project Journal — CI/CD Pipeline for the Student Registration System

**Author:** Santosh · **Course:** HeroVired Gen AI / Data Science — DevOps: CI/CD Pipeline (Graded Assignment)
**Repo:** `flask_Practice_repo` (this repo) · **Live app (at time of writing):** http://18.205.113.157:5000

> This is the narrative version of the project — what I built, in what order, and *why* each decision was made.
> For a quick tech-stack recap, see [`02_TECH_STACK_AND_MINDMAP.md`](./02_TECH_STACK_AND_MINDMAP.md).
> For the "what went wrong and how I fixed it" table, see [`03_LESSONS_LEARNED.md`](./03_LESSONS_LEARNED.md).
> For the actual submission write-up, see the `Submission_Package` folder shared alongside this repo.

---

## 1. The assignment, in one paragraph

HeroVired's brief asked for a CI/CD pipeline — Jenkins **or** GitHub Actions — that takes a source-controlled application, runs automated tests, builds a Docker image, pushes it to a container registry, deploys it to a cloud VM, and notifies a human by email whether the deployment succeeded or failed. The graded deliverable is the pipeline config + a repo + evidence screenshots + a short report, not the application itself — the app (a Flask + MongoDB "Student Registration System") is just the payload that proves the pipeline works end-to-end.

## 2. Why GitHub Actions over Jenkins

I chose **GitHub Actions**, not Jenkins, for three reasons specific to this assignment:

1. **No infrastructure to babysit.** Jenkins needs a server (or container) that I own, patch, and keep running — that's a whole separate ops problem layered on top of the actual CI/CD problem the assignment is testing. GitHub Actions runners are managed by GitHub; I only write YAML.
2. **The repo already lives on GitHub.** Actions triggers (`on: push`) attach directly to the repo with zero extra wiring — no webhook to configure, no separate service to point at the repo.
3. **Secrets management is built in.** GitHub's *Settings → Secrets and variables → Actions* gives me encrypted, per-repo secret storage for AWS keys, the EC2 SSH key, MongoDB URI, and email credentials — exactly what the pipeline needs, with no extra vault to stand up.

Jenkins is still the more common tool in traditional enterprise shops (and worth knowing), but for a single-repo, single-app assignment, GitHub Actions was the pragmatic choice.

## 3. The application layer

The starter app is a small Flask CRUD app ("add / update / delete / list student") backed by MongoDB, rendered with Jinja2 templates and Bootstrap 5. I made two application-level changes beyond what was scaffolded, both load-bearing for the pipeline:

### 3.1 Added a `/health` endpoint
```python
@app.route('/health')
def health():
    try:
        mongo.cx.admin.command('ping')
        return {"status": "ok", "mongo": "connected"}, 200
    except PyMongoError as e:
        return {"status": "error", "mongo": str(e)}, 503
```
**Why:** the pipeline's "deploy" stage isn't actually done just because SSH-and-restart succeeded — the container could be up but crash-looping, or up but unable to reach MongoDB. `/health` gives the pipeline something concrete to poll after deploy: a 200 with `"mongo": "connected"` means the app *and* its database dependency are both actually working, not just that a process is listening on port 5000.

### 3.2 Made MongoDB TLS conditional on the URI scheme
```python
_mongo_kwargs = {}
if (app.config["MONGO_URI"] or "").startswith("mongodb+srv://"):
    _mongo_kwargs["tlsCAFile"] = certifi.where()
mongo = PyMongo(app, **_mongo_kwargs)
```
**Why:** MongoDB Atlas (`mongodb+srv://`) requires TLS, and different OSes resolve system CA bundles differently, so forcing `certifi`'s bundle avoids "SSL handshake failed" surprises. But the pipeline's **Test** stage runs against a throwaway `mongo:6` Docker service on `mongodb://` (no TLS at all) — forcing `tlsCAFile` unconditionally onto a non-TLS server makes the driver refuse to connect. Scoping the TLS kwarg to only `mongodb+srv://` URIs lets the exact same `app.py` work against both the ephemeral CI database and the real Atlas cluster in production, with zero test-only code branches.

## 4. Containerization

A single-stage `Dockerfile` on `python:3.11-slim`:
- Copies `requirements.txt` first, installs dependencies, *then* copies the rest of the source. This ordering matters for Docker layer caching — dependency installs are the slow step, and they only get re-run when `requirements.txt` itself changes, not on every code edit.
- Exposes port 5000 and runs the Flask app.

`.dockerignore` keeps the build context lean: git metadata, virtualenvs, `__pycache__`, docs, and (as of this restructuring) the new `docs/` and `PRIVATE_do_not_share/` folders — neither should ever end up baked into a container image, both because it bloats the image and because `PRIVATE_do_not_share/` contains live secrets.

## 5. Image registry — Amazon ECR

Images are tagged with the **git commit SHA**, never `latest`. This was a deliberate choice: `latest` is mutable and gives you no way to know *which* code is actually running in a given container without separately checking logs or deploy history. Tagging by SHA means the image tag itself *is* the audit trail — `docker inspect` or even just looking at the running container's tag tells you exactly which commit is live.

The pipeline authenticates to ECR using `aws-actions/amazon-ecr-login`, which exchanges the IAM user's access key/secret (stored as GitHub secrets) for a short-lived ECR auth token — the long-lived credentials never touch `docker login` directly.

## 6. Compute — Amazon EC2

- **Instance:** Ubuntu 22.04, `t2.micro` (free-tier eligible).
- **IAM instance role** (not a user) attached to the EC2 instance, scoped to `AmazonEC2ContainerRegistryReadOnly` — the instance can *pull* from ECR without any access keys stored on the box itself. This is the standard "no long-lived credentials on a server" pattern.
- **Security group:** inbound 22 (SSH, restricted to my IP) and 5000 (app, open) — deliberately *not* 443/80, since this assignment doesn't require a reverse proxy or TLS termination in front of the app.
- **Deploy mechanism:** `appleboy/ssh-action` — the pipeline SSHes into the box using a private key stored as a GitHub secret, runs `docker pull` + `docker stop/rm` + `docker run` against the freshly-pushed ECR image tag.

Because this AWS account sits under an **AWS Organizations / Control Tower** setup (not a bare personal account), networking wasn't the default flat VPC I expected from tutorials — I had to work within the account's actual VPC/subnet layout and region restrictions rather than assuming a fresh default VPC existed. See the Lessons Learned doc for the specific issue this caused.

## 7. Database — MongoDB Atlas

Used the free **M0** tier rather than running MongoDB inside a second container on the EC2 box. Reasoning: a self-hosted MongoDB container on the same `t2.micro` competes with the app for the instance's very limited RAM, and doesn't demonstrate anything extra about CI/CD — the assignment is about the *pipeline*, not about database administration. Atlas also matches how a real small-team deployment would actually be built.

Setup involved:
- Creating a **Database Access** user (username/password, *not* my Atlas account login — a scoped app-only credential).
- **Network Access** IP allowlisting so the EC2 instance's public IP (and, during local testing, my own IP) can actually reach the cluster — Atlas blocks all connections by default.
- Building the `mongodb+srv://` connection string and storing it as a GitHub secret / EC2 environment variable — never committed to source.

## 8. The pipeline itself — 8 stages

Defined in [`.github/workflows/ci-cd.yml`](../../.github/workflows/ci-cd.yml), triggered on push to `main`:

| # | Stage | What it does | Why it exists |
|---|-------|---------------|----------------|
| 1 | **Checkout** | `actions/checkout` pulls the triggering commit | Baseline — everything else operates on this code |
| 2 | **Install** | `pip install -r requirements.txt` | Dependencies needed before tests can even import the app |
| 3 | **Test** | Runs the test suite against a `mongo:6` service container | Catches breakage *before* anything gets built or shipped — the whole point of "CI" |
| 4 | **AWS creds** | `aws-actions/configure-aws-credentials` | Authenticates the runner to AWS for the next two stages |
| 5 | **Build & Push** | `docker build` tagged by commit SHA, then push to ECR | Produces the exact artifact that will run in production |
| 6 | **Deploy** | SSH into EC2, pull the new image, replace the running container | Ships the artifact |
| 7 | **Verify** | Curl-retry loop against `/health` on the EC2 public IP | Confirms the deploy actually worked, not just that the SSH commands ran without error |
| 8 | **Notify** | `dawidd6/action-send-mail` — one step gated `if: success()`, one gated `if: failure()` | Closes the loop: a human finds out the outcome without watching the Actions tab |

A `determine_failure` step walks `steps.<id>.outcome` for each named step, in order, to find the *first* stage that didn't succeed — so the failure email says something like "Test stage failed" instead of a generic "pipeline failed," which matters a lot when triaging a broken run at a glance.

## 9. Notifications

Used Gmail SMTP with an **App Password** (not my real Gmail password) — Gmail requires 2-Step Verification to be enabled before it will issue app passwords, and app passwords can be revoked individually without touching the main account credential. Two conditional steps in the workflow send different emails depending on outcome:
- **Success:** commit SHA, branch, image tag, EC2 target, link to the run.
- **Failure:** the same details plus the specific failed-stage name resolved by `determine_failure`.

## 10. Documentation & repo hygiene (this restructuring)

After the pipeline was confirmed working end-to-end, the last piece of the assignment was making the repo itself a good artifact: readable by a grader, safe to make public, and useful to *future me* revisiting this project. That meant:
- Moving HeroVired's original assignment PDFs, this Learning Journal, and a curated set of **safe** screenshots into `docs/` — tracked and pushed.
- Moving every screenshot and file that showed real AWS access keys, the `.pem` private key, EC2 IP/account details in a sensitive context, or raw CI failure logs into `PRIVATE_do_not_share/` — added to `.gitignore` so it's physically present on disk (as my own reference) but never tracked or pushed.
- Removing two stale leftovers from the original repo template (`azure-pipelines.yml` — a competing pipeline definition never used, and `README.pdf` — a duplicate of `README.md`) since a grader should see exactly one pipeline definition and one canonical README.
- Verifying, three separate ways, that `.gitignore` actually excludes `PRIVATE_do_not_share/` before ever running `git add` — see [`03_LESSONS_LEARNED.md`](./03_LESSONS_LEARNED.md) for why that verification mattered here specifically (a live AWS secret sits inside that folder).

## 11. Where things stand

- The pipeline has had at least one fully green end-to-end run: test → build → push → deploy → verify → success email.
- The app is live and reachable at `http://18.205.113.157:5000` (note: this is a `t2.micro` demo instance, not intended to stay up indefinitely — if you're future-me reading this months later, the instance may have been stopped or terminated to avoid ongoing AWS cost; check the AWS console before assuming it's still live).
- One follow-up action **not yet done** as of this writing: the AWS access key visible in one of the private screenshots (`Screenshot 2026-08-16 180948.png`) should be deactivated/rotated in IAM, since it was captured on screen even though the folder itself is gitignored. Treat any credential that has ever appeared in a screenshot as compromised, regardless of where the screenshot ends up living.
