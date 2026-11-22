# Immich setup

This service expects Immich **Workflows** (Immich v2+/v3 with the built-in webhook action).

## 1. Create an API key

In Immich (as the target user):

1. Open **Account settings → API Keys**
2. Create a key with permission to **read/search assets** and **download originals**
3. Paste it into this app’s portal (or `users.yaml` → `immich_api_key`)

> Keys are per Immich user. The sync user’s id in this app (`alice`, etc.) is only a local slug — it does not have to match the Immich username, but one Immich user ↔ one portal user is the usual mapping.

## 2. Create an AssetCreate workflow

1. Immich → **Utilities → Workflows** (wording may vary by version)
2. Create a workflow with trigger **AssetCreate**
3. Add step **Trigger Webhook** (`immich-plugin-core#webhook`)
4. Configure:

| Field | Example |
|-------|---------|
| URL | `http://192.168.1.10:8088/hooks/alice/asset-create` |
| Method | `POST` |
| Header name | `X-Webhook-Token` |
| Header value | long random string (same as portal) |

**Important:** put only the URL in the URL field. Selecting Method = POST separately is required. Values like `POST http://…` in the URL field will break delivery.

5. Enable the workflow and optionally enable logging while testing

## 3. Verify

1. Ensure this service is healthy: `GET http://<host>:8088/health`
2. Upload a new photo as that Immich user
3. Check Immich workflow logs (if enabled) and this app’s dashboard **Recent events**

If webhooks fail, catch-up still runs on the user’s cron (default every 5 minutes) and will pick up new assets after the watermark.

## Payload note

Immich sends an AssetV1-style body:

```json
{
  "type": "AssetV1",
  "trigger": "AssetCreate",
  "data": { "asset": { "id": "<uuid>", "...": "..." } }
}
```

This project extracts `data.asset.id` (and a few older shapes) automatically.
