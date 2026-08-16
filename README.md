# Student Registration System — with CI/CD Pipeline

A simple Flask web application to manage student records with MongoDB as the backend
database. Users can add, view, update, and delete student details. This fork adds a full
CI/CD pipeline that tests, containerizes, and deploys the app to an EC2 instance on every
push to `main`.

## Features

- List all students on the home page
- Add a new student
- Update existing student details
- Delete a student with confirmation
- `/health` endpoint that verifies live MongoDB connectivity (used as the deploy-verification gate)

## Tech Stack

- **Backend:** Python, Flask
- **Database:** MongoDB (via Flask-PyMongo)
- **Frontend:** HTML, Jinja2 templates, Bootstrap 5
- **Containerization:** Docker
- **Registry:** Amazon ECR
- **Compute:** Amazon EC2
- **CI/CD:** GitHub Actions

## Local Setup

```bash
git clone <your-repo-url>
cd flask_Practice
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
cp .env.example .env         # then fill in MONGO_URI and SECRET_KEY
python app.py
```

Visit `http://localhost:5000`. Health check: `http://localhost:5000/health`.

Run tests locally (requires a local MongoDB on `localhost:27017`):

```bash
pytest -v
```

## Docker

```bash
docker build -t flask-practice:local .
docker run -d -p 5000:5000 --env-file .env --name flask-practice flask-practice:local
```

## CI/CD Pipeline

Defined in [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml). Triggers on every
push to `main` and runs, in order:

1. **Checkout** — pulls the latest commit
2. **Install** — `pip install -r requirements.txt`
3. **Test** — runs `pytest` against a MongoDB service container; the pipeline stops here if
   any test fails
4. **Build** — builds a Docker image tagged with the short Git commit SHA (never `latest`)
5. **Push to ECR** — authenticates to Amazon ECR and pushes the tagged image
6. **Deploy to EC2** — SSHes into the EC2 instance, pulls the new image, stops/removes the
   currently running container, and starts the new one with `--restart unless-stopped`
7. **Verify** — polls `GET /health` on the EC2 instance; a container that starts but can't
   reach MongoDB (or doesn't come up at all) fails the pipeline here
8. **Notify** — emails a customized success or failure report (commit SHA, branch, image
   tag, target host, run link, and — on failure — which stage broke)

### Required GitHub Secrets

Configure these under **Settings → Secrets and variables → Actions → Repository secrets**:

| Secret | Purpose |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM user credentials used by the pipeline to push to ECR |
| `AWS_SECRET_ACCESS_KEY` | IAM user credentials used by the pipeline to push to ECR |
| `AWS_REGION` | e.g. `ap-south-1` |
| `ECR_REPOSITORY` | Name of the ECR repository (not the full URI) |
| `EC2_HOST` | Public IP or DNS of the EC2 instance |
| `EC2_USER` | SSH login user (`ubuntu` for Ubuntu AMIs, `ec2-user` for Amazon Linux) |
| `EC2_SSH_KEY` | Private half of the EC2 key pair (PEM contents), used only by the pipeline |
| `MONGO_URI` | Production MongoDB connection string, injected into the container at runtime |
| `SECRET_KEY` | Flask session secret, injected into the container at runtime |
| `MAIL_USERNAME` | SMTP sender address (Gmail) |
| `MAIL_PASSWORD` | Gmail **App Password** (not the account password) |
| `MAIL_TO` | Address that receives success/failure notifications |

No secret is ever written into the repository or the pipeline file — the Flask app reads
`MONGO_URI`/`SECRET_KEY` from the environment at container start, and the pipeline reads
everything else from GitHub Secrets.

### How the deploy step connects to EC2

We use an **SSH-based deploy**: the GitHub Actions runner SSHes into the EC2 instance with a
dedicated key pair stored in `EC2_SSH_KEY` and runs the `docker pull` / `stop` / `rm` / `run`
sequence directly on the box. This was chosen over AWS SSM because it needs no extra IAM
permissions beyond ECR pull, no SSM agent/setup, and is the simplest reliable option for a
single-instance deployment target. The EC2 instance's IAM **instance role** (not static
keys) is used for `aws ecr get-login-password`, so no AWS credentials live on the box itself.

### AWS prerequisites (set up manually, once)

- An **ECR repository** to hold built images.
- An **EC2 instance** (Ubuntu 22.04) with:
  - Docker and the AWS CLI installed
  - An **IAM instance role** granting `AmazonEC2ContainerRegistryReadOnly` (so it can pull
    from ECR without static credentials)
  - A **security group** allowing inbound TCP 22 (SSH, restricted to the admin's IP) and
    TCP 5000 (the app port, open to `0.0.0.0/0` so the health check and app are reachable —
    acceptable for this teaching deployment; a production setup would front this with a
    load balancer/WAF instead of exposing the instance directly)

Full step-by-step provisioning commands are in [`SETUP_GUIDE.md`](../SETUP_GUIDE.md).

### Reproducing a deployment manually (if the pipeline were unavailable)

```bash
# From a machine with AWS CLI + docker configured for the target account:
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker build -t <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<sha> .
docker push <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<sha>

# On the EC2 instance:
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker pull <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<sha>
docker stop student-app || true
docker rm student-app || true
docker run -d --name student-app --restart unless-stopped -p 5000:5000 \
  -e MONGO_URI="<mongo-uri>" -e SECRET_KEY="<secret-key>" \
  <account>.dkr.ecr.<region>.amazonaws.com/<repo>:<sha>
curl http://localhost:5000/health
```

## Project Structure

```
flask_Practice/
├── app.py
├── requirements.txt
├── test_app.py
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── add_student.html
│   └── update_student.html
├── Dockerfile
├── .dockerignore
├── .env.example
├── .github/workflows/ci-cd.yml
└── README.md
```

## Notes

- Every deployed image is tagged with its Git commit SHA — never `latest` — so any running
  container can be traced back to an exact commit.
- The `/health` endpoint genuinely checks MongoDB connectivity via `mongo.cx.admin.command('ping')`,
  so a container that starts but can't reach the database is correctly reported as unhealthy.
