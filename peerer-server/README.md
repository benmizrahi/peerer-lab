# Peerer Cloud Run Broker Server

A lightweight, serverless rendezvous and asynchronous message broker for **Google Antigravity Instances** running on **Google Cloud Run**.

---

## Why a Broker Server?

In standard peer-to-peer setups, each Antigravity node must run an open listener on a local port. However, developer machines, laptops, Cloud Workstations, and CI runners are usually behind **NATs, firewalls, or dynamic IP addresses** where direct inbound HTTP requests fail.

The **Peerer Broker Server** solves this by acting as a central Cloud Run relay:
- **Outbound-Only Connections**: All peers connect *outbound* over secure HTTPS.
- **Rendezvous & Presence**: Peers register their alias, capabilities, and status (`online` / `offline`).
- **Async Long-Polling Queue**: Messages sent to an offline or NATed peer are buffered in the broker. The target peer receives them with sub-second latency via HTTP long-polling (`GET /api/v1/queue/poll`).
- **Delivery Receipts**: Message lifecycle is tracked (`queued` -> `delivered` -> `acked`).
- **Zero Inbound Port Forwarding Required**: Works behind corporate VPNs, hotel Wi-Fi, and firewalls.

---

## Architecture Overview

```text
                           Google Cloud Run
                 +───────────────────────────────────+
                 |       Peerer Broker Server        |
                 |                                   |
                 |  - Bearer Token Authentication    |
                 |  - Peer Presence & Heartbeats     |
                 |  - Long-Polling Event Queues      |
                 |  - In-Memory or Firestore Backend |
                 +─────────────────┬─────────────────+
                                   │
              HTTPS Outbound Only  │ (No inbound ports needed on peers)
         ┌─────────────────────────┴─────────────────────────┐
         │                                                   │
         ▼                                                   ▼
+──────────────────────────+                       +──────────────────────────+
|  Antigravity Instance A  |                       |  Antigravity Instance B  |
|  (Local / NAT / Laptop)  |                       |  (Workstation / NAT)     |
|                          |                       |                          |
|  [Peerer CLI / Sidecar]  |                       |  [Peerer CLI / Sidecar]  |
|  1. POST /api/v1/messages|                       |  1. Polls /queue/poll    |
|     (target: peer-b)     |                       |  2. Receives task        |
|                          |                       |  3. agentapi executes    |
|                          |                       |  4. POST /queue/ack      |
+──────────────────────────+                       +──────────────────────────+
```

---

## Project Structure

```text
peerer-broker/
├── Dockerfile                  # Multi-stage production container for Cloud Run
├── requirements.txt            # FastAPI, Uvicorn, Pydantic, Httpx
├── deploy.sh                   # Automated deployment script for gcloud
├── cloudbuild.yaml             # Google Cloud Build CI/CD definition
├── app/
│   ├── config.py               # Environment configuration & secret loading
│   ├── models.py               # Pydantic schemas and strict regex validation
│   ├── security.py             # Constant-time Bearer token auth & security headers
│   ├── main.py                 # FastAPI application, CORS, lifecycle tasks
│   ├── storage/
│   │   ├── base.py             # StorageBackend abstract interface
│   │   ├── memory.py           # In-memory store with async Event long-polling
│   │   └── firestore.py        # Optional Cloud Firestore persistent backend
│   └── routes/
│       ├── health.py           # Cloud Run liveness/readiness probes (/health)
│       ├── peers.py            # Peer registration, heartbeat, discovery
│       └── messages.py         # Message queuing, long-polling, and ack
└── tests/
    ├── test_health.py          # Uptime and security header tests
    ├── test_auth.py            # Authentication rejection & verification tests
    ├── test_peers.py           # Peer presence, filtering, and delete tests
    └── test_messages.py        # Message queuing, long-polling, and ack tests
```

---

## API Reference

All `/api/v1/*` endpoints require the `Authorization: Bearer <BROKER_AUTH_TOKEN>` header.

### 1. Health & Status
| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | None | Service liveness probe, version, peer count, queue size |
| `GET` | `/` | None | Landing info with stats and documentation links |

### 2. Peer Presence & Discovery
| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/peers/register` | Bearer | Register or update peer presence (`peer_id`, `display_name`, `capabilities`) |
| `POST` | `/api/v1/peers/heartbeat` | Bearer | Keepalive heartbeat (`{"peer_id": "alice"}`) |
| `GET` | `/api/v1/peers` | Bearer | List peers (optional `?online_only=true` filter) |
| `GET` | `/api/v1/peers/{peer_id}`| Bearer | Get specific peer details |
| `DELETE`| `/api/v1/peers/{peer_id}`| Bearer | Gracefully unregister peer on shutdown |

### 3. Messaging & Long-Polling
| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/messages` | Bearer | Submit message/task (`target_peer`, `sender`, `content`, `title`) |
| `GET` | `/api/v1/queue/poll` | Bearer | Long-poll for pending messages (`?peer_id=alice&timeout=20`) |
| `POST` | `/api/v1/queue/ack` | Bearer | Acknowledge processed message IDs (`{"peer_id": "alice", "message_ids": [...]}`) |
| `GET` | `/api/v1/messages/{id}` | Bearer | Check message delivery state (`queued`, `delivered`, `acked`) |

---

## Local Development

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Locally
```bash
export BROKER_AUTH_TOKEN="my-secret-token"
export PORT=8080
python3 -m app.main
```
Or with Uvicorn hot-reload:
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```

Interactive OpenAPI documentation is available at:
`http://127.0.0.1:8080/docs`

### 3. Run Tests
```bash
python3 -m unittest discover tests
```

---

## Deploying to Google Cloud Run

### Option 1: Automated Script (Recommended)
Make sure you are authenticated with `gcloud auth login` and have selected your project:
```bash
export PROJECT_ID="your-gcp-project-id"
export BROKER_AUTH_TOKEN="your-secure-shared-token"
./deploy.sh
```

### Option 2: Manual `gcloud run deploy`
```bash
gcloud run deploy peerer-broker \
    --source . \
    --region us-central1 \
    --platform managed \
    --allow-unauthenticated \
    --min-instances 0 \
    --memory 512Mi \
    --set-env-vars "BROKER_AUTH_TOKEN=your-token,STORAGE_BACKEND=memory"
```

### Option 3: With Cloud Firestore Persistence
To persist presence and messages across Cloud Run scale-to-zero cold starts:
1. Ensure Firestore in Native mode is enabled in your GCP project.
2. In `requirements.txt`, uncomment `google-cloud-firestore`.
3. Deploy with environment variable:
```bash
--set-env-vars "STORAGE_BACKEND=firestore,FIRESTORE_PROJECT_ID=your-gcp-project-id"
```

---

## Connecting Peerer Clients to the Broker

### 1. Peer Registration Example (Python)
```python
import urllib.request, json

BROKER_URL = "https://peerer-broker-xyz.a.run.app"
TOKEN = "your-secure-shared-token"

# Register presence
reg_payload = json.dumps({
    "peer_id": "alice-macbook",
    "display_name": "Alice Dev Box",
    "capabilities": ["code-review", "unit-test"]
}).encode()

req = urllib.request.Request(
    f"{BROKER_URL}/api/v1/peers/register",
    data=reg_payload,
    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
)
with urllib.request.urlopen(req) as resp:
    print("Registered:", json.loads(resp.read()))
```

### 2. Long-Polling for Inbound Tasks (Python)
```python
# Infinite worker loop in background thread
while True:
    try:
        url = f"{BROKER_URL}/api/v1/queue/poll?peer_id=alice-macbook&timeout=25"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            for msg in data.get("messages", []):
                print(f"Task from {msg['sender']}: {msg['title']}")
                # Execute via agentapi ...

                # Acknowledge receipt
                ack_payload = json.dumps({
                    "peer_id": "alice-macbook",
                    "message_ids": [msg["id"]]
                }).encode()
                ack_req = urllib.request.Request(
                    f"{BROKER_URL}/api/v1/queue/ack",
                    data=ack_payload,
                    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
                )
                urllib.request.urlopen(ack_req)
    except Exception as e:
        time.sleep(2)
```

### 3. Sending a Task to a Peer via Broker (cURL)
```bash
curl -X POST https://peerer-broker-xyz.a.run.app/api/v1/messages \
  -H "Authorization: Bearer your-secure-shared-token" \
  -H "Content-Type: application/json" \
  -d '{
    "target_peer": "alice-macbook",
    "sender": "bob-workstation",
    "title": "Review Auth Code",
    "content": "Please review src/auth.py for edge cases."
  }'
```
