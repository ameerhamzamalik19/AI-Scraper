# Real-time progress bar audit

## Executive summary

The progress bar is not monotonic because the frontend replaces the displayed value with the value from whichever WebSocket event arrives next. Several backend producers use different progress scales, and crawl progress uses a changing denominator. Therefore a backward movement is expected with the current implementation; it is not just a CSS animation issue.

There is also a separate delivery problem: the active WebSocket manager is process-local and does not listen to Redis, while workers publish their crawl events through Redis. Depending on how the API and workers are running, some events will not reach the browser at all.

## Current data flow

1. The URL request initializes the chat at `0`, then emits `5%` from `backend/api/routes/process.py`.
2. The crawler worker emits `10%` before crawling, `20%` after crawling, and `30%` before processing from `backend/workers/crawler_worker.py`.
3. Each crawler URL record emits `crawl_progress`. The frontend calculates:

   ```js
   Math.round((data.completed / data.total) * 100)
   ```

   and caps it at `95` in `frontend/ai-scraper/src/App.jsx`.

4. The processor emits `25%`, `40%`, `50%`, and `60%` from `backend/workers/processor_worker.py`.
5. The chunker emits `60%`, `65%`, `70%`, and `80%` from `backend/workers/chunker_worker.py`.
6. The embedder emits `80%`, `82%` through `96%`, then `100%` from `backend/workers/embedder_worker.py`.
7. The frontend sets `100%` on a `complete` event, although the inspected worker path normally reaches completion through a `progress_update` with status `completed`.

The frontend has two competing handlers:

- `onStatusUpdate` replaces the entire `processingStatus`, including `progress`.
- `onCrawlProgress` also changes `processingStatus.progress`, independently of the database status.

There is no client-side monotonic guard, event sequence number, stage model, or source/version check.

## Reproducible backward movements

### Crawl denominator changes

The crawler calculates its percentage from records discovered so far, not from a fixed total. A possible sequence is:

```text
processing 1/1 -> 95% (capped from 100)
discover another URL
crawl progress 1/2 -> 50%
```

The bar visibly moves from `95%` to `50%` even though one page has not become uncrawled.

### Pipeline stage changes

After the crawler emits `30%`, the processor starts by emitting `25%`. In the chunker, the `existing_chunks_count` branch emits `80%`, but the regular chunking path can emit `65%` after its initial `60%`. These are explicit decreases in the backend's progress writes.

### Event ordering and stale events

The client applies updates immediately. If a queued Redis/status event arrives after a newer stage event, the older value wins visually. Status updates also replace fields with defaults (`data.progress || 0`, `data.status || 'idle'`, etc.), so a partial or stale payload can reset progress and status fields.

## Confirmed issues and impact

### High: progress is not defined on one consistent scale

The fixed values represent pipeline stages, while crawl progress represents URL completion. They cannot be merged safely by direct assignment. This is the direct cause of backward movement such as `30 -> 25` and `95 -> 50`.

### High: crawl percentage has no stable denominator

`total` is `len(self.crawled_url_records)`, so it grows as new links are discovered. A percentage based on that value is not a valid overall progress measure. The `95%` cap hides the symptom temporarily but does not fix the calculation.

### High: worker WebSocket events are not reliably delivered cross-process

Workers use `redis_pubsub.WebSocketPubSub.publish()`. The active route imports the root `backend/websocket_manager.py`; its `broadcast()` sends to local sockets and publishes through its own async Redis client, but its active manager does not start a Redis listener for worker messages. The Redis-aware duplicate manager in `backend/utils/websocket_manager.py` is not imported by the active route. As a result, a separate Dramatiq worker process can publish an event that no API process forwards to connected browsers.

### Medium: duplicate WebSocket implementations increase protocol risk

There are two `ChatConnectionManager` implementations and two historical protocol paths. The active route uses the root manager, while the crawler/status code and unused manager do not share one clearly enforced delivery abstraction. This makes fixes easy to apply to the wrong implementation.

### Medium: completion display is inconsistent

The progress bar is rendered only while `processingStatus.is_processing` is true and only when progress is between `0` and `100`. On completion, the component hides the progress track rather than showing a completed `100%` state. On failure, `mark_failed()` deliberately writes `progress=0`, so the UI loses the last known progress and the bar disappears.

### Medium: reconnects do not recover missed progress

On reconnect, the client fetches crawled URLs but does not request or apply the current status. The server sends an initial status only when the socket joins, so this is dependent on the timing of reconnect and the status lookup. Missed Redis events are not replayed because Redis Pub/Sub is not a durable event log.

### Low: a stale socket can still update the current chat

When changing chats, the old socket is disconnected and a new one is created, but callbacks are not tagged with their chat ID before calling React state setters. A late message from the old socket could overwrite the new chat's progress or crawled URL list.

### Low: status payloads are treated as complete snapshots

`onStatusUpdate` fills absent values with defaults and replaces the previous object. If a backend event contains only a subset of fields, it can clear valid state. A patch-style update or merge with explicit field presence would be safer.

## Recommended correction

Use one authoritative progress state in the backend and one event schema. The simplest robust design is:

1. Define ordered pipeline stages with fixed ranges, for example crawl `5-20`, process `20-60`, chunk `60-80`, embed `80-100`.
2. Keep crawl URL counts as separate metadata (`completed`, `total`, `failed`) instead of converting them directly into the overall percentage. If a crawl total can grow, show counts or an indeterminate crawl indicator until discovery is complete.
3. Make the backend persist and broadcast a single status snapshot containing `progress`, `status`, `current_step`, and a monotonically increasing `sequence` or timestamp.
4. In the frontend, ignore an event with an older sequence and clamp progress to the current run's last value as a defensive fallback. Do not use `data.progress || 0`; distinguish a missing value from an explicit zero.
5. Make the active WebSocket manager the only manager and give it one reliable Redis subscription path, or have the API poll/read the persisted status after reconnect. Add authentication for the chat WebSocket while consolidating it.
6. Decide whether completed and failed states should retain and display the final progress. A completed state should normally render a stable `100%` bar briefly or as a completed status; a failed state should retain the last progress alongside the error.

## Suggested test cases

- Apply status events `30`, `25`, `40` and verify the displayed overall progress never decreases.
- Apply crawl events `1/1`, then `1/2`, and verify they do not reduce the overall pipeline percentage.
- Verify a stale event with a lower sequence cannot overwrite a newer event.
- Verify reconnect requests the current persisted status and restores the bar.
- Verify worker-process Redis events reach a connected browser.
- Verify switching chats ignores late events from the previous WebSocket.
- Verify completed and failed states have intentional progress-bar behavior.

## Validation note

This is a static audit of the event producers, WebSocket routing, and React handlers. No live browser/WebSocket session or end-to-end worker/Redis run was available during the audit, so the delivery finding should be confirmed in the deployed process topology.