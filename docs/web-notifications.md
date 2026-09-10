# Web production notifications

The production API runs one Uvicorn worker. `production_monitor` advances active
orchestrator jobs every five seconds even when no browser is open. HTTP polling
and background polling share one lock. Failed-video refunds use the job's owner,
not the HTTP request's current user.

`notification_outbox` persists events before Telegram delivery. A unique event
key deduplicates repeated job polling and payment-ledger reconciliation. Failed
delivery keeps the row pending and retries after 30 seconds, with backoff capped
at 15 minutes. This is at-least-once delivery: a Telegram timeout or crash after
send but before acknowledgement can result in a duplicate. No bot token is stored
in the queue. Monitor and delivery tasks are separate so Telegram outages cannot
stop batch generation.

Routes:

- `TELEGRAM_BOT_TOKEN`: the website verification bot. Login confirmation, the
  first five ready-video messages, and the final batch summary go to the owner's
  Telegram chat. Video messages link to `/app/projects/{projectId}`. Partial
  failures include the completed/total count and the same project link.
- `WEB_MANAGER_BOT_TOKEN` + `WEB_MANAGER_CHAT_ID`: the existing manager bot and
  chat. Web payment Init/link events and web generation failures use this route.
  These settings are required by the production deployment contract. They are
  explicitly configured from the public bot's existing `TG_BOT_TOKEN` and
  `MANAGER_CHAT_ID`; there is no automatic routing fallback.

Payment-link events are reconciled from the shared payment ledger. A saved URL
proves Init succeeded even if its status has already advanced. INIT_FAILED and
INIT_UNKNOWN are separate events. Existing web orders whose link creation was
never notified are also picked up once. Payment settlement/finance notifications
are still owned by the public bot's T-Bank webhook and polling code; their legacy
direct-send failure handling has not been converted to this web outbox.

On deploy, active batches resume monitoring. Previously terminal historical
batches do not send a burst of old user notifications. New login confirmations
are queued per login token, with only its hash retained as the event identifier.

Operator checks (application database):

```sql
SELECT event_key, attempts, delivered_at, last_error
FROM notification_outbox
WHERE delivered_at IS NULL
ORDER BY next_attempt;
```

Look for `production_monitor`, `notification_pending`, and `telegram_auth`
messages in `blast-web-api` logs. A missing manager route remains pending until
configuration is repaired; a failed send is never recorded as delivered.
