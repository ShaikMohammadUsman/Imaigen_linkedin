# 🚀 Deploying Scooter LinkedIn Bot to Google Cloud Platform (GCP)

Follow these steps to deploy your bot to a **secure, stable Virtual Machine (VM)** on GCP. Using a VM is recommended over serverless (Cloud Run) because browser automation requires meaningful memory and stable sessions.

---

## ✅ Prerequisites

1.  **Google Cloud SDK**: Install `gcloud` CLI 
    *   [Download here](https://cloud.google.com/sdk/docs/install)
2.  **Docker**: Install Docker Desktop (for local testing/building)
    *   [Download here](https://www.docker.com/products/docker-desktop)
3.  **GCP Project**: Have a project created in Google Cloud Console (e.g., `scooter-ai-2025`).

---

## 🛠️ Step 1: Configure & Authenticate

Open your terminal and run:

```bash
# Login to your Google Cloud account
gcloud auth login

# Set your active project (Replace with your ACTUAL Project ID)
gcloud config set project [YOUR_PROJECT_ID]

# Enable necessary APIs (Container Registry & Compute Engine)
gcloud services enable containerregistry.googleapis.com compute.googleapis.com
```

---

## 🏗️ Step 2: Build & Push Docker Image

We will build your code into a Docker container and store it in Google Container Registry (GCR).

```bash
# Build the image and tag it for GCR
# Replace [YOUR_PROJECT_ID] with your project ID
gcloud builds submit --tag gcr.io/[YOUR_PROJECT_ID]/scooter-linked-bot
```
*   *This will take 2-5 minutes as it installs dependencies.*
*   **Result**: Your bot is now packaged as `gcr.io/[YOUR_PROJECT_ID]/scooter-linked-bot`.

---

## 🚀 Step 3: Deploy to VM (Compute Engine)

Now we launch a VM that automatically runs your container.

```bash
# Deploy a new VM running your container
# Machine Type: e2-medium (2 vCPU, 4GB RAM) - Adjust if needed
gcloud compute instances create-with-container scooter-bot-vm \
    --container-image gcr.io/[YOUR_PROJECT_ID]/scooter-linked-bot \
    --machine-type e2-medium \
    --zone us-central1-a \
    --tags http-server,https-server
```

---

## 🌐 Step 4: Open Firewall (Expose Port 8000)

By default, GCP blocks traffic. We need to allow access to port 8000 (FastAPI).

```bash
# Create a firewall rule to allow traffic on port 8000
gcloud compute firewall-rules create allow-scooter-bot \
    --allow tcp:8000 \
    --source-ranges 0.0.0.0/0 \
    --target-tags http-server
```

---

## 🎉 Step 5: Access Your Bot!

1.  **Get the External IP** of your new VM:
    ```bash
    gcloud compute instances list
    ```
    *Look for the `EXTERNAL_IP` column.*

2.  **Open in Browser**:
    *   Go to: `http://[EXTERNAL_IP]:8000`
    *   You should see your Scooter Bot Dashboard live!

---

## 💾 Saving Data (Persistence)

**Important**: If you delete the VM, data inside (SQLite db, logs) will be lost unless you mount a persistent disk.

For production, we recommend:
1.  **Switch to Cloud SQL (PostgreSQL)** for the database.
2.  **Use Google Cloud Storage (GCS)** for screenshots/files.

But for a simple VM deployment, the above steps will get you running in < 10 minutes!
