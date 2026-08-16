# End-to-End Execution Guide — CI/CD Pipeline Assignment

Follow these steps in order. Everything under `flask_Practice/` in this folder is already
written for you (Dockerfile, `/health` route, tests, GitHub Actions workflow, README) — you
just need to copy it into your fork, provision AWS, wire up secrets, and push.

Time estimate if nothing goes wrong: ~60–90 minutes, mostly AWS console/CLI waiting.

---

## 0. Prerequisites checklist

- [ ] A GitHub account (to fork the repo)
- [ ] An AWS account with console + CLI access (IAM permissions for EC2, ECR, IAM role creation)
- [ ] AWS CLI v2 installed locally and run `aws configure` with a user that has admin-ish access
- [ ] A Gmail account you can enable an **App Password** on (Google Account → Security →
      2-Step Verification must be ON, then App Passwords)
- [ ] Git installed locally

---

## 1. Fork and clone

1. Open https://github.com/mohanDevOps-arch/flask_Practice and click **Fork** (top right).
2. Clone your fork:
   ```bash
   git clone https://github.com/<your-username>/flask_Practice.git
   cd flask_Practice
   ```

## 2. Copy the prepared files into your clone

Copy everything from this folder's `flask_Practice/` subfolder into your cloned repo,
**overwriting** `app.py` and `test_app.py` and adding the new files:

```bash
# Run from "Assignments 05" folder, adjust <your-clone-path> to where you cloned in step 1
cp -r flask_Practice/. <your-clone-path>/
```

On Windows PowerShell:
```powershell
Copy-Item -Recurse -Force "flask_Practice\*" "<your-clone-path>\"
```

Verify the diff is minimal and intentional:
```bash
cd <your-clone-path>
git status
git diff app.py test_app.py
```

You should see: `app.py` gained only the `/health` route; `test_app.py` gained only
`test_health_check_success`; new files `Dockerfile`, `.dockerignore`, `.env.example`,
`.github/workflows/ci-cd.yml`; `README.md` replaced with the fuller version.

## 3. Set up a production MongoDB (MongoDB Atlas free tier)

The EC2 container needs a real, reachable MongoDB — don't try to run Mongo on the same
instance, it adds needless complexity for this assignment.

1. Go to https://www.mongodb.com/cloud/atlas/register and create a free account.
2. Create a free **M0** cluster (any region close to your chosen AWS region).
3. **Database Access** → add a database user with a username/password (save these).
4. **Network Access** → add IP `0.0.0.0/0` (allow from anywhere) — simplest for this
   assignment since the EC2 instance's IP isn't static yet. Note this trade-off in your
   submission if asked.
5. **Connect** → "Drivers" → copy the connection string, it looks like:
   ```
   mongodb+srv://<user>:<password>@<cluster>.mongodb.net/student_db?retryWrites=true&w=majority
   ```
   This is your `MONGO_URI` — save it, you'll need it for a GitHub Secret in step 6.

## 4. AWS setup

Set some shell variables you'll reuse (adjust region/names as you like):

```bash
export AWS_REGION=ap-south-1
export ECR_REPO_NAME=flask-practice
export KEY_NAME=flask-practice-key
export SG_NAME=flask-practice-sg
```

### 4.1 Create the ECR repository

```bash
aws ecr create-repository --repository-name $ECR_REPO_NAME --region $AWS_REGION
```

Note the `repositoryUri` in the output — you'll derive the registry from it (everything
before the repo name), e.g. `123456789012.dkr.ecr.ap-south-1.amazonaws.com`.

### 4.2 Create an IAM role for the EC2 instance (ECR pull permission)

```bash
aws iam create-role --role-name flask-practice-ec2-role \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]
  }'

aws iam attach-role-policy --role-name flask-practice-ec2-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly

aws iam create-instance-profile --instance-profile-name flask-practice-ec2-profile

aws iam add-role-to-instance-profile \
  --instance-profile-name flask-practice-ec2-profile \
  --role-name flask-practice-ec2-role
```

### 4.3 Create a key pair (for the pipeline's SSH deploy step)

```bash
aws ec2 create-key-pair --key-name $KEY_NAME --query "KeyMaterial" --output text > $KEY_NAME.pem
chmod 400 $KEY_NAME.pem   # skip on Windows; keep the file safe, do NOT commit it
```

Keep `$KEY_NAME.pem` — its contents go into the `EC2_SSH_KEY` GitHub Secret in step 6.

### 4.4 Create a security group

```bash
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text)

SG_ID=$(aws ec2 create-security-group --group-name $SG_NAME \
  --description "flask-practice CI/CD assignment" --vpc-id $VPC_ID \
  --query "GroupId" --output text)

MY_IP=$(curl -s https://checkip.amazonaws.com)

# SSH only from your own IP
aws ec2 authorize-security-group-ingress --group-id $SG_ID \
  --protocol tcp --port 22 --cidr ${MY_IP}/32

# App port open so the pipeline's health check and you can reach it
aws ec2 authorize-security-group-ingress --group-id $SG_ID \
  --protocol tcp --port 5000 --cidr 0.0.0.0/0
```

> Document this choice in your submission notes: port 22 is locked to your IP; port 5000 is
> open to the world because this is a teaching deployment with no load balancer in front of it.

### 4.5 Launch the EC2 instance (Ubuntu 22.04, with Docker + AWS CLI via user-data)

```bash
cat > user-data.sh << 'EOF'
#!/bin/bash
apt-get update -y
apt-get install -y docker.io unzip curl
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip -q awscliv2.zip
./aws/install
EOF

AMI_ID=$(aws ec2 describe-images --owners 099720109477 \
  --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
            "Name=state,Values=available" \
  --query "sort_by(Images,&CreationDate)[-1].ImageId" --output text)

INSTANCE_ID=$(aws ec2 run-instances \
  --image-id $AMI_ID \
  --instance-type t2.micro \
  --key-name $KEY_NAME \
  --security-group-ids $SG_ID \
  --iam-instance-profile Name=flask-practice-ec2-profile \
  --user-data file://user-data.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=flask-practice}]' \
  --query "Instances[0].InstanceId" --output text)

echo "Instance: $INSTANCE_ID — waiting for it to be running..."
aws ec2 wait instance-running --instance-ids $INSTANCE_ID

EC2_HOST=$(aws ec2 describe-instances --instance-ids $INSTANCE_ID \
  --query "Reservations[0].Instances[0].PublicIpAddress" --output text)
echo "EC2 public IP: $EC2_HOST"
```

Save `$EC2_HOST` — it's your `EC2_HOST` GitHub Secret.

Wait ~2 minutes for user-data to finish installing Docker/AWS CLI, then sanity-check:

```bash
ssh -i $KEY_NAME.pem ubuntu@$EC2_HOST "docker --version && aws --version"
```

If that fails immediately, wait another minute (user-data is still running) and retry.

### 4.6 Create an IAM user for the pipeline's AWS credentials (push to ECR)

The EC2 **instance role** (step 4.2) only lets the box *pull* from ECR. The GitHub Actions
runner needs its own credentials to *push*:

```bash
aws iam create-user --user-name flask-practice-ci

aws iam attach-user-policy --user-name flask-practice-ci \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser

aws iam create-access-key --user-name flask-practice-ci
```

Save the `AccessKeyId` and `SecretAccessKey` from the output — these are your
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` GitHub Secrets.

## 5. Gmail App Password (SMTP)

1. On the Gmail account you want to send from: Google Account → **Security** → enable
   **2-Step Verification** if not already on.
2. Google Account → Security → **App passwords** → create one (name it e.g. "github-actions").
3. Copy the 16-character app password — this is `MAIL_PASSWORD`. `MAIL_USERNAME` is the
   Gmail address itself. `MAIL_TO` can be the same address or a different one you check.

## 6. Add GitHub Secrets

In your fork on GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
Add each of these:

| Secret name | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | from step 4.6 |
| `AWS_SECRET_ACCESS_KEY` | from step 4.6 |
| `AWS_REGION` | e.g. `ap-south-1` |
| `ECR_REPOSITORY` | `flask-practice` (just the name, not the full URI) |
| `EC2_HOST` | the public IP from step 4.5 |
| `EC2_USER` | `ubuntu` |
| `EC2_SSH_KEY` | full contents of `flask-practice-key.pem` from step 4.3, including the `-----BEGIN...` / `-----END...` lines |
| `MONGO_URI` | the Atlas connection string from step 3 |
| `SECRET_KEY` | any random string, e.g. output of `openssl rand -hex 32` |
| `MAIL_USERNAME` | your Gmail address |
| `MAIL_PASSWORD` | the 16-char app password from step 5 |
| `MAIL_TO` | address to receive notifications |

## 7. Commit and push to trigger the pipeline

```bash
git add app.py test_app.py Dockerfile .dockerignore .env.example .github/workflows/ci-cd.yml README.md
git commit -m "Add CI/CD pipeline: Dockerfile, /health check, GitHub Actions workflow"
git push origin main
```

## 8. Watch it run

1. On GitHub, open the **Actions** tab of your fork — you should see the workflow running.
2. Watch each stage go green in order: checkout → install → test → build → push → deploy → verify → notify.
3. Once green, open `http://<EC2_HOST>:5000` in a browser — you should see the Student
   Registration System home page.
4. Check the `MAIL_TO` inbox for the ✅ success email.

If a stage fails, click into it in the Actions log — the most common issues are:
- **Test stage fails immediately**: check the `mongo` service container started (it's
  automatic, no action needed) — more likely a typo in test code, re-check your copy.
- **Deploy stage fails / SSH timeout**: security group port 22 not open to the GitHub
  runner — note GitHub-hosted runners have dynamic IPs, so if you locked port 22 to your own
  IP only, the deploy step SSHing *from GitHub's* IP will fail. Either temporarily open port
  22 to `0.0.0.0/0` for the assignment (document why), or look up GitHub Actions' published
  IP ranges and allow those instead.
- **Verify stage fails**: SSH into the box and run `docker logs student-app` to see why the
  container isn't answering `/health` (usually a bad `MONGO_URI`).

> **Fix for the SSH IP issue above (do this now if you locked SSH to your own IP in step 4.4):**
> ```bash
> aws ec2 authorize-security-group-ingress --group-id $SG_ID --protocol tcp --port 22 --cidr 0.0.0.0/0
> ```
> Document this trade-off in your README/submission notes as instructed by the assignment.

## 9. Test the failure path (required deliverable)

1. Locally, break a test intentionally, e.g. in `test_app.py` change an assertion:
   ```python
   def test_home_page(client):
       response = client.get('/')
       assert response.status_code == 200
       assert b"This text does not exist" in response.data  # intentional failure
   ```
2. Commit and push:
   ```bash
   git add test_app.py
   git commit -m "Intentional failing test to verify pipeline failure path"
   git push origin main
   ```
3. Confirm in the Actions tab that the pipeline **stops at the Test stage** and does not
   proceed to Build/Push/Deploy.
4. Confirm you receive the ❌ failure email, and that it correctly says
   `Failed stage: Test (pytest)`.
5. **Revert the intentional failure** and push again so the repo ends on a green run:
   ```bash
   git revert HEAD
   git push origin main
   ```
   Confirm this run goes fully green again with a fresh success email.

## 10. Screenshots to capture (per submission checklist)

- [ ] Full pipeline run, all stages green (Actions tab, expanded)
- [ ] The success email
- [ ] The intentionally failed run (Actions tab showing it stopped at Test)
- [ ] The failure email showing the correct failed stage
- [ ] Optional but good: browser screenshot of the app running at `http://<EC2_HOST>:5000`

## 11. Final README check

Open `README.md` in your fork and make sure it still accurately reflects your setup (region,
any deviations you made). It already documents: prerequisites, secrets, why SSH-based
deploy was chosen, and how to redeploy manually — per the assignment's Section 7.

## 12. Submit

1. Make sure everything is pushed to your fork's `main` branch.
2. Create a text/Word/PDF file containing your GitHub repo link
   (`https://github.com/<your-username>/flask_Practice`).
3. Submit that file through Vlearn.

---

## Quick troubleshooting reference

| Symptom | Likely cause |
|---|---|
| `docker: permission denied` on EC2 | user-data hasn't finished / `usermod -aG docker ubuntu` needs a fresh SSH session — reconnect |
| ECR push `no basic auth credentials` | `AWS_ACCESS_KEY_ID`/`SECRET` secret wrong or IAM user missing ECR policy |
| Deploy step SSH `Permission denied (publickey)` | `EC2_SSH_KEY` secret doesn't include the full PEM including header/footer lines, or wrong `EC2_USER` |
| `/health` returns 503 | `MONGO_URI` secret wrong, or Atlas Network Access doesn't allow the EC2 IP (use `0.0.0.0/0` for this assignment) |
| Email step fails | Gmail blocked "less secure app" — you must use an **App Password**, not your normal Gmail password, and 2-Step Verification must be enabled first |

## Cleanup (after grading, optional)

```bash
aws ec2 terminate-instances --instance-ids $INSTANCE_ID
aws ec2 delete-security-group --group-id $SG_ID
aws ec2 delete-key-pair --key-name $KEY_NAME
aws ecr delete-repository --repository-name $ECR_REPO_NAME --force
aws iam remove-role-from-instance-profile --instance-profile-name flask-practice-ec2-profile --role-name flask-practice-ec2-role
aws iam delete-instance-profile --instance-profile-name flask-practice-ec2-profile
aws iam detach-role-policy --role-name flask-practice-ec2-role --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
aws iam delete-role --role-name flask-practice-ec2-role
aws iam detach-user-policy --user-name flask-practice-ci --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser
# delete the access key first (aws iam list-access-keys --user-name flask-practice-ci), then:
aws iam delete-user --user-name flask-practice-ci
```
This avoids ongoing AWS charges once your submission is graded.
